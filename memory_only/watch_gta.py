#!/usr/bin/env python3
"""Install the fixed in-memory Wine loader hook in one fresh Rockstar GTA launch.

Usage: python3 memory_only/watch_gta.py

This watcher accepts no target, PID, bottle, or command arguments. It reads
only ps PID/comm fields, never process arguments or environments. The game
may already be at its error window: that PID is excluded while the watcher
waits for a fresh process. The user closes it and launches through Rockstar
after the waiting message. Only project-local private logs are
written; no game, Wine, or bottle files/configuration are changed.

install_loader_hook.py owns in-process validation, a bounded operation, and detach
in a finally block. It must print exactly `GTM_HOOK_RESULT success` only
after successful verification and detach. A Windows comm value alone does
not identify a bottle; the LLDB helper must independently validate its
target before making any in-memory change.
"""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


_spec = importlib.util.spec_from_file_location("gtm_paths", Path(__file__).resolve().with_name("paths.py"))
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)
ROOT, HERE = paths.ROOT, paths.HERE
HELPER = HERE / "install_loader_hook.py"
INSTALLER_ENTRY = "install_game"
BOTTLE, GAME, GAME_COMM = paths.BOTTLE, paths.GAME, paths.GAME_COMM
GAME_SHA256 = "b48e80f5fc899eb89e5dcc9f3b68e9551cc8dcfac78b2a940b207e569e9987ea"
WAIT_SECONDS = 600
LLDB_SECONDS = 30
SUCCESS = "GTM_HOOK_RESULT success"
FAILURE = "GTM_HOOK_RESULT failure"
FIXTURE_VALIDATED = True  # Owned PID 88525 continued after debugger exit with core resident.


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def runtime_checks():
    source = ROOT / "prototype/run_tests.py"
    spec = importlib.util.spec_from_file_location("gtm_pinned_runtime_checks", source)
    require(spec is not None and spec.loader is not None, "Cannot import pinned runtime checks")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def matching_pids():
    # The full table remains in memory only; neither args nor environments are read.
    result = subprocess.run(["/bin/ps", "-axo", "pid=,comm="],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=5, check=True)
    matches = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) == 2 and fields[0].isdigit() and fields[1] == GAME_COMM:
            matches.append(int(fields[0]))
    return matches


def wait_for_fresh_game():
    existing = matching_pids()
    require(len(existing) <= 1, "Multiple existing GTA processes; refusing to select one")
    if existing:
        print(f"Existing GTA PID {existing[0]} is excluded. Close its error window and relaunch through Rockstar.", flush=True)
    print(f"Waiting up to {WAIT_SECONDS}s for a fresh GTA launch through Rockstar...", flush=True)
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        candidates = matching_pids()
        require(len(candidates) <= 1, "Multiple matching GTA processes; refusing to select one")
        if candidates and candidates[0] not in existing:
            return candidates[0]
        time.sleep(0.1)
    raise RuntimeError(f"No matching fresh GTA launch appeared within {WAIT_SECONDS} seconds")


def stop_debugger(client, record):
    """Interrupt the helper so its finally block can detach; never signal GTA."""
    if client.poll() is not None:
        return
    client.send_signal(signal.SIGINT)
    record["debugger_interrupt_sent"] = True
    try:
        client.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    record["forced_debugger_shutdown"] = True
    client.terminate()
    try:
        client.wait(timeout=5)
    except subprocess.TimeoutExpired:
        client.kill()
        client.wait(timeout=5)
    print("LLDB did not exit after interruption; detach is unconfirmed. Inspect the private log before relaunching GTA.",
          file=sys.stderr, flush=True)


def run_debugger(pid, directory, record):
    require(matching_pids() == [pid], "Target identity changed before debugger attachment")
    argv = ["/usr/bin/xcrun", "lldb", "--batch", "--no-use-colors", "--no-lldbinit", "--attach-pid", str(pid),
            "-o", "command script import " + json.dumps(str(HELPER)),
            "-o", f"script {HELPER.stem}.{INSTALLER_ENTRY}(lldb.debugger)"]
    record["lldb_argv"] = argv
    log = directory / "lldb.log"
    record["lldb_log"] = str(log)
    client = None
    with log.open("x", encoding="utf-8") as output:
        try:
            client = subprocess.Popen(argv, cwd=HERE, stdin=subprocess.DEVNULL,
                                      stdout=output, stderr=subprocess.STDOUT,
                                      start_new_session=True)
            try:
                client.wait(timeout=LLDB_SECONDS)
            except subprocess.TimeoutExpired:
                record["lldb_timed_out"] = True
                raise RuntimeError(f"LLDB exceeded its {LLDB_SECONDS}-second limit")
        finally:
            if client is not None:
                stop_debugger(client, record)
                record["lldb_exit_code"] = client.poll()
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    record["hook_and_detach_confirmed"] = (lines.count(SUCCESS) == 1 and FAILURE not in lines)
    require(record["lldb_exit_code"] == 0, "LLDB reported failure; inspect its private log")
    require(record["hook_and_detach_confirmed"], "Missing unambiguous hook-and-detach success marker")


def main():
    require(len(sys.argv) == 1, "This fixed-target watcher accepts no arguments")
    require(FIXTURE_VALIDATED,
            "Watcher disabled: the fixed in-memory loader hook needs a successful owned-fixture test.")
    require(Path(__file__).resolve() == HERE / "watch_gta.py", "Watcher must run from its fixed project path")
    require(HERE.resolve() == HERE and not HERE.is_symlink(), "Unexpected project directory indirection")
    os.umask(0o077)
    logs = HERE / "logs"
    require(not logs.is_symlink(), "Log directory must not be a symlink")
    logs.mkdir(mode=0o700, exist_ok=True)
    logs.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
    directory = Path(tempfile.mkdtemp(prefix=stamp, dir=logs))
    record = {"game": str(GAME), "game_comm": GAME_COMM, "passed": False}
    checks = None
    try:
        require(HELPER.is_file() and not HELPER.is_symlink(), "The fixed LLDB helper is missing or is a symlink")
        checks = runtime_checks()
        record["runtime_before"] = checks.runtime_state()
        checks.check_runtime(record["runtime_before"])
        record["game_sha256_before"] = sha256(GAME)
        require(record["game_sha256_before"] == GAME_SHA256, "Game fingerprint changed; refusing this fixed-build operation")
        record["helper_sha256"] = sha256(HELPER)
        pid = wait_for_fresh_game()
        record["pid"] = pid
        print(f"Matched fresh GTA PID {pid}; installing the fixed in-memory Wine loader hook.", flush=True)
        require(sha256(HELPER) == record["helper_sha256"], "LLDB helper changed while waiting")
        run_debugger(pid, directory, record)
        record["passed"] = True
    except KeyboardInterrupt:
        record["error"] = "Interrupted"
    except Exception as exc:
        record["error"] = str(exc)
    finally:
        if checks is not None:
            try:
                record["runtime_after"] = checks.runtime_state()
                checks.check_runtime(record["runtime_after"])
                require(record["runtime_after"] == record["runtime_before"], "Checked runtime files changed")
                record["game_sha256_after"] = sha256(GAME)
                require(record["game_sha256_after"] == GAME_SHA256, "Game file changed")
                record["checked_files_unchanged"] = True
            except Exception as exc:
                record["verification_error"] = str(exc)
                record["passed"] = False
        with (directory / "results.json").open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
    print("RESULT: " + ("loader hook installed and debugger detached; game outcome still requires verification" if record["passed"] else "failed"), flush=True)
    if not record["passed"]:
        print(record.get("error", record.get("verification_error", "Unknown failure")), file=sys.stderr)
    print(f"Private results: {directory / 'results.json'}", flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
