/* Self-owned import test. Never opens another process or uses real mod DLLs. */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include "lml_context.h"

typedef DWORD (WINAPI *pid_fn)(void);
typedef DWORD (WINAPI *count_fn)(void);
static HANDLE start_event;
static DWORD expected_pid, expected_original_error;
static volatile LONG failures, events_count, recursive_calls, total_calls;
static LONG events[64];
static BOOL recursive;
static DWORD iterations;
static pid_fn original_pid;

__declspec(noinline) static DWORD owned_pid_call(void) { return GetCurrentProcessId(); }
static void check_call(DWORD error)
{
    SetLastError(error);
    DWORD pid = owned_pid_call();
    DWORD after = GetLastError();
    if (pid != expected_pid || after != (expected_original_error ? expected_original_error : error))
        InterlockedIncrement(&failures);
    InterlockedIncrement(&total_calls);
}
__declspec(dllexport) void WINAPI GTMFixtureEvent(DWORD code)
{
    LONG index = InterlockedIncrement(&events_count) - 1;
    if (index >= 0 && index < 64) events[index] = (LONG)code;
    else InterlockedIncrement(&failures);
    if (recursive) {
        InterlockedIncrement(&recursive_calls);
        check_call(0x51000000U + code);
    }
}
static DWORD WINAPI fake_original_pid(void)
{
    DWORD pid = original_pid();
    SetLastError(expected_original_error);
    return pid;
}
static DWORD WINAPI worker(void *unused)
{
    (void)unused;
    if (WaitForSingleObject(start_event, 10000) != WAIT_OBJECT_0) return 1;
    for (DWORD i = 0; i < iterations; ++i) check_call(0x52000000U + i);
    return 0;
}
static void **pid_import(void)
{
    BYTE *base = (BYTE *)GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(base + dos->e_lfanew);
    IMAGE_IMPORT_DESCRIPTOR *imports = (IMAGE_IMPORT_DESCRIPTOR *)(base +
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT].VirtualAddress);
    for (; imports->Name; ++imports) {
        if (_stricmp((char *)base + imports->Name, "KERNEL32.dll")) continue;
        IMAGE_THUNK_DATA64 *names = (IMAGE_THUNK_DATA64 *)(base + imports->OriginalFirstThunk);
        IMAGE_THUNK_DATA64 *slots = (IMAGE_THUNK_DATA64 *)(base + imports->FirstThunk);
        for (unsigned i = 0; names[i].u1.AddressOfData; ++i) {
            if (IMAGE_SNAP_BY_ORDINAL64(names[i].u1.Ordinal)) continue;
            IMAGE_IMPORT_BY_NAME *name = (IMAGE_IMPORT_BY_NAME *)(base + names[i].u1.AddressOfData);
            if (!strcmp((char *)name->Name, "GetCurrentProcessId")) return (void **)&slots[i].u1.Function;
        }
    }
    return NULL;
}
static int fullpath(WCHAR *out, const WCHAR *directory, const WCHAR *name)
{
    return swprintf(out, 1024, L"%ls\\%ls", directory, name) > 0;
}
int main(int argc, char **argv)
{
    WCHAR directory[1024], easy[1024], vfs[1024], blob_path[1024];
    char guard[8];
    DWORD count, size, protection, ignored, thread_count = 1;
    HANDLE file, threads[16] = {0};
    BYTE *code;
    struct GTMLmlContext *context;
    void **slot;
    int mode = argc == 2 ? atoi(argv[1]) : -1;
    if (mode < 0 || mode > 6 || !GetEnvironmentVariableA("GTM_LML_OWNED_FIXTURE", guard, sizeof(guard)) || strcmp(guard,"1")) return 2;
    if (!GetModuleFileNameW(NULL, directory, 1024)) return 3;
    WCHAR *slash = wcsrchr(directory, L'\\');
    if (!slash || wcscmp(slash + 1, L"GTMLmlProbe.exe")) return 4;
    *slash = 0;
    if (!fullpath(easy,directory,mode == 5 ? L"absent\\EasyHookPatch.dll" : L"fake\\EasyHookPatch.dll") ||
        !fullpath(vfs,directory,mode == 3 ? L"absent\\vfs.dll" : mode == 4 ? L"missing-export\\vfs.dll" : L"fake\\vfs.dll") ||
        !fullpath(blob_path,directory,L"lml_callback.bin")) return 5;
    file = CreateFileW(blob_path,GENERIC_READ,FILE_SHARE_READ,NULL,OPEN_EXISTING,0,NULL);
    if (file == INVALID_HANDLE_VALUE) return 6;
    size = GetFileSize(file,NULL);
    if (size == INVALID_FILE_SIZE || !size || size > 4000) { CloseHandle(file); return 7; }
    code = VirtualAlloc(NULL,4096,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    context = VirtualAlloc(NULL,4096,MEM_COMMIT|MEM_RESERVE,PAGE_READWRITE);
    if (!code || !context) { CloseHandle(file); return 8; }
    if (!ReadFile(file,code+16,size,&count,NULL) || count != size) { CloseHandle(file); return 9; }
    CloseHandle(file);
    code[0]=0x48; code[1]=0xb9; memcpy(code+2,&context,8);
    code[10]=0xe9; code[11]=1; code[12]=0; code[13]=0; code[14]=0; code[15]=0x90;
    HMODULE kernel = GetModuleHandleW(L"kernel32.dll");
    original_pid = (pid_fn)(void *)GetProcAddress(kernel,"GetCurrentProcessId");
    expected_pid = original_pid();
    if (GetModuleHandleW(L"vfs.dll") || GetModuleHandleW(L"EasyHookPatch.dll")) return 20;
    slot = pid_import();
    if (!slot || *slot != (void *)original_pid) return 10;
    expected_original_error = mode == 6 ? 0x53454341U : 0;
    context->original_pid = (GTM_U64)(mode == 6 ? fake_original_pid : original_pid);
    context->get_last_error=(GTM_U64)(void *)GetProcAddress(kernel,"GetLastError");
    context->set_last_error=(GTM_U64)(void *)GetProcAddress(kernel,"SetLastError");
    context->load_library_ex_w=(GTM_U64)(void *)GetProcAddress(kernel,"LoadLibraryExW");
    context->get_proc_address=(GTM_U64)(void *)GetProcAddress(kernel,"GetProcAddress");
    context->easy_path=(GTM_U64)easy; context->vfs_path=(GTM_U64)vfs;
    context->result=0xffffffffU;
    if (!VirtualProtect(code,4096,PAGE_EXECUTE_READ,&protection) || !FlushInstructionCache(GetCurrentProcess(),code,16+size)) return 11;
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(code,&mbi,sizeof(mbi)) || mbi.State != MEM_COMMIT || mbi.Protect != PAGE_EXECUTE_READ) return 12;
    recursive = mode == 1 || mode == 2 || mode == 5;
    iterations = mode == 2 ? 1000 : 5;
    thread_count = mode == 2 ? 16 : 1;
    start_event = CreateEventW(NULL,TRUE,FALSE,NULL);
    if (!start_event) return 13;
    for (DWORD i=0;i<thread_count;++i) {
        threads[i]=CreateThread(NULL,0,worker,NULL,0,NULL);
        if (!threads[i]) return 14;
    }
    if (!VirtualProtect(slot,8,PAGE_READWRITE,&protection)) return 15;
    InterlockedExchangePointer((void *volatile *)slot,code);
    if (!VirtualProtect(slot,8,protection,&ignored)) return 16;
    SetEvent(start_event);
    if (WaitForMultipleObjects(thread_count,threads,TRUE,30000) != WAIT_OBJECT_0) return 17;
    for (DWORD i=0;i<thread_count;++i) {
        DWORD result=1;
        if (!GetExitCodeThread(threads[i],&result) || result) ++failures;
        CloseHandle(threads[i]);
    }
    CloseHandle(start_event);
    if (!VirtualProtect(slot,8,PAGE_READWRITE,&protection)) return 18;
    InterlockedExchangePointer((void *volatile *)slot,(void *)original_pid);
    if (!VirtualProtect(slot,8,protection,&ignored)) return 19;
    DWORD init_count=0;
    if (context->vfs_module) {
        count_fn fn=(count_fn)(void *)GetProcAddress((HMODULE)context->vfs_module,"FixtureInitCalls");
        if (!fn) ++failures; else init_count=fn();
    }
    DWORD expected_result = mode == 3 ? 1 : mode == 4 ? 2 : 0;
    DWORD expected_init = expected_result ? 0 : 1;
    LONG expected_events = (mode == 3 ? 1 : mode == 4 || mode == 5 ? 2 : 3);
    if (context->state != 2 || context->attempts != 1 || context->stage != 5 ||
        context->result != expected_result || init_count != expected_init || events_count != expected_events) ++failures;
    for (LONG i=0;i<events_count && i<64;++i)
        if (events[i] != i + (mode == 5 ? 2 : 1)) ++failures;
    if ((mode == 3 && !context->vfs_error) || (mode == 4 && !context->proc_error) ||
        (mode == 5 && !context->easy_error)) ++failures;
    printf("RESULT mode=%d state=%u attempts=%u stage=%u result=%u init_calls=%lu events=%ld recursive=%ld calls=%ld failures=%ld\n",
           mode,context->state,context->attempts,context->stage,context->result,(unsigned long)init_count,
           events_count,recursive_calls,total_calls,failures);
    printf("ERRORS easy=%u vfs=%u export=%u; CODE rx=1; IAT restored=%d; allocations_retained=1\n",
           context->easy_error,context->vfs_error,context->proc_error,*slot==(void *)original_pid);
    printf("COMPLETE result=%d\n",failures ? 1 : 0);
    return failures ? 1 : 0;
}
