/*
 * Process-local loader experiment for the self-owned GTMLoaderProbe.exe only.
 * No remote process access, executable-code writes, or Wine file changes.
 * The runner verifies the exact CrossOver kernelbase file before launching.
 * Install/remove must be called by the probe's single main test thread.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winternl.h>
#include <stdint.h>
#include <string.h>
#include <wchar.h>

/* This exact CrossOver build passes Win32 flags by value. Newer Wine builds
 * use a different ABI; the image/hash gates deliberately exclude them. */
typedef NTSTATUS (NTAPI *ldr_load_fn)(PWSTR, DWORD, PUNICODE_STRING, PHANDLE);
static ldr_load_fn original_load;
static PVOID volatile *loader_slot;
static volatile LONG attempts;
static BOOL installed;
static BOOL protection_saved;
static DWORD original_protection;

static BOOL is_d3d12_name(const UNICODE_STRING *name)
{
    static const WCHAR expected[] = L"d3d12.dll";
    if (!name || !name->Buffer || (name->Length & 1)) return FALSE;
    unsigned length = name->Length / sizeof(WCHAR), start = 0;
    for (unsigned i = 0; i < length; ++i)
        if (name->Buffer[i] == L'\\' || name->Buffer[i] == L'/') start = i + 1;
    if (length - start != sizeof(expected) / sizeof(WCHAR) - 1) return FALSE;
    for (unsigned i = start; i < length; ++i) {
        WCHAR c = name->Buffer[i];
        if (c >= L'A' && c <= L'Z') c += L'a' - L'A';
        if (c != expected[i - start]) return FALSE;
    }
    return TRUE;
}

static NTSTATUS NTAPI compat_load(PWSTR search_path, DWORD flags,
                                 PUNICODE_STRING name, PHANDLE module)
{
    NTSTATUS status = original_load(search_path, flags, name, module);
    DWORD saved_error = GetLastError();
    if (status >= 0 && !(flags & DONT_RESOLVE_DLL_REFERENCES) &&
        is_d3d12_name(name) && !GetModuleHandleW(L"d3d12core.dll")) {
        InterlockedIncrement(&attempts);
        /* A normal System32 load retains one reference, as in the source patch. */
        LoadLibraryExW(L"d3d12core.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    }
    SetLastError(saved_error);
    return status;
}

static BOOL contains_rva(DWORD size, DWORD rva, SIZE_T count)
{
    return rva < size && count <= (SIZE_T)size - rva;
}

static BOOL equals_ascii(const BYTE *base, DWORD size, DWORD rva, const char *text)
{
    SIZE_T length = strlen(text);
    if (!contains_rva(size, rva, length + 1)) return FALSE;
    for (SIZE_T i = 0; i <= length; ++i) {
        unsigned char a = base[rva + i], b = (unsigned char)text[i];
        if (a >= 'A' && a <= 'Z') a += 'a' - 'A';
        if (b >= 'A' && b <= 'Z') b += 'a' - 'A';
        if (a != b) return FALSE;
    }
    return TRUE;
}

static PVOID volatile *find_loader_slot(HMODULE module)
{
    BYTE *base = (BYTE *)module;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew < 0 ||
        dos->e_lfanew > 0x300) return NULL;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE ||
        nt->FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 ||
        nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC ||
        nt->OptionalHeader.SizeOfImage != 0x29d000 ||
        nt->OptionalHeader.CheckSum != 0x29f07f) return NULL;
    DWORD size = nt->OptionalHeader.SizeOfImage;
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!contains_rva(size, dir.VirtualAddress, dir.Size)) return NULL;
    for (DWORD offset = 0; offset + sizeof(IMAGE_IMPORT_DESCRIPTOR) <= dir.Size;
         offset += sizeof(IMAGE_IMPORT_DESCRIPTOR)) {
        IMAGE_IMPORT_DESCRIPTOR *imp = (IMAGE_IMPORT_DESCRIPTOR *)(base + dir.VirtualAddress + offset);
        if (!imp->Name) break;
        if (!equals_ascii(base, size, imp->Name, "ntdll.dll")) continue;
        if (!imp->OriginalFirstThunk || !imp->FirstThunk) return NULL;
        for (DWORD i = 0; i < size / sizeof(IMAGE_THUNK_DATA64); ++i) {
            SIZE_T off = (SIZE_T)i * sizeof(IMAGE_THUNK_DATA64);
            if (off > size || !contains_rva(size, imp->OriginalFirstThunk, off + sizeof(IMAGE_THUNK_DATA64)) ||
                !contains_rva(size, imp->FirstThunk, off + sizeof(IMAGE_THUNK_DATA64))) return NULL;
            IMAGE_THUNK_DATA64 *name = (IMAGE_THUNK_DATA64 *)(base + imp->OriginalFirstThunk + off);
            if (!name->u1.AddressOfData) break;
            if (IMAGE_SNAP_BY_ORDINAL64(name->u1.Ordinal)) continue;
            if (name->u1.AddressOfData > MAXDWORD - 2) return NULL;
            if (!equals_ascii(base, size, (DWORD)name->u1.AddressOfData + 2, "LdrLoadDll")) continue;
            PVOID volatile *slot = (PVOID volatile *)(base + imp->FirstThunk + off);
            if ((uintptr_t)slot % sizeof(PVOID)) return NULL;
            return slot;
        }
    }
    return NULL;
}

static DWORD replace_slot(PVOID expected, PVOID replacement)
{
    DWORD previous, ignored;
    if (!VirtualProtect((PVOID)loader_slot, sizeof(PVOID), PAGE_READWRITE, &previous))
        return GetLastError();
    if (!protection_saved) {
        original_protection = previous;
        protection_saved = TRUE;
    }
    PVOID observed = InterlockedCompareExchangePointer(loader_slot, replacement, expected);
    installed = *loader_slot == (PVOID)compat_load;
    BOOL restored = VirtualProtect((PVOID)loader_slot, sizeof(PVOID), original_protection, &ignored);
    if (!restored && observed == expected && replacement == (PVOID)compat_load) {
        /* The page is still writable: undo an installation that only partly succeeded. */
        InterlockedCompareExchangePointer(loader_slot, expected, replacement);
        installed = *loader_slot == (PVOID)compat_load;
        VirtualProtect((PVOID)loader_slot, sizeof(PVOID), original_protection, &ignored);
    }
    if (observed != expected) return ERROR_BUSY;
    if (!restored) return ERROR_ACCESS_DENIED;
    return ERROR_SUCCESS;
}

__declspec(dllexport) DWORD WINAPI GTMInstall(void)
{
    WCHAR exe[32768], enabled[2];
    if (installed) return ERROR_ALREADY_EXISTS;
    if (GetEnvironmentVariableW(L"GTM_LOADER_PROBE", enabled, 2) != 1 || enabled[0] != L'1')
        return ERROR_ACCESS_DENIED;
    DWORD length = GetModuleFileNameW(NULL, exe, 32768);
    if (!length || length >= 32768) return ERROR_INVALID_NAME;
    const WCHAR *name = wcsrchr(exe, L'\\');
    if (_wcsicmp(name ? name + 1 : exe, L"GTMLoaderProbe.exe")) return ERROR_ACCESS_DENIED;
    HMODULE kernelbase = GetModuleHandleW(L"kernelbase.dll");
    HMODULE ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!kernelbase || !ntdll) return ERROR_MOD_NOT_FOUND;
    loader_slot = find_loader_slot(kernelbase);
    original_load = (ldr_load_fn)GetProcAddress(ntdll, "LdrLoadDll");
    if (!loader_slot || !original_load || *loader_slot != (PVOID)original_load)
        return ERROR_REVISION_MISMATCH;
    /* Keep the helper resident until process exit, including on partial failure
     * or a call that was already in flight when the original pointer is restored. */
    HMODULE self;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
                           (LPCWSTR)(uintptr_t)compat_load, &self)) return GetLastError();
    attempts = 0;
    DWORD result = replace_slot((PVOID)original_load, (PVOID)compat_load);
    return result;
}

__declspec(dllexport) DWORD WINAPI GTMRemove(void)
{
    if (!installed) {
        DWORD ignored;
        if (protection_saved && loader_slot && *loader_slot == (PVOID)original_load)
            return VirtualProtect((PVOID)loader_slot, sizeof(PVOID), original_protection, &ignored)
                ? ERROR_SUCCESS : GetLastError();
        return ERROR_NOT_FOUND;
    }
    DWORD result = replace_slot((PVOID)compat_load, (PVOID)original_load);
    return result;
}

__declspec(dllexport) DWORD WINAPI GTMCoreLoadAttempts(void)
{
    return (DWORD)InterlockedCompareExchange(&attempts, 0, 0);
}
