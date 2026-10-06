/* Extracted .text entry: all code/data dependencies arrive in a trusted context.
 * Ordinary returns only: no SEH/C++ unwind metadata is supplied for this blob.
 * No process access, installer, arbitrary target arguments, or DLL unloading.
 */
#include "lml_context.h"
#define ABI __attribute__((ms_abi))
typedef GTM_U32 (ABI *pid_fn)(void);
typedef GTM_U32 (ABI *error_fn)(void);
typedef void (ABI *set_error_fn)(GTM_U32);
typedef void *(ABI *load_fn)(const unsigned short *, void *, GTM_U32);
typedef void *(ABI *proc_fn)(void *, const char *);
typedef void (ABI *initialize_fn)(void);

__attribute__((ms_abi, noinline, used))
GTM_U32 GTMLmlCallback(struct GTMLmlContext *context)
{
    GTM_U32 pid = ((pid_fn)context->original_pid)();
    GTM_U32 error = ((error_fn)context->get_last_error)();
    GTM_U32 expected = 0;
    if (__atomic_compare_exchange_n(&context->state, &expected, 1, 0,
                                    __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        void *easy, *vfs, *initialize;
        volatile char name[15];
        context->attempts++;
        context->stage = 1;
        easy = ((load_fn)context->load_library_ex_w)(
            (const unsigned short *)context->easy_path, (void *)0, 0);
        context->easy_module = (GTM_U64)easy;
        context->easy_error = easy ? 0 : ((error_fn)context->get_last_error)();
        context->stage = 2;
        vfs = ((load_fn)context->load_library_ex_w)(
            (const unsigned short *)context->vfs_path, (void *)0, 0);
        context->vfs_module = (GTM_U64)vfs;
        if (!vfs) {
            context->vfs_error = ((error_fn)context->get_last_error)();
            context->result = 1;
        } else {
            context->stage = 3;
            name[0]='I'; name[1]='n'; name[2]='i'; name[3]='t'; name[4]='i';
            name[5]='a'; name[6]='l'; name[7]='i'; name[8]='z'; name[9]='e';
            name[10]='S'; name[11]='a'; name[12]='f'; name[13]='e'; name[14]=0;
            initialize = ((proc_fn)context->get_proc_address)(vfs, (const char *)name);
            context->initialize_safe = (GTM_U64)initialize;
            if (!initialize) {
                context->proc_error = ((error_fn)context->get_last_error)();
                context->result = 2;
            } else {
                context->stage = 4;
                ((initialize_fn)initialize)();
                context->result = 0;
            }
        }
        context->stage = 5;
        __atomic_store_n(&context->state, 2, __ATOMIC_RELEASE);
    }
    ((set_error_fn)context->set_last_error)(error);
    return pid;
}
