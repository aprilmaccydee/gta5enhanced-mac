#!/usr/bin/env python3
"""Rebuild the two project-owned callback blobs and verify their tested hashes.

This compiles our C files only. It neither rebuilds nor modifies Wine, and it
does not attach to any process. Requires the pinned Zig compiler executable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ZIG_SHA256 = '1c5f706db0ed6d55451940f31dc05a6177d05696bc1fa5d853696d04c718b523'
EXPECTED = {
    'core_callback': ('GTMCoreCompatCallback', '04e22dd0d994126885594eab93a37b6db60123a12461dd8e04b509eb23688d7c'),
    'core_queue_callback': ('GTMCoreQueueCompatCallback', 'b4a41423708b02cffeea00ad6300a70b66f52752fe4bc2d84403a71f3ee15837'),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def extract_text(data, entry_name):
    machine, count, _, symoff, nsyms, optsize, _ = struct.unpack_from('<HHIIIHH', data)
    require(machine == 0x8664 and optsize == 0, 'Expected x64 COFF object')
    text, text_index = None, None
    for index in range(count):
        offset = 20 + 40 * index
        name = data[offset:offset + 8].split(b'\0', 1)[0]
        size, raw, _, _, relocations, _, _ = struct.unpack_from('<IIIIHHI', data, offset + 16)
        require(relocations == 0, 'Callback has a relocation')
        if name == b'.text':
            require(raw + size <= len(data), 'Truncated code section')
            text, text_index = data[raw:raw + size], index + 1
        if name in (b'.data', b'.bss', b'.pdata', b'.xdata'):
            require(size == 0, 'Callback gained data or unwind sections')
    require(text and len(text) <= 4096, 'Callback exceeds its allocation')
    strings = symoff + nsyms * 18
    symbols, index = [], 0
    while index < nsyms:
        offset = symoff + index * 18
        name = data[offset:offset + 8]
        if name[:4] == b'\0' * 4:
            start = strings + struct.unpack_from('<I', name, 4)[0]
            name = data[start:data.index(0, start)]
        else:
            name = name.split(b'\0', 1)[0]
        value, section, _, storage, auxiliary = struct.unpack_from('<IhHBB', data, offset + 8)
        if storage == 2:
            require(section > 0, 'Callback has an undefined external symbol')
            symbols.append((name.decode(), value, section))
        index += 1 + auxiliary
    require(symbols == [(entry_name, 0, text_index)], 'Unexpected callback entry or helper')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zig', default=os.environ.get('GTM_ZIG') or shutil.which('zig'))
    parser.add_argument('--check', action='store_true', help='Verify reproduction without replacing blobs')
    args = parser.parse_args()
    require(args.zig, 'Set GTM_ZIG or pass --zig with the pinned Zig executable')
    zig = Path(args.zig).expanduser().resolve()
    require(digest(zig.read_bytes()) == ZIG_SHA256, 'Zig executable fingerprint differs')
    env = os.environ.copy()
    env['ZIG_LOCAL_CACHE_DIR'] = str(HERE / '.zig-cache')
    env['ZIG_GLOBAL_CACHE_DIR'] = str(HERE / '.zig-global-cache')
    flags = ['cc', '-target', 'x86_64-windows-gnu', '-std=c11', '-O2', '-g0',
             '-ffreestanding', '-fno-builtin', '-fno-stack-protector',
             '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
             '-fno-vectorize', '-fno-slp-vectorize', '-mno-sse', '-mno-sse2',
             '-mno-mmx', '-msoft-float', '-mno-red-zone', '-Wall', '-Wextra', '-Werror']
    built = {}
    with tempfile.TemporaryDirectory(prefix='gtm-callback-build-') as directory:
        for name, (entry, expected) in EXPECTED.items():
            obj = Path(directory) / (name + '.obj')
            subprocess.run([str(zig), *flags, '-c', str(HERE / (name + '.c')), '-o', str(obj)],
                           cwd=ROOT, env=env, check=True, timeout=180)
            blob = extract_text(obj.read_bytes(), entry)
            require(digest(blob) == expected, name + ' differs from the validated payload')
            if args.check:
                require((HERE / (name + '.bin')).read_bytes() == blob,
                        name + ' checked-in payload differs from rebuilt code')
            built[name] = blob
        # Publish only after both independently pinned outputs validated.
        if not args.check:
            for name, blob in built.items():
                (HERE / (name + '.bin')).write_bytes(blob)
    print(json.dumps({name: {'bytes': len(blob), 'sha256': digest(blob)}
                      for name, blob in built.items()}, indent=2))


if __name__ == '__main__':
    main()
