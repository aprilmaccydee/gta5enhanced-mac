#!/usr/bin/env python3
"""Build/test only the private, self-owned LML adaptation fixture.

python3 prototype/lml/run_fixture.py --zig /path/to/pinned/zig [--build-only]
Uses the existing GTMLoaderPrototype bottle and project Z: files. No real mod
DLL, game, launcher, debugger, registry command, or external process is used.
The raw callback has no unwind metadata; these tests cover ordinary returns.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fixture = module("gtm_lml_fixture", HERE.parent / "run_tests.py")
builder = module("gtm_lml_extract", ROOT / "memory_only/build_callbacks.py")
FLAGS = ["cc", "-target", "x86_64-windows-gnu", "-std=c11", "-O2", "-g0",
         "-ffreestanding", "-fno-builtin", "-fno-stack-protector",
         "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-fno-vectorize",
         "-fno-slp-vectorize", "-mno-sse", "-mno-sse2", "-mno-mmx", "-msoft-float",
         "-mno-red-zone", "-Wall", "-Wextra", "-Werror"]
CASES = ("success", "reentrant", "concurrent", "missing-vfs", "missing-export",
         "ignored-easy-failure", "original-last-error")


def invoke(label, argv, env, record, timeout=180):
    argv = [str(x) for x in argv]
    result = subprocess.run(argv, cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    output = result.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
    (HERE / "logs" / (label + ".log")).write_text(output)
    record[label] = {"argv": argv, "exit_code": result.returncode}
    fixture.require(result.returncode == 0, label + " failed; see its project log")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", type=Path, default=fixture.DEFAULT_ZIG)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    fixture.require(args.zig is not None and fixture.sha256(args.zig) == fixture.ZIG_SHA256,
                    "Pinned Zig compiler required")
    fixture.require((fixture.BOTTLE / "cxbottle.conf").is_file(), "Existing isolated bottle required")
    before = fixture.runtime_state()
    fixture.check_runtime(before)
    for subdirectory in ("logs", "fake", "missing-export"):
        (HERE / subdirectory).mkdir(exist_ok=True)
    env = fixture.fixture_environment()
    env["GTM_LML_OWNED_FIXTURE"] = "1"
    # Exact absolute fake DLL paths are loaded by our own executable.
    env["WINEDLLOVERRIDES"] = "EasyHookPatch,vfs=n,b"
    record = {"runtime_before": before, "build_only": args.build_only,
              "scope": "owned fixture and fake dependencies only", "commands": {}}
    try:
        blobs = []
        for index in range(2):
            obj = HERE / ("lml_callback" + ("-repeat" if index else "") + ".obj")
            invoke("build-callback-" + str(index),
                   [args.zig, *FLAGS, "-c", HERE / "lml_callback.c", "-o", obj], env, record["commands"])
            blobs.append(builder.extract_text(obj.read_bytes(), "GTMLmlCallback"))
        fixture.require(blobs[0] == blobs[1], "Callback reproduction differs")
        blob = blobs[0]
        (HERE / "lml_callback.bin").write_bytes(blob)
        record["callback"] = {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
                              "entry_offset": 0, "relocations": 0, "reproduced_twice": True,
                              "context_bytes": 112, "shim_bytes": 16}
        common = [args.zig, "cc", "-target", "x86_64-windows-gnu", "-std=c11", "-O1",
                  "-g", "-Wall", "-Wextra", "-Werror"]
        outputs = []
        for label, kind, extra, output in (
                ("easy", 1, [], HERE / "fake/EasyHookPatch.dll"),
                ("vfs", 2, [], HERE / "fake/vfs.dll"),
                ("no-export", 2, ["-DGTM_MISSING_EXPORT=1"], HERE / "missing-export/vfs.dll")):
            invoke("build-" + label,
                   [*common, "-shared", "-DGTM_DEPENDENCY_KIND=" + str(kind), *extra,
                    HERE / "fake_dependency.c", "-o", output], env, record["commands"])
            outputs.append(output)
        executable = HERE / "GTMLmlProbe.exe"
        invoke("build-probe", [*common, HERE / "lml_probe.c", "-o", executable], env, record["commands"])
        outputs.append(executable)
        record["artifacts"] = {str(path.relative_to(HERE)): fixture.sha256(path) for path in outputs}
        record["sources"] = {name: fixture.sha256(HERE / name) for name in
                             ("lml_callback.c", "lml_context.h", "lml_probe.c", "fake_dependency.c")}
        print(json.dumps(record["callback"], sort_keys=True), flush=True)
        if not args.build_only:
            record["tests"] = []
            for index, case in enumerate(CASES):
                output = invoke("run-" + case,
                    [fixture.CX / "bin/wine", "--debugmsg=-all", "--bottle", fixture.BOTTLE_NAME,
                     "--cx-app", fixture.paths.windows_path(executable), "--", str(index)],
                    env, record["commands"], timeout=60)
                fixture.require("COMPLETE result=0" in output and "IAT restored=1" in output,
                                case + " lacked fixture completion")
                record["tests"].append({"case": case, "passed": True})
                print(case + ": passed", flush=True)
            record["passed"] = True
    finally:
        after = fixture.runtime_state()
        fixture.check_runtime(after)
        record["runtime_after"] = after
        record["runtime_unchanged"] = before == after
        (HERE / "results.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
