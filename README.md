# GTA V Enhanced on macOS: CrossOver startup compatibility

An experimental, version-pinned compatibility fix for the Rockstar edition of GTA V Enhanced under CrossOver on Apple silicon. It cleared the DirectX 12 adapter error and the subsequent “Initializing” stall in the tested installation. The user confirmed that the main menu or game opened; extended gameplay and stability have not been benchmarked.

The fix changes only the running game's memory. It loads Wine's existing `d3d12core.dll` and supplies Windows-visible forwarding entries for two D3DMetal command-queue methods, allowing Social Club's normal graphics hooks to initialize. It requires no Wine rebuild or persistent changes to game files, CrossOver files, registry, or bottle configuration. Closing the game removes the changes.

See [the technical analysis](docs/PUBLIC_ANALYSIS.md) for the evidence, GameToMac's role in the investigation, and remaining limitations.

## Tested scope

| Component | Validated scope |
|---|---|
| Host | Apple silicon Mac using Rosetta; no cross-model or macOS-version test matrix. |
| CrossOver | **26.3**, build **26.3.0.39832**, with its bundled **D3DMetal 3.0** backend. |
| Game | Purchased Rockstar Games Launcher edition of **GTA V Enhanced**, at the executable hash pinned in the source. |
| Mode | Story Mode startup. GTA Online is outside this project's scope. |
| Outcome | All eight overlay graphics hooks enabled; SDK UI reached `FINISHED`; the observed startup loop exited; user confirmed a menu or game window. |

The guards intentionally reject different binary builds. Relocating an installation does not make a different CrossOver or GTA version compatible. The Steam edition, Intel Macs, other graphics backends, and updated binaries have not been validated.

The successful run also used the game's `-nobattleye -disableSpatialAudioClient` launch arguments for Story Mode and a Wine server started with `WINEMSYNC=0 WINEESYNC=0`. The watcher does not change these settings. Earlier tests showed that the audio argument and synchronization settings alone did not fix the initialization stall.

## Run

Use a physical Mac with the game and Rockstar Games Launcher already installed in CrossOver. Python 3 and Apple's command-line tools must be available, including `xcrun lldb`; the debugger must be permitted to attach to the local game process. This is a launch helper, not an installer for CrossOver or GTA.

The checkout location is detected automatically. CrossOver is located in the current user's Applications directory or the system Applications directory. The default bottle is named `Rockstar Games Launcher`. Optional environment overrides are:

```sh
export GTM_CROSSOVER_APP="/path/to/CrossOver.app"
export GTM_BOTTLE_PATH="/path/to/Rockstar Games Launcher"
```

The game must retain the expected Windows installation path, `C:\Program Files\Rockstar Games\Grand Theft Auto V Enhanced\GTA5_Enhanced.exe`, inside that bottle. Runtime and game hash checks still apply when paths are overridden.

From the repository root:

```sh
python3 memory_only/watch_gta_queue.py
```

Wait for the fresh-launch message, then press **Play** in Rockstar Games Launcher. An existing GTA process is excluded; close it before starting a new one. The watcher handles one fresh launch, verifies the fixed target and runtime, applies the memory-only compatibility change, and detaches. Run the watcher again before each fresh game launch.

Local diagnostic records are written under `memory_only/logs/`. A successful watcher result confirms installation and detachment, not gameplay quality. A hash mismatch or an attachment failure is a failed run; do not remove the guards to make an incompatible build pass.

## Source and distribution

The watcher uses [the loader callback source](memory_only/core_queue_callback.c) and its matching project-built callback artifact. Both included callback binaries can be reproduced using the pinned **Zig 0.17.0 macOS ARM64** compiler:

```sh
python3 memory_only/build_callbacks.py --zig /path/to/zig --check
```

`--check` compiles and verifies byte-for-byte reproduction without replacing the binaries. Omit it to rebuild the artifacts. `GTM_ZIG` or `zig` on `PATH` can select the compiler instead of `--zig`; its executable hash must match. This builds only the project's callback code, not Wine. The owned loader and graphics fixtures under `prototype/` document how the behavior was checked before the game test.

No GameToMac, CrossOver, Wine runtime, Apple D3DMetal, GTA, or Social Club binaries are distributed here. Those components must come from the user's own installation. Private diagnostic logs and account information are not part of this repository. See [third-party notices](THIRD_PARTY_NOTICES.md) for the owned fixture's MinHook dependency. This is an independent compatibility investigation, not an official Rockstar, CodeWeavers, Apple, or GameToMac project.
