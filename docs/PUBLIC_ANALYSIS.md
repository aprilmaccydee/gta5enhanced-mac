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
