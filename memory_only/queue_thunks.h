/* Fixed CrossOver 26.3 / D3DMetal 3 queue interoperability, in process memory.
 * Call only after a normal successful load of the pinned Apple d3d12.dll.
 * Native COM identity and call signatures are unchanged. No SDK state edits.
 * All native/RVA identities below must also be disk-hash checked by installer.
 */
#ifndef GTM_QUEUE_THUNKS_H
#define GTM_QUEUE_THUNKS_H
typedef unsigned char GQ8;
typedef unsigned short GQ16;
typedef unsigned int GQ32;
typedef unsigned long long GQ64;
#define GQABI __attribute__((ms_abi))
#define GQINLINE static __attribute__((always_inline)) inline
#define GQKB 0x6fffffc10000ULL
#define GQNT 0x6ffffff40000ULL
#define GQ16AT(a) (*(volatile GQ16 *)(GQ64)(a))
#define GQ32AT(a) (*(volatile GQ32 *)(GQ64)(a))
#define GQ64AT(a) (*(volatile GQ64 *)(GQ64)(a))
typedef void *(GQABI *gq_alloc_fn)(void *, GQ64, GQ32, GQ32);
typedef int (GQABI *gq_protect_fn)(void *, GQ64, GQ32, GQ32 *);
typedef int (GQABI *gq_free_fn)(void *, GQ64, GQ32);
typedef int (GQABI *gq_flush_fn)(void *, const void *, GQ64);
typedef int (GQABI *gq_lock_fn)(GQ32, GQ32 *, GQ64 *);
typedef int (GQABI *gq_unlock_fn)(GQ32, GQ64);

GQINLINE void gq_emit(volatile GQ8 *p, GQ64 destination)
{
    p[0] = 0xff; p[1] = 0x25;
    p[2] = 0; p[3] = 0; p[4] = 0; p[5] = 0;
    for (GQ32 i = 0; i < 8; ++i) p[6 + i] = (GQ8)(destination >> (i * 8));
    p[14] = 0xcc; p[15] = 0xcc;
}

/* Return1 means pair published. Return0 means no change; >=2 is a failure.
 * Any page ever published is intentionally retained until process exit.
 */
GQINLINE GQ32 GTMPrepareQueueThunks(GQ64 module)
{
    GQ64 cookie = 0, glue, metal, dispatch, execute, signal;
    GQ32 disposition = 0, old_protection = 0, result = 0;
    volatile GQ64 *execute_slot, *signal_slot;
    GQ8 *page = (GQ8 *)0;
    int published = 0;
    if (((gq_lock_fn)(GQNT + 0x15780))(0, &disposition, &cookie) != 0)
        return 2;
    /* Nested DllMain LoadLibrary can return before dispatch is populated. */
    if (!module || (module & 0xfff) ||
        GQ16AT(module) != 0x5a4d || GQ32AT(module + 0x3c) != 0x78 ||
        GQ32AT(module + 0x78) != 0x4550 || GQ16AT(module + 0x7c) != 0x8664 ||
        GQ32AT(module + 0x80) != 0x68e81b9b ||
        GQ32AT(module + 0xc8) != 0x1b000 ||
        GQ64AT(module + 0x1250) != 0x4800001df1058b48ULL)
        goto finish;
    dispatch = GQ64AT(module + 0x3048);
    if (dispatch < 0x100000 || dispatch >= 0x800000000000ULL) goto finish;
    glue = dispatch - 0x3120;
    if ((glue & 0xfff) || GQ64AT(glue) != 0x01000007feedfacfULL ||
        GQ64AT(glue + 0x6b0) != 0x000000180000001bULL ||
        GQ64AT(glue + 0x6b8) != 0x13363e4d51ea4b93ULL ||
        GQ64AT(glue + 0x6c0) != 0x6e1f2e6d074c0fadULL ||
        GQ64AT(glue + 0x3120) != 0xff00005f503d8348ULL)
        goto finish;
    dispatch = GQ64AT(glue + 0x9350);
    if (dispatch < 0x100000 || dispatch >= 0x800000000000ULL) goto finish;
    metal = dispatch - 0x3d0fc;
    if ((metal & 0xfff) || GQ64AT(metal) != 0x01000007feedfacfULL ||
        GQ64AT(metal + 0x910) != 0x000000180000001bULL ||
        GQ64AT(metal + 0x918) != 0xbd353fa90a95a39eULL ||
        GQ64AT(metal + 0x920) != 0xa41f7adcf92b038bULL)
        goto finish;
    execute = metal + 0x4814e;
    signal = metal + 0x48258;
    execute_slot = (volatile GQ64 *)(metal + 0x36eeb0);
    signal_slot = (volatile GQ64 *)(metal + 0x36eed0);
    if (*execute_slot != execute || *signal_slot != signal) goto finish;
    page = ((gq_alloc_fn)(GQKB + 0x6f680))((void *)0, 4096, 0x3000, 4);
    if (!page) { result = 3; goto finish; }
    gq_emit(page, execute);
    gq_emit(page + 32, signal);
    if (!((gq_protect_fn)(GQKB + 0x6fe30))(page, 4096, 0x20, &old_protection)) {
        result = 4; goto finish;
    }
    if (!((gq_flush_fn)(GQKB + 0x20ef0))((void *)~0ULL, page, 64)) {
        result = 5; goto finish;
    }
    if (!__atomic_compare_exchange_n(execute_slot, &execute, (GQ64)page,
                                     0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {
        result = 6; goto finish;
    }
    published = 1;
    if (!__atomic_compare_exchange_n(signal_slot, &signal, (GQ64)(page + 32),
                                     0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {
        GQ64 expected = (GQ64)page;
        (void)__atomic_compare_exchange_n(execute_slot, &expected, metal + 0x4814e,
                                          0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
        result = 7; goto finish;
    }
    result = 1;
finish:
    if (page && !published)
        (void)((gq_free_fn)(GQKB + 0x6fbd0))(page, 0, 0x8000);
    (void)((gq_unlock_fn)(GQNT + 0x16700))(0, cookie);
    return result;
}
#endif
