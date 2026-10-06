#ifndef GTM_LML_CONTEXT_H
#define GTM_LML_CONTEXT_H
typedef unsigned int GTM_U32;
typedef unsigned long long GTM_U64;
/* Private owned compatibility fixture ABI; all addresses belong to this process. */
struct GTMLmlContext {
    GTM_U64 original_pid, get_last_error, set_last_error;
    GTM_U64 load_library_ex_w, get_proc_address, easy_path, vfs_path;
    volatile GTM_U32 state, attempts, stage, result;
    volatile GTM_U32 easy_error, vfs_error, proc_error, reserved;
    volatile GTM_U64 easy_module, vfs_module, initialize_safe;
};
_Static_assert(sizeof(struct GTMLmlContext) == 112, "Context ABI size");
_Static_assert(__builtin_offsetof(struct GTMLmlContext, state) == 56, "State ABI");
_Static_assert(__builtin_offsetof(struct GTMLmlContext, easy_module) == 88, "Result ABI");
#endif
