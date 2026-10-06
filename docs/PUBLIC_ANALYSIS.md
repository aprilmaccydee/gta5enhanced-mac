# GTA V Enhanced startup: findings and validated correction

This investigation began with a static inspection of GameToMac **0.1.48 Alpha, build 88**, then tested a narrowly scoped compatibility correction in an existing CrossOver installation. The final Rockstar-edition launch passed the previously observed initialization wait, and the user confirmed that a main menu or game window opened. This establishes startup success on the pinned installation, not sustained gameplay, performance, or broad hardware compatibility.

## What GameToMac supplies

The inspected bundle combines an x86-64 Wine **11.13** runtime, Rosetta, Apple **D3DMetal 4.0 beta 2**, and separate launcher/game settings. Rosetta translates CPU instructions; Wine implements Windows APIs; D3DMetal handles graphics translation and uses Apple's shader conversion components. Apple's [Game Porting Toolkit explanation](https://developer.apple.com/videos/play/wwdc2023/10123/) describes this broader translation architecture.

The bundled Wine changes cover Apple's native callback and window-surface integration, browser-helper compatibility, Rockstar launcher feature-level selection, an unchanged-keyboard-layout notification guard, and a GTA-specific D3D12Core load. Package hashes and selected binary properties corroborated the supplied source and manifests.

This is substantial runtime integration, rather than a native macOS version of GTA. The bundled validation records report isolated fullscreen launches, while still marking integrated gameplay and clean first-run testing as pending. GameToMac itself was not launched during this investigation. Its Steam profile and Apple 4.0b2 renderer are distinct from the Rockstar/CrossOver configuration tested below.

## First blocker: D3D12Core module residency

The unmodified CrossOver launch reported `ERR_GFX_D3D_NOD3D12`, “Failed to initialize DirectX 12 adapter.” The Apple D3D12 backend was loaded, but `d3d12core.dll` was absent from the process. A separate owned probe successfully created a feature-level `12_0` device, showing that the broad error message did not by itself identify a physical GPU limitation.

GameToMac's loader patch attempts to load Wine's existing core module after a normal successful D3D12 load. The CrossOver experiment reproduced that behavior in process memory while preserving the original load result and last-error value. This removed the adapter error in the actual game, which then opened a window and remained at “Initializing.”

The core DLL is a Wine placeholder. Loading it does not implement Microsoft's Agility SDK or add GPU features; Apple's renderer still supplies graphics functionality. Microsoft describes the relationship between the D3D12 loader and core runtime in its [Agility SDK guide](https://devblogs.microsoft.com/directx/gettingstarted-dx12agility/). The test establishes the observed benefit of module residency in this configuration, without claiming to have identified every internal condition behind the original game error.

## Second blocker: native queue methods and Windows hook validation

The remaining stall had a concrete graphics interoperability cause. D3DMetal's `ExecuteCommandLists` and `Signal` methods were callable native Mach-O functions. An owned Direct/Copy queue probe executed those methods successfully, but Wine's Windows memory query classified their addresses as `MEM_FREE` with non-executable protection. Native code can exist outside Wine's Windows virtual-memory bookkeeping; that classification did not mean the underlying functions were unusable. See Microsoft's [VirtualQuery contract](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualquery) for the Windows-side properties being queried.

The installed Social Club D3D12 renderer's hook validation required committed, executable Windows memory. The stalled game's retained setup records showed four completed factory hooks followed by the first queue-method target, with later queue and swapchain targets absent and the renderer state cleaned up. Static control flow identified an abort at the first queue hook. Its numerical error code was not captured in the game, so the evidence should not be described as a directly observed return code.

The renderer's Present hook normally drives Social Club's UI callback. Failed graphics setup prevented that path from becoming active. A log line saying the overlay had hooked successfully was misleading here: the inspected caller emitted it without checking the setup return value.

The correction provides two genuine forwarding entries in Windows-managed executable memory and publishes them through the shared command-queue interface. They call the original native methods. This lets the overlay perform its ordinary graphics-hook setup; it does not fabricate memory-query results, force SDK readiness values, or change game completion conditions. The existing D3D12Core behavior remains part of the same loader-time callback.

## Validation

The correction was first exercised in owned, windowless fixtures, then in the actual game:

| Check | Observed result |
|---|---|
| Basic D3D12 loader cases | Normal load preserved; core resident; excluded load modes did not trigger the extra load; fresh baseline unchanged. |
| Forwarding entries | Reported as committed/executable by Wine; original queue methods remained callable. |
| Real MinHook test | Both queue hooks created and enabled; original-function trampolines worked; command submission, fence completion, call counts, and restoration passed. |
| Exact loader-time callback | Forwarding entries published and core resident before device creation; the same hook/fence checks passed. |
| Actual Rockstar launch | All eight graphics hooks enabled; queue, device, renderer, swapchain, and UI callback present. |
| Startup progress | SDK UI reached `FINISHED`; the previously observed startup loop was inactive and completion values matched. |
| User confirmation | Main menu or game window opened. No extended gameplay benchmark was performed. |
| Persistence checks | Debugger detached; checked game and runtime file hashes unchanged. No Wine rebuild or persistent bottle/registry changes were required. |

The tested callback contains **1,584 bytes** and has SHA-256 `b4a41423708b02cffeea00ad6300a70b66f52752fe4bc2d84403a71f3ee15837`. The source is [core_queue_callback.c](../memory_only/core_queue_callback.c); the normal entry point is [watch_gta_queue.py](../memory_only/watch_gta_queue.py). Changes expire with the game process and must be reapplied for a new launch. Path configuration does not relax the binary identity or ABI checks.

The native-loader calling convention is itself build-sensitive. An early fixture used the newer Wine declaration and failed; correcting it to the installed CrossOver ABI fixed the fixture. That is one reason the implementation remains pinned to **CrossOver 26.3, build 26.3.0.39832**, its **D3DMetal 3.0** components, and the recorded Rockstar game executable.

## Findings that were not fixes

Disabling MSync/ESync did not resolve the stall in the comparison run. An optional spatial-audio interface experiment caused an audio initialization failure in the game and was abandoned; it is not part of the successful queue correction. The game's spatial-audio flag also failed to clear the observed stall by itself.

Timestamp queries were unsupported in the examined D3DMetal 3.0 path: successful submissions and completed fences left timestamp readback sentinels unchanged. The examined GameToMac D3DMetal 4.0b2 paths had the same limitation, so it did not explain the difference or justify a timestamp patch. Microsoft's [D3D12 timing documentation](https://learn.microsoft.com/en-us/windows/win32/direct3d12/timing) describes the expected counter and calibration behavior. The final startup success required no timestamp correction.

The result does not establish that every GTA feature works, that Apple 4.0b2 is required, or that all GameToMac patches should be copied into CrossOver. It demonstrates two particular startup corrections on one pinned installation. This public report omits raw process captures, personal paths, launcher arguments, and account-bearing logs; proprietary game and runtime binaries are not redistributed.

## Lenny's Mod Loader: bootstrap and unresolved Enhanced tick

A subsequent investigation found that `version=native,builtin` already selected the intended DLLs: Rockstar Launcher mapped Wine's builtin `version.dll`, while GTA mapped the game-folder native proxy and the builtin implementation. GTA did not map `vfs.dll` or `EasyHookPatch.dll` in the inspected launch. Native-only is unnecessary for this observed selection and removes the fallback that the launcher needs. Wine checks environment overrides before application-specific and global registry settings; see the [Wine 11.0 load-order implementation](https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/ntdll/unix/loadorder.c).

The installed native proxy has SHA-256 `bc5e691f41729df02e3d777f7dd1937dfa1c62e763fa58c8084a944e3cdc47a7`. Its process-attach code obtains the executable path and searches, case-sensitively, for `GTA5.exe`. When that substring is absent, it returns success without arranging LML startup. `GTA5_Enhanced.exe` does not match. A successfully loaded proxy therefore does not establish that LML initialized.

The installed backend has SHA-256 `65c7ce43cbf08bf2206de7d1fffbb50c0e05e196efd1880ef2ccebf5c1d784cf`. Its `InitializeSafe` export explicitly recognizes `GTA5_Enhanced.exe` and selects Enhanced setup. Local archive comparison established that this backend came from the separate `vfs-test.zip` contents, while the proxy matches `lml_beta_0414.zip`. The test archive contains no replacement `version.dll`. Enhanced code in the backend cannot run when the older bootstrap never loads it; this does not establish that every LML release or every Enhanced game build is supported.

The original proxy first attempts to load `EasyHookPatch.dll`, then defers loading `vfs.dll` and calling `InitializeSafe` through a game import of `GetCurrentProcessId`. EasyHookPatch has its own initialization work. The deferred wrapper marks initialization attempted only after the call returns, so simply arming it in an arbitrary running, multithreaded game would require additional timing and reentrancy analysis. Loading the backend also creates or truncates `vfs.log` relative to the process working directory.

GTA statically imports `VERSION.dll`. This happens through Wine's internal dependency loader, rather than the kernelbase import intercepted by this project's D3D12 callback. Changing the proxy's filename check after its process-attach callback has returned does not replay its startup. The existing graphics watcher consequently does not fix this LML mismatch. The initial investigation was read-only; a separate memory-only bootstrap experiment followed.

A separate local prototype was subsequently built for the user's confirmed development backend. It attempts the EasyHookPatch preload and calls VFS initialization from a normal game process-ID API call, with an atomic guard set before loading either DLL. Recursive and concurrent callers keep receiving the ordinary process ID without re-entering initialization. The compatibility helper changes only process memory; running LML can still write LML's own logs and state files.

The exact 276-byte callback, SHA-256 `58f8ac95ab2973b33da6233ca7daaebcafec194b5b2e165f88bc4a5ffa7a84b0`, passed seven isolated cases with dummy dependencies: normal startup, reentrancy, concurrency, missing VFS, missing initialization export, optional preload failure, and preservation of the original API's last-error behavior. The concurrent case completed 16,003 calls with one initialization. These tests establish callback mechanics, not complete compatibility with the development DLL. The raw callback has no exception-unwind metadata; exceptions escaping through it remain untested.

The subsequent real-game trial loaded both EasyHookPatch and VFS. Read-only inspection confirmed one initialization attempt, a successful return from `InitializeSafe`, no recorded DLL/export errors, and debugger detachment. VFS logged its native pipe as ready, discovered Enhanced's vehicle metadata type, and recorded reaching the game after 61 seconds. The user confirmed entering Story Mode. The checked game, LML, and runtime DLL hashes remained unchanged. This establishes that the memory-only bootstrap ran, not that every LML subsystem or mod worked.

The development build's separate WebSocket endpoint, `ws://127.0.0.1:35855/beam`, was **not listening** during that run. Its ShowroomBridge loader is reached from the first `EnhancedTick`, which should emit `First tick, initializing GTA5 Enhanced` before attempting the bridge load. Neither that message nor a bridge-load result appeared, and no `ShowroomBridge.log` existed. The native pipe's readiness must not be confused with WebSocket readiness.

Bounded static inspection found no internal caller or callback registration for the exported `EnhancedTick`; the Enhanced setup worker does not call it. The installed module scan also found no other DLL/ASI referencing that export name. This supports a missing external tick integration, but does not identify the intended caller or exclude an ordinal-based lookup. That callback uses game-thread TLS and allocator state, so invoking it from an arbitrary worker or debugger would not establish the required integration. The bridge DLL is installed, and CrossOver includes Wine Mono; there is no evidence yet that a missing .NET runtime or a socket-bind failure caused this result. The unresolved step is getting the development build's intended tick integration to run.

A deeper audit found a concrete difference between startup branches. Legacy initialization walks a hook-registration list and installs a recurring callback that runs its tick handler. Enhanced initialization skips that hook setup, publishes seven API callbacks that do not include `EnhancedTick`, starts the pipe and metadata workers, and returns. The metadata worker terminates after setup rather than running a tick loop. Checks of references into the entire tick body, image relocations, initialization tables, and the first-tick log/flag found no alternative internal registration or inlined implementation. The first-tick flag begins set and is cleared only after its log message; the observed metadata-worker log shows that the earlier initialization gate passed.

The public VFS contained in the user's `lml_beta_0414.zip` also exports `EnhancedTick`, so this entry point predates the private bridge build. The installed `version.dll` calls only `InitializeSafe`; EasyHookPatch's reviewed worker patches EasyHook allocation behavior; NativeInterop's sole managed entry constructs `ModManager.Core.UnmanagedHost` and calls `Load`. None of those inspected paths establishes an Enhanced scheduler. Core's obfuscated dynamic behavior and an unidentified external host remain limitations of the static analysis. This evidence establishes that the memory-only bootstrap is only the initial loading step. A particular missing plugin, an available replacement `version.dll`, and a working recurring game-thread integration have not yet been demonstrated.

## Optional standalone executable profile

The separate `standalone-compat` branch adds a direct CrossOver launch path for the user's supplied `GTA5_Enhanced.exe`, SHA-256 `827538303fa80cd65a81dabfe67f5ffc4c8c2f90a3bad2d01c0a38099cdb1cbc`. It leaves the original Rockstar profile pinned to SHA-256 `b48e80f5fc899eb89e5dcc9f3b68e9551cc8dcfac78b2a940b207e569e9987ea`. No replacement executable is distributed or generated.

Static comparison found that all 685 named imports retain their symbols and slots, including the process-ID slot used by the optional bootstrap. D3D12 SDK exports also match. However, the supplied file has an empty IAT directory and prefilled absolute import pointers. The new profile therefore checks the loaded game identity and requires the process-ID slot to resolve into the fingerprinted, mapped Wine kernel32 implementation, with matching routine bytes, before any allocation or memory change. This verifies actual loader rebinding rather than trusting those stored pointers.

The direct-launch wrapper accepts only the optional `--lml` switch, uses the existing bottle and fixed game path, and rejects an already running game. A separate watcher supports manual launching. Graphics/core compatibility remains the default; the LML bootstrap is explicit. Neither profile adds an `EnhancedTick` scheduler, and retained imports or strings do not establish full behavioral equivalence between executable builds. Isolated tests exercise hash rejection before launch, command scope, profile isolation, and the mapped-import guard; a successful installation marker alone is not proof of gameplay.

The first local standalone trial passed the runtime and mapped-import checks, installed both hooks, and detached successfully. Post-run fingerprints matched. Module inspection found D3D12Core, EasyHookPatch, and VFS; the fresh VFS log recorded initialization and pipe readiness. At the last observation, no first Enhanced tick, reached-game marker, or TCP listener was present, and the game process remained running. A follow-up read-only debugger inspection timed out during attachment before its inspection code ran; process-state checks subsequently showed the game running. Main-menu or gameplay success for this profile remains unconfirmed. The standalone helper's 12 isolated Python tests passed, including rejection of the candidate's stored import pointer and a mismatched mapped PE identity.
