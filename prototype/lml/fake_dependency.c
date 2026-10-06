#include <windows.h>
typedef void (WINAPI *event_fn)(DWORD);
static volatile LONG calls;
static void event(DWORD code)
{
    event_fn notify = (event_fn)(void *)GetProcAddress(GetModuleHandleW(NULL), "GTMFixtureEvent");
    if (notify) notify(code);
}
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID reserved)
{
    (void)instance; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) event(GTM_DEPENDENCY_KIND);
    return TRUE;
}
__declspec(dllexport) DWORD WINAPI FixtureInitCalls(void)
{
    return (DWORD)InterlockedCompareExchange(&calls, 0, 0);
}
#if GTM_DEPENDENCY_KIND == 2 && !defined(GTM_MISSING_EXPORT)
__declspec(dllexport) void InitializeSafe(void)
{
    InterlockedIncrement(&calls);
    event(3);
    Sleep(30); /* Give competing owned threads time to exercise the working state. */
}
#endif
