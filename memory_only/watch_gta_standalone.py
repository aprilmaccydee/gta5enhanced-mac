#!/usr/bin/env python3
"""Watch one manual launch of the explicitly pinned standalone game build.

Usage: python3 memory_only/watch_gta_standalone.py [--lml]
For a fixed direct CrossOver launch, use launch_gta_standalone.py instead.
The default selects graphics/core compatibility only. --lml explicitly adds
the pinned development LML bootstrap; it does not supply an EnhancedTick caller.
This entry point does not change the Rockstar profile.
"""
import argparse
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
from game_profiles import KERNEL32_SHA256, game_hash, validate_game_file

HERE = Path(__file__).resolve().parent
PROFILE = "standalone"
GAME_SHA256 = game_hash(PROFILE)
INSTALLER_ENTRY = "install_queue_game_standalone"
LML_INSTALLER_ENTRY = "install_lml_game_standalone"
LML_HASHES = {
    "version.dll": "bc5e691f41729df02e3d777f7dd1937dfa1c62e763fa58c8084a944e3cdc47a7",
    "vfs.dll": "65c7ce43cbf08bf2206de7d1fffbb50c0e05e196efd1880ef2ccebf5c1d784cf",
    "EasyHookPatch.dll": "4a4cbce1efc158739da9d310362fe0365a3c8cec6d57f264a0dbe4722f1fec38",
}
NATIVE_RUNTIME_HASHES = {
    "lib64/apple_gptk/external/libd3dshared.dylib":
        "5131e631eee8b542eadf48f4df9fd662d9aeeb59139137e0e6e14047dc434995",
    "lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal":
        "05a7beaed4494a4f5f53d3f626a82fffc3b70146436a908b7048a0632a49e1a8",
}


def launch_environment(watch):
    """Select the existing bottle/backend for this launch, without saving settings."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CX_", "WINE", "GTM_", "DYLD_", "LD_"))}
    env.update(CX_BOTTLE_PATH=str(watch.BOTTLE.parent), CX_BOTTLE=watch.BOTTLE.name,
               CX_GRAPHICS_BACKEND="d3dmetal", WINEDEBUG="-all")
    return env


def launch_command(watch):
    """Fixed executable and tested Story Mode flags; no caller-supplied arguments."""
    return [str(watch.paths.CX / "bin/wine"), "--debugmsg=-all", "--bottle",
            watch.BOTTLE.name, "--cx-app", watch.GAME_COMM, "--",
            "-nobattleye", "-disableSpatialAudioClient"]


def wait_for_game(watch, direct_launch):
    validate_game_file(watch.GAME, PROFILE)
    existing = watch.matching_pids()
    watch.require(len(existing) <= 1, "Multiple matching games; refusing to select one")
    child = None
    if direct_launch:
        watch.require(not existing, "Close the existing game before a standalone direct launch")
        watch.require((watch.BOTTLE / "cxbottle.conf").is_file(), "Existing CrossOver bottle required")
        print("Launching the pinned standalone game through CrossOver...", flush=True)
        # Game output is discarded: the compatibility logs contain no game stdout,
        # process command lines, environments, or inherited authentication arguments.
        child = subprocess.Popen(launch_command(watch), cwd=watch.GAME.parent,
                                 env=launch_environment(watch), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
    else:
        if existing:
            print(f"Existing GTA PID {existing[0]} is excluded. Close it and launch a fresh standalone game.",
                  flush=True)
        print(f"Waiting up to {watch.WAIT_SECONDS}s for a fresh standalone launch...", flush=True)
    deadline = time.monotonic() + watch.WAIT_SECONDS
    while time.monotonic() < deadline:
        candidates = watch.matching_pids()
        watch.require(len(candidates) <= 1, "Multiple matching games; refusing to select one")
        if candidates and candidates[0] not in existing:
            validate_game_file(watch.GAME, PROFILE)
            return candidates[0]
        if child is not None:
            status = child.poll()
            if status is not None and status != 0:
                raise RuntimeError(f"CrossOver direct launch exited with status {status}")
        time.sleep(0.1)
    raise RuntimeError("No fresh matching standalone game appeared before the wait limit")


def configured_watcher(direct_launch=False, lml=False):
    """Use an isolated module instance; importing this file cannot alter the default."""
    spec = importlib.util.spec_from_file_location("gtm_standalone_watch_base", HERE / "watch_gta.py")
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    watch.GAME_SHA256 = GAME_SHA256
    watch.INSTALLER_ENTRY = LML_INSTALLER_ENTRY if lml else INSTALLER_ENTRY
    if lml:
        watch.HELPER = HERE / "install_lml_bootstrap.py"
    base_checks = watch.runtime_checks
    base_debugger = watch.run_debugger

    def runtime_checks():
        checks = base_checks()
        checks.RUNTIME_SHA256.update(NATIVE_RUNTIME_HASHES)
        checks.RUNTIME_SHA256["lib/wine/x86_64-windows/kernel32.dll"] = KERNEL32_SHA256
        if lml:
            checks.RUNTIME_SHA256.update({str(watch.GAME.parent / name): digest
                                          for name, digest in LML_HASHES.items()})
        return checks

    def run_debugger(pid, directory, record):
        record["profile"] = PROFILE
        record["direct_launch_requested"] = direct_launch
        record["lml_bootstrap_requested"] = lml
        return base_debugger(pid, directory, record)

    watch.runtime_checks = runtime_checks
    watch.wait_for_fresh_game = lambda: wait_for_game(watch, direct_launch)
    watch.run_debugger = run_debugger
    return watch


def main(direct_launch=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lml", action="store_true",
                        help="Also start the pinned dev LML bootstrap; tick integration remains separate")
    args = parser.parse_args()
    watch = configured_watcher(direct_launch, args.lml)
    definitions = ast.parse(watch.HELPER.read_text(encoding="utf-8"))
    watch.require(any(isinstance(node, ast.FunctionDef) and node.name == watch.INSTALLER_ENTRY
                      for node in definitions.body),
                  "The standalone installer profile has not been integrated; no launch or attachment performed")
    # The shared fixed-target runner accepts no arguments; this entry point has
    # already parsed its sole explicit profile option without forwarding it.
    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0]]
        return watch.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
