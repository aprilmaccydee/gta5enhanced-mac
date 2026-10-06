#!/usr/bin/env python3
"""Build/run only GTMQueueHookProbe in the existing isolated fixture bottle.

Usage: python3 prototype/graphics/run_queue_hook_probe.py [--build-only] [--exact-callback]
The executable launches from its project Z: path. No bottle configuration,
game files, or installed runtime files are changed. Vendored MinHook hashes,
the compiler, and the installed CrossOver renderer/runtime are checked first.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("loader_fixture", HERE.parent / "run_tests.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
VENDOR = HERE / "vendor/minhook"


def invoke(label, args, env, timeout):
    args = [str(arg) for arg in args]
    record = {"argv": args}
    try:
        result = subprocess.run(args, cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout)
        output = result.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
        record["exit_code"] = result.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", errors="replace")
        record["timed_out"] = True
    (HERE / (label + ".log")).write_text(output)
    print(label + ": " + json.dumps(record), flush=True)
    return record, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--exact-callback", action="store_true")
    args = parser.parse_args()
    state = fixture.runtime_state()
    fixture.check_runtime(state)
    fixture.require(fixture.DEFAULT_ZIG is not None,
                    "Set GTM_ZIG or put the pinned Zig compiler on PATH.")
    fixture.require(fixture.sha256(fixture.DEFAULT_ZIG) == fixture.ZIG_SHA256,
                    "Unexpected compiler fingerprint")
    fixture.require((fixture.BOTTLE / "cxbottle.conf").is_file(), "Isolated bottle missing")
    manifest_path = VENDOR / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    fixture.require(manifest["repository"] == "https://github.com/TsudaKageyu/minhook" and
                    manifest["ref"] == "v1.3.4", "Unexpected MinHook revision")
    for item in manifest["files"]:
        path = VENDOR / item["path"]
        fixture.require(path.resolve().is_relative_to(VENDOR.resolve()) and
                        fixture.sha256(path) == item["sha256"], "MinHook source mismatch")
    metal = fixture.CX / "lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal"
    # Pinned separately from fixture.runtime_state, which covers the PE renderer.
    metal_hash = fixture.sha256(metal)
    expected_metal = "05a7beaed4494a4f5f53d3f626a82fffc3b70146436a908b7048a0632a49e1a8"
    fixture.require(metal_hash == expected_metal, "Native renderer fingerprint changed")
    glue = fixture.CX / "lib64/apple_gptk/external/libd3dshared.dylib"
    glue_hash = fixture.sha256(glue)
    fixture.require(glue_hash == "5131e631eee8b542eadf48f4df9fd662d9aeeb59139137e0e6e14047dc434995",
                    "Native graphics dispatch fingerprint changed")
    source = HERE / "queue_hook_probe.c"
    binary = HERE / ("GTMQueueCallbackProbe.exe" if args.exact_callback else "GTMQueueHookProbe.exe")
    prefix = "queue-callback" if args.exact_callback else "queue-hook"
    env = fixture.fixture_environment()
    env["GTM_QUEUE_HOOK_PROBE"] = "1"
    env["ZIG_LOCAL_CACHE_DIR"] = str(HERE / ".zig-cache")
    env["ZIG_GLOBAL_CACHE_DIR"] = str(HERE / ".zig-global-cache")
    results = {"purpose": "Owned queue forwarding/MinHook/restore fixture; no target process access",
               "runtime_before": state, "native_renderer_sha256": metal_hash,
               "native_dispatch_sha256": glue_hash,
               "source_sha256": fixture.sha256(source),
               "minhook_manifest_sha256": fixture.sha256(manifest_path),
               "build_only": args.build_only}
    if args.exact_callback:
        payload = HERE.parent.parent / "memory_only/core_queue_callback.bin"
        expected = "b4a41423708b02cffeea00ad6300a70b66f52752fe4bc2d84403a71f3ee15837"
        fixture.require(payload.stat().st_size == 1584 and fixture.sha256(payload) == expected,
                        "Exact callback payload fingerprint changed")
        results["payload_sha256"] = expected
        env["GTM_QUEUE_CALLBACK_PATH"] = fixture.paths.windows_path(payload)
        results["callback_fixture_header_sha256"] = fixture.sha256(HERE / "queue_callback_fixture.h")
    try:
        command = [fixture.DEFAULT_ZIG, "cc", "-target", "x86_64-windows-gnu",
                   "-std=c11", "-O0", "-g", "-Wall", "-Wextra", "-Werror",
                   "-fno-sanitize=alignment",
                   source, VENDOR / "src/buffer.c", VENDOR / "src/hook.c",
                   VENDOR / "src/trampoline.c", VENDOR / "src/hde/hde64.c",
                   "-o", binary]
        if args.exact_callback:
            command.insert(2, "-DGTM_EXACT_CALLBACK_FIXTURE=1")
        results["build"], _ = invoke(prefix + "-build", command, env, 180)
        fixture.require(results["build"].get("exit_code") == 0,
                        "Build failed; see queue-hook-build.log")
        results["binary_sha256"] = fixture.sha256(binary)
        if not args.build_only:
            windows_path = fixture.paths.windows_path(binary)
            results["run"], output = invoke(prefix + "-run",
                [fixture.CX / "bin/wine", "--debugmsg=-all", "--bottle",
                 fixture.BOTTLE_NAME, "--cx-app", windows_path, "--"], env, 60)
            expected = ["COMPLETE result=0", "RESTORE native_table_unchanged=1",
                        "COUNTERS execute=1 signal=1 expected_each=1",
                        "RESTORED_COUNTERS execute=1 signal=1 unchanged=1",
                        "RETAIN_THUNKS until_process_exit=1"]
            if args.exact_callback:
                expected += ["CALLBACK_BEFORE_DEVICE entries_published=1 core_resident=1",
                             "LOADER_RESTORE original_pointer=1 protection_restored=1"]
            results["passed"] = results["run"].get("exit_code") == 0 and all(x in output for x in expected)
            fixture.require(results["passed"], "Probe failed; see queue-hook-run.log")
    finally:
        after = fixture.runtime_state()
        fixture.check_runtime(after)
        results["runtime_after"] = after
        results["runtime_unchanged"] = (state == after and fixture.sha256(metal) == metal_hash
                                        and fixture.sha256(glue) == glue_hash)
        (HERE / (prefix + "-results.json")).write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
