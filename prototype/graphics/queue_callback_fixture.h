/* Exact fixed callback exercised in this owned probe only. No process access.
 * Runner verifies payload SHA256 and installed runtime hashes before launch. */
struct fixture_loader {
    void *entry;
    PVOID volatile *slot;
    void *original;
    int published;
};

static int fixture_loader_restore(struct fixture_loader *state)
{
    if (!state->published) return 1;
    DWORD prior = 0, ignored = 0;
    if (!VirtualProtect((void *)state->slot, sizeof(void *), PAGE_READWRITE, &prior)) return 0;
    void *old = InterlockedCompareExchangePointer(state->slot, state->original, state->entry);
    int ok = old == state->entry;
    if (ok) state->published = 0;
    if (!VirtualProtect((void *)state->slot, sizeof(void *), prior, &ignored)) ok = 0;
    printf("LOADER_RESTORE original_pointer=%d protection_restored=%d\n",
           *state->slot == state->original, ok);
    return ok;
}

static int fixture_loader_install(struct fixture_loader *state)
{
    WCHAR payload[4096];
    DWORD length = GetEnvironmentVariableW(L"GTM_QUEUE_CALLBACK_PATH", payload, 4096);
    if (!length || length >= 4096) return 0;
    HMODULE kb = GetModuleHandleW(L"kernelbase.dll"), nt = GetModuleHandleW(L"ntdll.dll");
    if ((uintptr_t)kb != 0x6fffffc10000ULL || (uintptr_t)nt != 0x6ffffff40000ULL ||
        GetModuleHandleW(L"d3d12.dll") || GetModuleHandleW(L"d3d12core.dll")) return 0;
    state->original = (void *)GetProcAddress(nt, "LdrLoadDll");
    if ((uintptr_t)state->original != 0x6ffffff55680ULL) return 0;
    state->slot = (PVOID volatile *)((unsigned char *)kb + 0xc9e40U);
    if (*state->slot != state->original) return 0;
    HANDLE file = CreateFileW(payload, GENERIC_READ, FILE_SHARE_READ, NULL,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    LARGE_INTEGER size;
    int ok = GetFileSizeEx(file, &size) && size.QuadPart == 1584;
    if (ok) state->entry = VirtualAlloc(NULL, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    DWORD received = 0;
    if (!state->entry || !ReadFile(file, state->entry, 1584, &received, NULL) || received != 1584) ok = 0;
    CloseHandle(file);
    if (!ok) return 0;
    DWORD prior = 0, ignored = 0;
    if (!VirtualProtect(state->entry, 4096, PAGE_EXECUTE_READ, &prior) ||
        !FlushInstructionCache(GetCurrentProcess(), state->entry, 1584) ||
        !VirtualProtect((void *)state->slot, sizeof(void *), PAGE_READWRITE, &prior)) return 0;
    void *old = InterlockedCompareExchangePointer(state->slot, state->entry, state->original);
    state->published = old == state->original;
    ok = state->published;
    if (!VirtualProtect((void *)state->slot, sizeof(void *), prior, &ignored)) ok = 0;
    printf("LOADER_INSTALL exact_payload_bytes=1584 installed=%d protection_restored=%d\n",
           state->published, ok);
    /* Callback code is intentionally retained through process exit. */
    return ok;
}

static int fixture_callback_entries(HMODULE d3d12, unsigned char **entries_out, void ***table_out)
{
    /* Inspect only the pinned renderer's expected dispatch chain and table. */
    uintptr_t glue_entry = *(uintptr_t *)((unsigned char *)d3d12 + 0x3048U);
    if (glue_entry < 0x100000U) return 0;
    uintptr_t glue = glue_entry - 0x3120U;
    if ((glue & 0xfffU) || *(uint64_t *)glue != 0x01000007feedfacfULL) return 0;
    uintptr_t metal_entry = *(uintptr_t *)(glue + 0x9350U);
    if (metal_entry < 0x100000U) return 0;
    uintptr_t metal = metal_entry - 0x3d0fcU;
    if ((metal & 0xfffU) || *(uint64_t *)metal != 0x01000007feedfacfULL) return 0;
    void **table = (void **)(metal + 0x36ee60U);
    unsigned char *entry = table[10];
    MEMORY_BASIC_INFORMATION info = {0};
    int ok = VirtualQuery(entry, &info, sizeof(info)) == sizeof(info) &&
             info.State == MEM_COMMIT && info.Protect == PAGE_EXECUTE_READ &&
             info.Type == MEM_PRIVATE && info.AllocationBase == entry &&
             table[14] == entry + 32;
    if (!ok) return 0;
    uintptr_t target0 = 0, target1 = 0;
    const unsigned char jump[6] = {0xff, 0x25, 0, 0, 0, 0};
    memcpy(&target0, entry + 6, sizeof(target0));
    memcpy(&target1, entry + 38, sizeof(target1));
    ok = memcmp(entry, jump, sizeof(jump)) == 0 &&
         memcmp(entry + 32, jump, sizeof(jump)) == 0 &&
         target0 == metal + 0x4814eU && target1 == metal + 0x48258U;
    printf("CALLBACK_BEFORE_DEVICE entries_published=%d core_resident=%d\n", ok,
           GetModuleHandleW(L"d3d12core.dll") != NULL);
    if (ok) { *entries_out = entry; *table_out = table; }
    return ok && GetModuleHandleW(L"d3d12core.dll") != NULL;
}
