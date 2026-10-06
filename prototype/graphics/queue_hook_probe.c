/* Owned process only: exercise two queue forwarding entries with upstream MinHook.
 * No PID arguments, process access, game, SDK, or runtime-file modification.
 * All public queue calls execute serially; each GPU submission completes before
 * entry changes. Hook trampolines may reference their thunk's embedded literal,
 * so thunk storage outlives hook removal and restoration of the native table.
 */
#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#include <windows.h>
#include <d3d12.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "vendor/minhook/include/MinHook.h"
#ifdef GTM_EXACT_CALLBACK_FIXTURE
#include "queue_callback_fixture.h"
#endif

typedef HRESULT (WINAPI *create_device_fn)(IUnknown *, D3D_FEATURE_LEVEL, REFIID, void **);
typedef void (STDMETHODCALLTYPE *execute_fn)(ID3D12CommandQueue *, UINT, ID3D12CommandList *const *);
typedef HRESULT (STDMETHODCALLTYPE *signal_fn)(ID3D12CommandQueue *, ID3D12Fence *, UINT64);
static execute_fn original_execute;
static signal_fn original_signal;
static volatile LONG execute_calls, signal_calls;
static const IID device_iid = {0x189819f1,0x1db6,0x4b57,{0xbe,0x54,0x18,0x21,0x33,0x9b,0x85,0xf7}};
static const IID queue_iid = {0x0ec870a6,0x5d7e,0x4c22,{0x8c,0xfc,0x5b,0xaa,0xe0,0x76,0x16,0xed}};
static const IID fence_iid = {0x0a753dcf,0xc4d8,0x4b91,{0xad,0xf6,0xbe,0x5a,0x60,0xd9,0x5a,0x76}};
static const IID allocator_iid = {0x6102dee4,0xaf59,0x4b09,{0xb9,0x99,0xb4,0x4d,0x73,0xf0,0x9b,0x24}};
static const IID list_iid = {0x5b160d0f,0xac1b,0x4185,{0x8b,0xa8,0xb3,0xae,0x42,0xa5,0xa4,0x55}};
static const IID unknown_iid = {0,0,0,{0xc0,0,0,0,0,0,0,0x46}};

static void STDMETHODCALLTYPE detour_execute(ID3D12CommandQueue *q, UINT n,
                                             ID3D12CommandList *const *lists)
{
    InterlockedIncrement(&execute_calls);
    original_execute(q, n, lists);
}

static HRESULT STDMETHODCALLTYPE detour_signal(ID3D12CommandQueue *q, ID3D12Fence *f, UINT64 v)
{
    InterlockedIncrement(&signal_calls);
    return original_signal(q, f, v);
}

static int executable_entry(const char *label, const void *address)
{
    MEMORY_BASIC_INFORMATION info = {0};
    SIZE_T bytes = VirtualQuery(address, &info, sizeof(info));
    int ok = bytes == sizeof(info) && info.State == MEM_COMMIT &&
             (info.Protect & (PAGE_EXECUTE | PAGE_EXECUTE_READ |
                              PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY));
    printf("ENTRY label=%s query_bytes=%llu state=0x%lx protect=0x%lx type=0x%lx executable=%d\n",
           label, (unsigned long long)bytes, (unsigned long)info.State,
           (unsigned long)info.Protect, (unsigned long)info.Type, ok);
    return ok;
}

static int check_identity(ID3D12CommandQueue *q, IUnknown *expected, const char *stage)
{
    IUnknown *actual = NULL;
    HRESULT hr = ID3D12CommandQueue_QueryInterface(q, &unknown_iid, (void **)&actual);
    int ok = SUCCEEDED(hr) && actual == expected;
    printf("IDENTITY stage=%s hresult=0x%08lx unchanged=%d\n", stage, (unsigned long)hr, ok);
    if (actual) IUnknown_Release(actual);
    return ok;
}

static int submit(ID3D12Device *device, ID3D12CommandQueue *q,
                  ID3D12GraphicsCommandList *list, ID3D12Fence *fence,
                  HANDLE event, UINT64 value, const char *stage)
{
    ID3D12CommandList *lists[] = {(ID3D12CommandList *)list};
    ID3D12CommandQueue_ExecuteCommandLists(q, 1, lists);
    HRESULT signal_hr = ID3D12CommandQueue_Signal(q, fence, value);
    HRESULT event_hr = FAILED(signal_hr) ? signal_hr :
        ID3D12Fence_SetEventOnCompletion(fence, value, event);
    DWORD wait = FAILED(event_hr) ? WAIT_FAILED : WaitForSingleObject(event, 2000);
    UINT64 completed = ID3D12Fence_GetCompletedValue(fence);
    HRESULT removed = ID3D12Device_GetDeviceRemovedReason(device);
    int ok = SUCCEEDED(signal_hr) && SUCCEEDED(event_hr) && wait == WAIT_OBJECT_0 &&
             completed == value && completed != UINT64_MAX && SUCCEEDED(removed);
    printf("SUBMIT stage=%s signal_hr=0x%08lx event_hr=0x%08lx wait=0x%08lx "
           "completed=%llu expected=%llu removed_hr=0x%08lx passed=%d\n",
           stage, (unsigned long)signal_hr, (unsigned long)event_hr,
           (unsigned long)wait, (unsigned long long)completed,
           (unsigned long long)value, (unsigned long)removed, ok);
    return ok;
}

static int mh_result(const char *operation, MH_STATUS status)
{
    printf("MINHOOK operation=%s status=%d name=%s\n", operation, (int)status,
           MH_StatusToString(status));
    return status == MH_OK;
}

int main(int argc, char **argv)
{
    (void)argv;
    WCHAR enabled[2], image[MAX_PATH];
    setvbuf(stdout, NULL, _IONBF, 0);
    if (argc != 1 || GetEnvironmentVariableW(L"GTM_QUEUE_HOOK_PROBE", enabled, 2) != 1 ||
        enabled[0] != L'1' || !GetModuleFileNameW(NULL, image, MAX_PATH)) return 2;
    WCHAR *base = image;
    for (WCHAR *p = image; *p; ++p) if (*p == L'\\' || *p == L'/') base = p + 1;
#ifdef GTM_EXACT_CALLBACK_FIXTURE
    if (wcscmp(base, L"GTMQueueCallbackProbe.exe")) return 2;
    struct fixture_loader loader = {0};
#else
    if (wcscmp(base, L"GTMQueueHookProbe.exe")) return 2;
#endif

    HMODULE d3d12 = NULL;
    ID3D12Device *device = NULL;
    ID3D12CommandQueue *queue = NULL;
    ID3D12CommandAllocator *allocator = NULL;
    ID3D12GraphicsCommandList *list = NULL;
    ID3D12Fence *fence = NULL;
    IUnknown *identity = NULL;
    HANDLE event = NULL;
    unsigned char *entries = NULL;
    void **table = NULL, *saved[19] = {0};
    int result = 1, initialized = 0, execute_created = 0, signal_created = 0;
    int execute_enabled = 0, signal_enabled = 0, table_changed = 0;
    int restoration_ok = 1, hooks_removed = 1;
    HRESULT hr;
    printf("GTMQueueHookProbe: own process, empty DIRECT submission, two temporary queue entries.\n");
#ifdef GTM_EXACT_CALLBACK_FIXTURE
    if (!fixture_loader_install(&loader)) goto cleanup;
#endif
    d3d12 = LoadLibraryExW(L"d3d12.dll", NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
#ifdef GTM_EXACT_CALLBACK_FIXTURE
    if (!fixture_loader_restore(&loader)) goto cleanup;
    if (!d3d12 || !fixture_callback_entries(d3d12, &entries, &table)) goto cleanup;
    memcpy(saved, table, sizeof(saved));
    memcpy(&saved[10], entries + 6, sizeof(void *));
    memcpy(&saved[14], entries + 38, sizeof(void *));
    table_changed = 1;
#endif
    if (!d3d12) goto cleanup;
    create_device_fn create = (create_device_fn)GetProcAddress(d3d12, "D3D12CreateDevice");
    if (!create) goto cleanup;
    hr = create(NULL, D3D_FEATURE_LEVEL_12_0, &device_iid, (void **)&device);
    printf("CREATE_DEVICE hresult=0x%08lx nonnull=%d\n", (unsigned long)hr, device != NULL);
    if (FAILED(hr) || !device) goto cleanup;
    D3D12_COMMAND_QUEUE_DESC desc = {0};
    desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
    hr = ID3D12Device_CreateCommandQueue(device, &desc, &queue_iid, (void **)&queue);
    if (FAILED(hr) || !queue) goto cleanup;
    hr = ID3D12CommandQueue_QueryInterface(queue, &unknown_iid, (void **)&identity);
    if (FAILED(hr) || !identity) goto cleanup;
    hr = ID3D12Device_CreateCommandAllocator(device, D3D12_COMMAND_LIST_TYPE_DIRECT,
                                            &allocator_iid, (void **)&allocator);
    if (FAILED(hr) || !allocator) goto cleanup;
    hr = ID3D12Device_CreateCommandList(device, 0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                                       allocator, NULL, &list_iid, (void **)&list);
    if (FAILED(hr) || !list) goto cleanup;
    hr = ID3D12GraphicsCommandList_Close(list);
    printf("CLOSE_EMPTY_LIST hresult=0x%08lx\n", (unsigned long)hr);
    if (FAILED(hr)) goto cleanup;
    hr = ID3D12Device_CreateFence(device, 0, D3D12_FENCE_FLAG_NONE, &fence_iid, (void **)&fence);
    if (FAILED(hr) || !fence) goto cleanup;
    event = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!event || !submit(device, queue, list, fence, event, 1, "baseline") ||
        !check_identity(queue, identity, "baseline")) goto cleanup;

    table = (void **)(uintptr_t)queue->lpVtbl;
    memcpy(saved, table, sizeof(saved));
#ifdef GTM_EXACT_CALLBACK_FIXTURE
    uintptr_t native_base = (uintptr_t)table - 0x36ee60U;
    int layout_ok = table[10] == entries && table[14] == entries + 32;
    saved[10] = (void *)(native_base + 0x4814eU);
    saved[14] = (void *)(native_base + 0x48258U);
    table_changed = layout_ok;
#else
    uintptr_t native_base = (uintptr_t)saved[10] - 0x4814eU;
    int layout_ok = (uintptr_t)saved[14] == native_base + 0x48258U &&
                    (uintptr_t)table == native_base + 0x36ee60U;
#endif
    printf("NATIVE_LAYOUT pinned_crossOver26_3_apple3_layout=%d\n", layout_ok);
    if (!layout_ok) goto cleanup;
    executable_entry("native_execute", saved[10]);
    executable_entry("native_signal", saved[14]);

#ifndef GTM_EXACT_CALLBACK_FIXTURE
    entries = VirtualAlloc(NULL, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!entries) goto cleanup;
    memset(entries, 0x90, 64);
    const unsigned char jump[6] = {0xff, 0x25, 0, 0, 0, 0};
    memcpy(entries, jump, sizeof(jump));
    memcpy(entries + 6, &saved[10], sizeof(void *));
    memcpy(entries + 32, jump, sizeof(jump));
    memcpy(entries + 38, &saved[14], sizeof(void *));
    DWORD previous;
    if (!VirtualProtect(entries, 4096, PAGE_EXECUTE_READ, &previous) ||
        !FlushInstructionCache(GetCurrentProcess(), entries, 64) ||
        !executable_entry("windows_execute", entries) ||
        !executable_entry("windows_signal", entries + 32)) goto cleanup;

    if (table[10] != saved[10] || table[14] != saved[14]) goto cleanup;
    void *old_execute = InterlockedExchangePointer((PVOID volatile *)&table[10], entries);
    void *old_signal = InterlockedExchangePointer((PVOID volatile *)&table[14], entries + 32);
    table_changed = 1;
    if (old_execute != saved[10] || old_signal != saved[14]) goto cleanup;
#else
    if (!executable_entry("windows_execute", entries) ||
        !executable_entry("windows_signal", entries + 32)) goto cleanup;
#endif
    if (!submit(device, queue, list, fence, event, 2, "forwarding") ||
        !check_identity(queue, identity, "forwarding")) goto cleanup;

    if (!mh_result("initialize", MH_Initialize())) goto cleanup;
    initialized = 1;
    if (!mh_result("create_execute", MH_CreateHook(entries, (void *)detour_execute,
                                                    (void **)&original_execute))) goto cleanup;
    execute_created = 1;
    if (!mh_result("create_signal", MH_CreateHook(entries + 32, (void *)detour_signal,
                                                   (void **)&original_signal))) goto cleanup;
    signal_created = 1;
    if (!mh_result("enable_execute", MH_EnableHook(entries))) goto cleanup;
    execute_enabled = 1;
    if (!mh_result("enable_signal", MH_EnableHook(entries + 32))) goto cleanup;
    signal_enabled = 1;
    if (!submit(device, queue, list, fence, event, 3, "hooked") ||
        !check_identity(queue, identity, "hooked")) goto cleanup;
    printf("COUNTERS execute=%ld signal=%ld expected_each=1\n", execute_calls, signal_calls);
    if (execute_calls != 1 || signal_calls != 1) goto cleanup;
    result = 0;

cleanup:
#ifdef GTM_EXACT_CALLBACK_FIXTURE
    if (!fixture_loader_restore(&loader)) result = 1;
#endif
    /* No public queue calls can overlap this single-threaded fixture cleanup. */
    if (signal_enabled && !mh_result("disable_signal", MH_DisableHook(entries + 32))) hooks_removed = 0;
    if (execute_enabled && !mh_result("disable_execute", MH_DisableHook(entries))) hooks_removed = 0;
    if (signal_created && !mh_result("remove_signal", MH_RemoveHook(entries + 32))) hooks_removed = 0;
    if (execute_created && !mh_result("remove_execute", MH_RemoveHook(entries))) hooks_removed = 0;
    if (table_changed) {
        void *old_execute = InterlockedExchangePointer((PVOID volatile *)&table[10], saved[10]);
        void *old_signal = InterlockedExchangePointer((PVOID volatile *)&table[14], saved[14]);
        restoration_ok = old_execute == entries && old_signal == entries + 32 &&
                         memcmp(saved, table, sizeof(saved)) == 0;
        printf("RESTORE native_table_unchanged=%d\n", restoration_ok);
    }
    if (initialized && !mh_result("uninitialize", MH_Uninitialize())) hooks_removed = 0;
    if (!restoration_ok || !hooks_removed) result = 1;
    if (!result) {
        if (!submit(device, queue, list, fence, event, 4, "restored") ||
            !check_identity(queue, identity, "restored") ||
            execute_calls != 1 || signal_calls != 1) result = 1;
        printf("RESTORED_COUNTERS execute=%ld signal=%ld unchanged=%d\n",
               execute_calls, signal_calls, execute_calls == 1 && signal_calls == 1);
    }
    /* MinHook/Rosetta may retain references to the embedded target literals.
     * Retain every thunk page until process exit even after successful cleanup. */
    if (entries) printf("RETAIN_THUNKS until_process_exit=1\n");
    if (identity) IUnknown_Release(identity);
    if (list) ID3D12GraphicsCommandList_Release(list);
    if (allocator) ID3D12CommandAllocator_Release(allocator);
    if (queue) ID3D12CommandQueue_Release(queue);
    if (fence) ID3D12Fence_Release(fence);
    if (event) CloseHandle(event);
    if (device) ID3D12Device_Release(device);
    if (d3d12) FreeLibrary(d3d12);
    printf("COMPLETE result=%d\n", result);
    return result;
}
