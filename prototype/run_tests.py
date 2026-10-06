#!/usr/bin/env python3
"""Build and test the self-owned loader fixture against the pinned CrossOver.

Build only:
    python3 prototype/run_tests.py --build-only
Build and run the fixed seven-case suite:
    python3 prototype/run_tests.py
Use a relocated copy of the same verified Zig executable:
    python3 prototype/run_tests.py --zig /path/to/zig

CrossOver, bottle name, executable names, and test cases are fixed. This
script builds only core_compat.c and loader_probe.c; it never rebuilds Wine,
attaches to another process, or accepts a target PID. Each case starts a new
fixture process in prototype/bottles/GTMLoaderPrototype. A missing bottle is
created with CrossOver's win10_64 template. Existing bottles are retained.

Logs and results.json are retained under prototype/logs/<UTC-run-id>/.
The optout case checks the probe's startup guard; it does not independently
exercise the helper's environment guard. Selected runtime hashes are checked
before any build/launch and again on exit, including after a test failure.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import uuid


HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gtm_paths", HERE.parent / "memory_only/paths.py")
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)
APP, CX = paths.APP, paths.CX
BOTTLES = HERE / "bottles"
BOTTLE_NAME = "GTMLoaderPrototype"
BOTTLE = BOTTLES / BOTTLE_NAME
DESTINATION = BOTTLE / "drive_c/GTMProbe"
ARTIFACTS = HERE / "artifacts"
DEFAULT_ZIG = paths.ZIG
ZIG_SHA256 = "1c5f706db0ed6d55451940f31dc05a6177d05696bc1fa5d853696d04c718b523"
EXPECTED_VERSION = ("26.3", "26.3.0.39832")
RUNTIME_SHA256 = {
    "lib/wine/x86_64-unix/ntdll.so": "ab32585d767ae631154235861a2e0e8ea61bd09267a2dd341a439e3e8ed071fc",
    "lib/wine/x86_64-windows/ntdll.dll": "6dff64c00793ce92124f1316985c63783f539f26b392975c70f57637458d2387",
    "lib/wine/x86_64-windows/kernelbase.dll": "ca72a76ce5770996cc9cba6989031a79eb1e63b6d9fca90d59eebab22e39191b",
    "lib/wine/x86_64-windows/d3d12core.dll": "c0952bf63464d11f7be221050e31d07c84dab3fa4d9a60e249245f05f79fda8a",
    "lib64/apple_gptk/wine/x86_64-windows/d3d12.dll": "bbda1c4e94ee70255c528c5689b28333ca9bece2d755ede7c4197977a534704f",
}
CASES = (
    ("baseline", "GTMLoaderProbe.exe", (), "1", 0),
    ("patched", "GTMLoaderProbe.exe", ("--patched",), "1", 0),
    ("datafile", "GTMLoaderProbe.exe", ("--patched", "--datafile"), "1", 0),
    ("noresolve", "GTMLoaderProbe.exe", ("--patched", "--noresolve"), "1", 0),
    ("negative-name", "GTMLoaderProbeNegative.exe", ("--patched",), "1", 11),
    ("optout", "GTMLoaderProbe.exe", ("--patched",), "0", 2),
    ("baseline-after", "GTMLoaderProbe.exe", (), "1", 0),
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        return digest.hexdigest()


def runtime_state():
    with (APP / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    return {
        "version": [info.get("CFBundleShortVersionString"), info.get("CFBundleVersion")],
        "sha256": {name: sha256(CX / name) for name in RUNTIME_SHA256},
    }


def check_runtime(state):
    require(tuple(state["version"]) == EXPECTED_VERSION, "Unexpected CrossOver version; refusing this fixed-build test.")
    for name, expected in RUNTIME_SHA256.items():
        require(state["sha256"][name] == expected, f"Runtime fingerprint mismatch: {name}")


def local_directory(path):
    require(path.resolve().is_relative_to(HERE), f"Fixture path escapes prototype directory: {path}")
    path.mkdir(parents=True, exist_ok=True)


def fixture_environment():
    # Retain the real HOME; remove inherited Wine/loader settings rather than
    # accidentally reusing another bottle or selecting another DLL backend.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CX_", "WINE", "GTM_", "DYLD_", "LD_"))}
    env.update(CX_BOTTLE_PATH=str(BOTTLES), CX_BOTTLE=BOTTLE_NAME,
               CX_GRAPHICS_BACKEND="d3dmetal",
               WINEDEBUG="-all", ZIG_LOCAL_CACHE_DIR=str(HERE / ".zig-cache"),
               ZIG_GLOBAL_CACHE_DIR=str(HERE / ".zig-global-cache"))
    return env


def command(label, argv, env, log_directory, records, timeout=180):
    log = log_directory / (label + ".log")
    record = {"label": label, "argv": [str(arg) for arg in argv], "log": str(log)}
    records.append(record)
    try:
        completed = subprocess.run(record["argv"], cwd=HERE, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=timeout)
        output = completed.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        record["exit_code"] = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        record["timed_out"] = True
        log.write_text(output, encoding="utf-8")
        raise RuntimeError(f"{label} timed out; see {log}") from exc
    log.write_text(output, encoding="utf-8")
    return completed.returncode, output


def snapshot_modules(output):
    snapshots, current = {}, None
    for line in output.splitlines():
        match = re.fullmatch(r"SNAPSHOT label=(\w+) iteration=1", line)
        if match:
            current = match[1]
            require(current not in snapshots, f"Duplicate snapshot: {current}")
            snapshots[current] = {}
        match = re.match(r"MODULE name=(\S+) resident=([01])(?: |$)", line)
        if match and current:
            snapshots[current][match[1]] = int(match[2])
    return snapshots


def validate_case(name, output, code, expected_code):
    require(code == expected_code, f"Expected exit {expected_code}, got {code}")
    if name == "optout":
        require("Opt-in required: GTM_LOADER_PROBE=1" in output, "Missing optout rejection")
        require("INSTALL " not in output and "HOOK " not in output, "Optout reached helper installation")
        return {"guard": "probe startup opt-in"}
    snapshots = snapshot_modules(output)
    require(snapshots.get("initial", {}).get("d3d12core.dll") == 0, "Initial core is not absent")
    if name == "negative-name":
        require("INSTALL result=5 core_load_attempts=0" in output, "Wrong-name fixture was not rejected")
        require("COMPLETE result=11;" in output, "Negative fixture did not complete its expected failure")
        require("HOOK " not in output and "REMOVE " not in output, "Rejected fixture reached active hook")
        return {"guard": "helper executable-name restriction", "snapshots": snapshots}

    require("COMPLETE result=0; no GPU device was created." in output, "Missing successful completion")
    expected_core = int(name == "patched")
    for label in ("before_load", "after_load", "after_unload"):
        expected = 0 if label == "before_load" else expected_core
        require(snapshots.get(label, {}).get("d3d12core.dll") == expected,
                f"Unexpected core residency at {label}")
    require(snapshots["after_load"].get("d3d12.dll") == int(name != "datafile"), "Unexpected target residency")
    load = re.search(r"^LOAD name=d3d12\.dll handle=([0-9a-fA-Fx]+) last_error=(\d+) flags=(\d+)$", output, re.M)
    require(load is not None and int(load[1], 16) != 0, "Target DLL load did not return a handle")
    expected_flags = 1 if name == "noresolve" else (2050 if name == "datafile" else 2048)
    require(int(load[3]) == expected_flags, "Wrong target LoadLibraryExW flags")
    if name in ("baseline", "patched", "baseline-after"):
        require(int(load[2]) == 0, "Normal target load did not preserve the expected zero last-error value")
    if name in ("patched", "datafile", "noresolve"):
        count = expected_core
        require("INSTALL result=0 core_load_attempts=0" in output, "Helper did not install cleanly")
        require(f"PATCH_RESULT core_resident={expected_core} core_load_attempts={count}" in output, "Wrong hook result")
        require(f"REMOVE result=0 core_load_attempts={count}" in output, "Helper did not remove cleanly")
        require(snapshots.get("after_remove", {}).get("d3d12core.dll") == expected_core, "Removal changed core residency")
    else:
        require("INSTALL " not in output and "REMOVE " not in output, "Baseline installed a helper")
    if name != "patched":
        require("HOOK " not in output, "An excluded or baseline case invoked the hook")
    return {"snapshots": snapshots, "load_last_error": int(load[2]), "load_flags": int(load[3])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", type=Path, default=DEFAULT_ZIG)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    logs = HERE / "logs" / run_id
    local_directory(logs)
    results = {"run_id": run_id, "build_only": args.build_only, "commands": [], "tests": []}
    failed = False
    try:
        results["runtime_before"] = runtime_state()
        check_runtime(results["runtime_before"])
        require(args.zig is not None, "Set GTM_ZIG, use --zig, or put the pinned Zig compiler on PATH.")
        zig = args.zig.expanduser().resolve(strict=True)
        require(sha256(zig) == ZIG_SHA256, "Zig fingerprint mismatch; refusing to execute compiler.")
        results["zig"] = {"path": str(zig), "sha256": ZIG_SHA256}
        sources = (HERE / "core_compat.c", HERE / "loader_probe.c")
        results["source_sha256"] = {p.name: sha256(p) for p in sources}
        local_directory(ARTIFACTS)
        env = fixture_environment()
        for source, target, extra, optimization in (
                (sources[0], "GTMCoreCompat.dll", ["-shared"], "-O1"),
                (sources[1], "GTMLoaderProbe.exe", [], "-O0")):
            destination = ARTIFACTS / target
            require(not destination.is_symlink(), f"Refusing symlink output: {destination}")
            argv = [zig, "cc", "-target", "x86_64-windows-gnu", "-std=c11", optimization, "-g",
                    "-Wall", "-Wextra", "-Werror", *extra, source, "-o", destination]
            code, _ = command("build-" + source.stem, argv, env, logs, results["commands"], timeout=600)
            require(code == 0, f"Build failed: {source.name}")
        require(results["source_sha256"] == {p.name: sha256(p) for p in sources}, "Sources changed during build")
        results["artifact_sha256"] = {name: sha256(ARTIFACTS / name)
                                      for name in ("GTMCoreCompat.dll", "GTMLoaderProbe.exe")}
        if not args.build_only:
            local_directory(BOTTLES)
            require(BOTTLE.resolve().is_relative_to(BOTTLES), "Bottle path escapes isolated root")
            if not BOTTLE.exists():
                argv = [CX / "bin/cxbottle", "--bottle", BOTTLE_NAME,
                        "--create", "--template", "win10_64",
                        "--description", "Self-owned loader prototype; no games",
                        "--param", "Bottle:MenuMode=ignore", "--param", "Bottle:AssocMode=ignore"]
                code, _ = command("create-bottle", argv, env, logs, results["commands"], timeout=300)
                require(code == 0, "Isolated bottle creation failed")
            require((BOTTLE / "cxbottle.conf").is_file(), "Existing bottle is incomplete; it was retained for inspection")
            require(DESTINATION.resolve().is_relative_to(BOTTLE.resolve()), "Fixture destination escapes isolated bottle")
            local_directory(DESTINATION)
            for name in ("GTMCoreCompat.dll", "GTMLoaderProbe.exe", "GTMLoaderProbeNegative.exe"):
                destination = DESTINATION / name
                require(not destination.is_symlink(), f"Refusing symlink destination: {destination}")
                source = ARTIFACTS / ("GTMLoaderProbe.exe" if "Negative" in name else name)
                shutil.copy2(source, destination)
            for name, executable, options, optin, expected in CASES:
                argv = [CX / "bin/wine", "--debugmsg=-all", "--bottle", BOTTLE_NAME,
                        "--cx-app", "C:\\GTMProbe\\" + executable, "--", *options, "--no-wait"]
                code, output = command(name, argv, {**env, "GTM_LOADER_PROBE": optin}, logs, results["commands"])
                record = {"name": name, "exit_code": code, "expected_exit_code": expected}
                results["tests"].append(record)
                try:
                    record.update(validate_case(name, output, code, expected))
                    record["passed"] = True
                except RuntimeError as exc:
                    record.update(passed=False, error=str(exc))
                    failed = True
                print(f"{'PASS' if record['passed'] else 'FAIL'} {name}", flush=True)
            by_name = {test["name"]: test for test in results["tests"]}
            normal = [by_name[name] for name in ("baseline", "patched", "baseline-after")]
            if all(test["passed"] for test in normal):
                same_error = len({test["load_last_error"] for test in normal}) == 1
                results["normal_load_last_error_matches_baselines"] = same_error
                require(same_error, "Patched normal-load last error differs from the two baseline runs")
    except KeyboardInterrupt:
        results["error"] = "Interrupted"
        failed = True
    except Exception as exc:
        results["error"] = str(exc)
        failed = True
    finally:
        try:
            results["runtime_after"] = runtime_state()
            check_runtime(results["runtime_after"])
            unchanged = results.get("runtime_before") == results["runtime_after"]
            results["checked_runtime_files_unchanged"] = unchanged
            require(unchanged, "Checked CrossOver runtime files changed during this run")
        except Exception as exc:
            results["runtime_verification_error"] = str(exc)
            failed = True
        results["passed"] = not failed
        (logs / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Results: {logs / 'results.json'}")
    if failed:
        print(results.get("error", results.get("runtime_verification_error", "One or more test assertions failed.")), file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
