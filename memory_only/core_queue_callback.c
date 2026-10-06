/*
 * Fixed CrossOver 26.3 x64 loader callback with shared queue forwarding.
 * No process access, imports, global storage, runtime library, or installer.
 * Entry and every indirect callee use the Windows x64 calling convention.
 * Runtime hashes, mapped bases, lifetime and installation are checked outside
 * this artifact. These addresses are deliberately not configurable.
 */
#include "queue_thunks.h"

typedef unsigned short U16;
typedef unsigned int U32;
typedef unsigned long long U64;
typedef signed int STATUS;

struct unicode_name {
    U16 length;
    U16 maximum_length;
    U16 *buffer;
};

#define WINABI __attribute__((ms_abi))
typedef STATUS (WINABI *load_original_fn)(U16 *, U32, struct unicode_name *, void **);
typedef U32 (WINABI *get_error_fn)(void);
typedef void (WINABI *set_error_fn)(U32);
typedef void *(WINABI *get_module_fn)(const U16 *);
typedef void *(WINABI *load_core_fn)(const U16 *, void *, U32);

_Static_assert(sizeof(void *) == 8, "Only Windows x64 is supported");
_Static_assert(sizeof(struct unicode_name) == 16, "Unexpected UNICODE_STRING layout");
_Static_assert(__builtin_offsetof(struct unicode_name, buffer) == 8, "Unexpected buffer offset");

__attribute__((ms_abi, noinline, used))
STATUS GTMCoreQueueCompatCallback(U16 *search_path, U32 flags,
                             struct unicode_name *name, void **module)
{
    STATUS status = ((load_original_fn)0x6ffffff55680ULL)(search_path, flags, name, module);
    U32 saved_error = ((get_error_fn)0x6ffffff67390ULL)();

    if (status == 0 && !(flags & 0x63U) && name && name->buffer &&
        !(name->length & 1U) && name->length <= name->maximum_length) {
        U32 count = name->length / 2U;
        U32 start = count;
        while (start && name->buffer[start - 1] != '\\' && name->buffer[start - 1] != '/')
            --start;
        if (count - start == 9U) {
            U32 i;
            for (i = 0; i < 9U; ++i) {
                U32 ch = name->buffer[start + i];
                U32 expected = i == 8U ? 'l' : (U32)((0x6c642e3231643364ULL >> (i * 8U)) & 0xffU);
                if (ch >= 'A' && ch <= 'Z') ch += 'a' - 'A';
                if (ch != expected) break;
            }
            if (i == 9U) {
                if (module && *module)
                    (void)GTMPrepareQueueThunks((U64)*module);
                /* Explicit volatile stores keep the UTF-16 name on the stack. */
                volatile U16 core[14];
                core[0] = 'd'; core[1] = '3'; core[2] = 'd'; core[3] = '1';
                core[4] = '2'; core[5] = 'c'; core[6] = 'o'; core[7] = 'r';
                core[8] = 'e'; core[9] = '.'; core[10] = 'd'; core[11] = 'l';
                core[12] = 'l'; core[13] = 0;
                if (!((get_module_fn)0x6fffffc3be40ULL)((const U16 *)core))
                    ((load_core_fn)0x6fffffc492d0ULL)((const U16 *)core, (void *)0, 0x800U);
            }
        }
    }
    ((set_error_fn)0x6ffffff74980ULL)(saved_error);
    return status;
}
