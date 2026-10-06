/*
 * GTMLoaderProbe.exe: self-owned Windows x64 DLL-residency experiment.
 *
 * Required opt-in: GTM_LOADER_PROBE=1
 * One experiment per fresh process. Default: baseline, no input waits.
 * Arguments: --patched, --datafile, --noresolve, --wait, --no-wait.
 * Named checkpoints execute even when input waits are disabled.
 *
 * This program loads d3d12.dll through the ordinary Windows loader. It does
 * not create a GPU device, load d3d12core.dll explicitly, modify another
 * process, or change executable code. In --patched mode it loads the separate
 * self-test compatibility DLL beside this executable and calls its public
 * install/remove interface. GetModuleHandleW observations do not
 * acquire module references. Only our own d3d12.dll reference is released;
 * the compatibility DLL stays resident until this process exits.
 *
 * Exported ProbeRendezvous and ProbeLoadD3D12 provide named observation points.
 * The exported globals describe the current iteration and last load result.
 * Compile as a Windows x64 executable named GTMLoaderProbe.exe, preferably
 * with debug information and optimization disabled for this experiment.
 *
 * Allowed names are GTMLoaderProbe.exe and GTMLoaderProbeNegative.exe. The
 * latter exercises the compatibility DLL's own positive-name restriction.
 * GameToMac's GTA5_Enhanced.exe-specific hook will not match either name.
 *
 * Exit codes:
 *   0: experiment completed; NOT a claim of graphics support
 *   2: missing or invalid GTM_LOADER_PROBE opt-in
 *   3: executable name could not be verified or is not an allowed fixture
 *   4: checkpoint input ended or failed
 *   5: d3d12.dll failed to load
 *   6: a resident module's path could not be read completely
 *   7: our d3d12.dll reference could not be released
 *   8: invalid command-line arguments
 *   9: compatibility DLL or required exports could not be loaded
 *  10: core already resident before target load; fresh baseline unavailable
 *  11: GTMInstall returned failure (expected for the negative-name fixture)
 *  12: GTMRemove returned failure; compatibility DLL deliberately not unloaded
 *  13: patched result differs from the expected residency/count
 */

#if !defined(_WIN64) || !(defined(__x86_64__) || defined(_M_X64))
#error Build this source for Windows x64.
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

enum probe_phase {
    PROBE_BEFORE_LOAD = 10,
    PROBE_AFTER_LOAD = 20,
    PROBE_AFTER_REMOVE = 25,
    PROBE_AFTER_UNLOAD = 30,
    PROBE_FINISHED = 40
};

__declspec(dllexport) volatile DWORD g_probe_phase = 0;
__declspec(dllexport) volatile DWORD g_probe_iteration = 0;
__declspec(dllexport) volatile DWORD g_probe_load_error = ERROR_SUCCESS;
__declspec(dllexport) volatile DWORD g_probe_load_flags = LOAD_LIBRARY_SEARCH_SYSTEM32;
__declspec(dllexport) HMODULE volatile g_probe_d3d12 = NULL;
__declspec(dllexport) HMODULE volatile g_probe_core = NULL;

__declspec(dllexport) __declspec(noinline)
void WINAPI ProbeRendezvous(DWORD phase, DWORD iteration)
{
    g_probe_phase = phase;
    g_probe_iteration = iteration;
    MemoryBarrier();
}

__declspec(dllexport) __declspec(noinline)
HMODULE WINAPI ProbeLoadD3D12(void)
{
    HMODULE module;
    SetLastError(ERROR_SUCCESS);
    module = LoadLibraryExW(L"d3d12.dll", NULL, g_probe_load_flags);
    g_probe_load_error = GetLastError();
    g_probe_d3d12 = module;
    return module;
}

static int checkpoint(DWORD phase, DWORD iteration, int no_wait)
{
    char line[128];
    ProbeRendezvous(phase, iteration);
    printf("CHECKPOINT phase=%lu iteration=%lu\n",
           (unsigned long)phase, (unsigned long)iteration);
    if (no_wait)
        return 1;
    printf("Press Enter to continue.\n");
    if (!fgets(line, sizeof(line), stdin)) {
        fprintf(stderr, "Checkpoint input ended or failed.\n");
        return 0;
    }
    /* Consume an entire line so a long input cannot release another gate. */
    if (!strchr(line, '\n')) {
        int ch;
        do { ch = getchar(); } while (ch != '\n' && ch != EOF);
    }
    return 1;
}

static int observe_module(const wchar_t *name, int is_core)
{
    wchar_t path[32768];
    const DWORD capacity = (DWORD)(sizeof(path) / sizeof(path[0]));
    DWORD error, length;
    HMODULE module;

    SetLastError(ERROR_SUCCESS);
    module = GetModuleHandleW(name);
    error = GetLastError();
    if (is_core)
        g_probe_core = module;
    if (!module) {
        printf("MODULE name=%ls resident=0 query_error=%lu\n",
               name, (unsigned long)error);
        return 1; /* An absent module is an observation, not a test failure. */
    }
    SetLastError(ERROR_SUCCESS);
    length = GetModuleFileNameW(module, path, capacity);
    error = GetLastError();
    if (!length || length >= capacity) {
        fprintf(stderr, "Cannot read complete path for %ls; error=%lu\n",
                name, (unsigned long)error);
        return 0;
    }
    printf("MODULE name=%ls resident=1 handle=%p path=%ls\n",
           name, (void *)module, path);
    return 1;
}

static int snapshot(const char *label, DWORD iteration)
{
    printf("SNAPSHOT label=%s iteration=%lu\n", label, (unsigned long)iteration);
    if (!observe_module(L"d3d12.dll", 0))
        return 0;
    return observe_module(L"d3d12core.dll", 1);
}

static int verify_executable_name(void)
{
    wchar_t path[32768];
    wchar_t *backslash, *slash, *base;
    const DWORD capacity = (DWORD)(sizeof(path) / sizeof(path[0]));
    DWORD length = GetModuleFileNameW(NULL, path, capacity);
    if (!length || length >= capacity)
        return 0;
    backslash = wcsrchr(path, L'\\');
    slash = wcsrchr(path, L'/');
    base = backslash ? backslash + 1 : path;
    if (slash && slash + 1 > base)
        base = slash + 1;
    return _wcsicmp(base, L"GTMLoaderProbe.exe") == 0 ||
           _wcsicmp(base, L"GTMLoaderProbeNegative.exe") == 0;
}

typedef DWORD (WINAPI *GTM_API)(void);

static HMODULE load_compatibility_dll(void)
{
    wchar_t path[32768];
    wchar_t *backslash, *slash, *base;
    const wchar_t name[] = L"GTMCoreCompat.dll";
    const DWORD capacity = (DWORD)(sizeof(path) / sizeof(path[0]));
    DWORD length = GetModuleFileNameW(NULL, path, capacity);
    if (!length || length >= capacity)
        return NULL;
    backslash = wcsrchr(path, L'\\');
    slash = wcsrchr(path, L'/');
    base = backslash ? backslash + 1 : path;
    if (slash && slash + 1 > base)
        base = slash + 1;
    if ((size_t)(base - path) + sizeof(name) / sizeof(name[0]) > capacity) {
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return NULL;
    }
    wcscpy(base, name);
    printf("COMPAT path=%ls\n", path);
    return LoadLibraryExW(path, NULL,
                         LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
}

int main(int argc, char **argv)
{
    wchar_t enabled[2];
    int i, patched = 0, datafile = 0, noresolve = 0;
    int no_wait = 1, installed = 0, result = 0;
    HMODULE module = NULL, compat = NULL;
    GTM_API install_fn = NULL, remove_fn = NULL, attempts_fn = NULL;
    DWORD attempts = 0;

    setvbuf(stdout, NULL, _IONBF, 0);
    for (i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--patched")) patched = 1;
        else if (!strcmp(argv[i], "--datafile")) datafile = 1;
        else if (!strcmp(argv[i], "--noresolve")) noresolve = 1;
        else if (!strcmp(argv[i], "--wait")) no_wait = 0;
        else if (!strcmp(argv[i], "--no-wait")) no_wait = 1;
        else {
            fprintf(stderr, "Usage: GTMLoaderProbe.exe [--patched] [--datafile|--noresolve] [--wait|--no-wait]\n");
            return 8;
        }
    }
    if (datafile && noresolve) {
        fprintf(stderr, "Choose only one of --datafile and --noresolve.\n");
        return 8;
    }
    if (GetEnvironmentVariableW(L"GTM_LOADER_PROBE", enabled, 2) != 1 ||
        enabled[0] != L'1') {
        fprintf(stderr, "Opt-in required: GTM_LOADER_PROBE=1\n");
        return 2;
    }
    if (!verify_executable_name()) {
        fprintf(stderr, "Executable must be GTMLoaderProbe.exe or GTMLoaderProbeNegative.exe.\n");
        return 3;
    }
    printf("GTMLoaderProbe: Windows x64, mode=%s load=%s waits=%s\n",
           patched ? "patched" : "baseline",
           datafile ? "datafile" : (noresolve ? "noresolve" : "normal"),
           no_wait ? "off" : "on");
    printf("Core residency alone does not establish a functioning Agility SDK.\n");
    if (!snapshot("initial", 1))
        return 6;
    if (g_probe_core) {
        fprintf(stderr, "Core was already resident; use a fresh process for this experiment.\n");
        return 10;
    }

    if (patched) {
        DWORD status;
        compat = load_compatibility_dll();
        if (!compat) {
            fprintf(stderr, "Cannot load GTMCoreCompat.dll; error=%lu\n",
                    (unsigned long)GetLastError());
            return 9;
        }
        install_fn = (GTM_API)GetProcAddress(compat, "GTMInstall");
        remove_fn = (GTM_API)GetProcAddress(compat, "GTMRemove");
        attempts_fn = (GTM_API)GetProcAddress(compat, "GTMCoreLoadAttempts");
        if (!install_fn || !remove_fn || !attempts_fn) {
            fprintf(stderr, "Missing required compatibility DLL exports.\n");
            result = 9;
            goto cleanup;
        }
        status = install_fn();
        printf("INSTALL result=%lu core_load_attempts=%lu\n",
               (unsigned long)status, (unsigned long)attempts_fn());
        if (status) {
            result = 11;
            goto cleanup;
        }
        installed = 1;
    }

    if (!snapshot("before_load", 1)) { result = 6; goto cleanup; }
    if (g_probe_core) { result = 10; goto cleanup; }
    if (!checkpoint(PROBE_BEFORE_LOAD, 1, no_wait)) { result = 4; goto cleanup; }
    if (datafile)
        g_probe_load_flags |= LOAD_LIBRARY_AS_DATAFILE;
    if (noresolve)
        g_probe_load_flags = DONT_RESOLVE_DLL_REFERENCES;
    module = ProbeLoadD3D12();
    printf("LOAD name=d3d12.dll handle=%p last_error=%lu flags=%lu\n",
           (void *)module, (unsigned long)g_probe_load_error,
           (unsigned long)g_probe_load_flags);
    if (!snapshot("after_load", 1)) { result = 6; goto cleanup; }
    if (patched) {
        attempts = attempts_fn();
        printf("PATCH_RESULT core_resident=%d core_load_attempts=%lu\n",
               g_probe_core != NULL, (unsigned long)attempts);
        if ((datafile || noresolve) ? (attempts != 0 || g_probe_core != NULL)
                                   : (attempts != 1 || g_probe_core == NULL))
            result = 13;
    }
    if (!module)
        result = 5;
    if (!checkpoint(PROBE_AFTER_LOAD, 1, no_wait) && !result)
        result = 4;

cleanup:
    if (installed) {
        DWORD status = remove_fn();
        printf("REMOVE result=%lu core_load_attempts=%lu\n",
               (unsigned long)status, (unsigned long)attempts_fn());
        if (status) {
            /* A live callback might still refer to this DLL. Keep it resident
             * until process exit rather than unloading code it may target. */
            fprintf(stderr, "GTMRemove failed; retaining loaded modules until process exit.\n");
            return 12;
        }
        installed = 0;
        if (!snapshot("after_remove", 1) && !result)
            result = 6;
        if (!checkpoint(PROBE_AFTER_REMOVE, 1, no_wait) && !result)
            result = 4;
    }
    if (module) {
        if (!FreeLibrary(module)) {
            fprintf(stderr, "FreeLibrary(d3d12.dll) failed; error=%lu\n",
                    (unsigned long)GetLastError());
            if (!result) result = 7;
        } else {
            g_probe_d3d12 = NULL;
        }
        if (!snapshot("after_unload", 1) && !result)
            result = 6;
        if (!checkpoint(PROBE_AFTER_UNLOAD, 1, no_wait) && !result)
            result = 4;
    }
    /* Keep the compatibility DLL resident through process exit, including
     * after removal, so no concurrent callback could outlive its code. */
    ProbeRendezvous(PROBE_FINISHED, 1);
    printf("COMPLETE result=%d; no GPU device was created.\n", result);
    return result;
}
