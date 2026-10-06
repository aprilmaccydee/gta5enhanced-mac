"""Fixed GTA/LML development-build startup experiment; no debugger target calls.

Reuses the verified graphics callback. A separate one-shot callback starts the
already installed LML development build from an ordinary game PID API call.
Only the exact local game and DLL fingerprints are accepted. No PID argument,
arbitrary DLL path, registry edit, or on-disk game/runtime patch is supported.
"""
import hashlib
import ctypes
import json
from pathlib import Path
import struct
import subprocess

import lldb
import install_loader_hook as graphics

checks = graphics.checks
HERE = Path(__file__).resolve().parent
CALLBACK = HERE.parent / 'prototype/lml/lml_callback.bin'
# Exact artifact passed seven isolated cases and an independent source/artifact review.
CALLBACK_SHA256 = '58f8ac95ab2973b33da6233ca7daaebcafec194b5b2e165f88bc4a5ffa7a84b0'
FIXTURE_VALIDATED = True
LML_HASHES = {
    'version.dll': 'bc5e691f41729df02e3d777f7dd1937dfa1c62e763fa58c8084a944e3cdc47a7',
    'vfs.dll': '65c7ce43cbf08bf2206de7d1fffbb50c0e05e196efd1880ef2ccebf5c1d784cf',
    'EasyHookPatch.dll': '4a4cbce1efc158739da9d310362fe0365a3c8cec6d57f264a0dbe4722f1fec38',
}
GAME_PID_SLOT = graphics.profiles.GAME_PID_SLOT
GAME_DIRECTORY = 'C:\\Program Files\\Rockstar Games\\Grand Theft Auto V Enhanced\\'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def qword(process, address):
    return struct.unpack('<Q', checks.read(process, address, 8))[0]


def creation_time(pid):
    library = ctypes.CDLL('/usr/lib/libproc.dylib')
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    info = ctypes.create_string_buffer(136)
    require(library.proc_pidinfo(pid, 3, 0, info, 136) == 136, 'Process creation metadata unavailable')
    return struct.unpack_from('<QQ', info.raw, 120)


def pinned(path, expected):
    require(path.is_file() and not path.is_symlink(), 'Pinned file missing or symlink: ' + path.name)
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, 'Pinned file changed: ' + path.name)
    return data


def allocate(process, permissions):
    error = lldb.SBError()
    address = process.AllocateMemory(4096, permissions, error)
    require(error.Success() and address != lldb.LLDB_INVALID_ADDRESS, 'Debugger memory allocation failed')
    return address


def install_lml_game(debugger):
    _install_lml_game(debugger, 'rockstar')


def install_lml_game_standalone(debugger):
    _install_lml_game(debugger, 'standalone')


def _install_lml_game(debugger, profile):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    allocated = []
    published = False
    attempted_publication = False
    success = False
    detached = False
    safe_resume = True
    record = {}
    old_async = debugger.GetAsync()
    try:
        debugger.SetAsync(False)
        require(FIXTURE_VALIDATED and CALLBACK_SHA256, 'LML callback fixture has not passed')
        require(process.IsValid() and process.GetState() == lldb.eStateStopped and
                target.GetAddressByteSize() == 8, 'A stopped x64 game is required')
        require(not target.GetNumBreakpoints(), 'No user breakpoints permitted')
        pid = process.GetProcessID()
        creation = creation_time(pid)
        comm = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'comm='], text=True, timeout=5).strip()
        require(comm == checks.paths.GAME_COMM, 'Process is outside the fixed GTA scope')
        mappings = subprocess.check_output(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn'],
                                           text=True, stderr=subprocess.DEVNULL, timeout=5).splitlines()
        require('n' + str(checks.GAME) in mappings, 'Expected game image is not mapped')
        require('n' + str(checks.APPLE_D3D12) not in mappings, 'D3D12 already mapped; fresh startup missed')
        require(not any(line.lower().endswith('/vfs.dll') for line in mappings),
                'VFS already mapped; refusing a second initialization path')
        require(not any(line.lower().endswith(('/easyhookpatch.dll', '/easyhook64.dll')) for line in mappings),
                'EasyHookPatch already mapped; an existing bootstrap may be active')
        graphics.profiles.validate_game_file(checks.GAME, profile)
        for name, expected in LML_HASHES.items():
            pinned(checks.GAME.parent / name, expected)
        for path, expected in (
            (checks.CX / 'lib/wine/x86_64-windows/kernelbase.dll', checks.KERNELBASE_SHA256),
            (checks.CX / 'lib/wine/x86_64-windows/ntdll.dll', checks.NTDLL_SHA256),
            (checks.APPLE_D3D12, checks.APPLE_D3D12_SHA256),
        ):
            pinned(path, expected)
        for relative, expected in graphics.QUEUE_NATIVE_HASHES.items():
            pinned(checks.CX / relative, expected)
        checks.validate_image(process, 0x140000000, checks.GAME)
        checks.validate_image(process, checks.KERNELBASE, checks.CX / 'lib/wine/x86_64-windows/kernelbase.dll')
        checks.validate_image(process, checks.NTDLL, checks.CX / 'lib/wine/x86_64-windows/ntdll.dll')
        require(qword(process, graphics.SLOT) == graphics.ORIGINAL, 'Existing loader hook found')
        original_pid, kernel32_base = graphics.profiles.validate_pid_import(process, checks, mappings)

        callback = pinned(CALLBACK, CALLBACK_SHA256)
        queue_callback = pinned(graphics.QUEUE_CALLBACK, graphics.QUEUE_CALLBACK_SHA256)
        require(0 < len(callback) <= 4080 and 0 < len(queue_callback) <= 4096, 'Callback size exceeds bound')
        rw = lldb.ePermissionsReadable | lldb.ePermissionsWritable
        rx = lldb.ePermissionsReadable | lldb.ePermissionsExecutable
        context = allocate(process, rw)
        allocated.append(context)
        lml_code = allocate(process, rx)
        allocated.append(lml_code)
        queue_code = allocate(process, rx)
        allocated.append(queue_code)
        easy_path = (GAME_DIRECTORY + 'EasyHookPatch.dll' + '\0').encode('utf-16-le')
        vfs_path = (GAME_DIRECTORY + 'vfs.dll' + '\0').encode('utf-16-le')
        easy_address = context + 112
        vfs_address = easy_address + len(easy_path)
        header = struct.pack('<7Q8I3Q', original_pid, checks.NTDLL + 0x27390,
            checks.NTDLL + 0x34980, checks.KERNELBASE + 0x392d0, checks.KERNELBASE + 0xd8c0,
            easy_address, vfs_address, 0, 0, 0, 0xffffffff, 0, 0, 0, 0, 0, 0, 0)
        require(len(header) == 112 and len(header + easy_path + vfs_path) < 4096, 'Unexpected context layout')
        shim = b'\x48\xb9' + struct.pack('<Q', context) + b'\xe9\x01\0\0\0\x90'
        graphics.write(process, context, header + easy_path + vfs_path)
        graphics.write(process, lml_code, shim + callback)
        graphics.write(process, queue_code, queue_callback)
        region = lldb.SBMemoryRegionInfo()
        error = process.GetMemoryRegionInfo(context, region)
        require(error.Success() and region.IsReadable() and region.IsWritable() and not region.IsExecutable(),
                'Context allocation is not writable, non-executable memory')
        for address in (lml_code, queue_code):
            region = lldb.SBMemoryRegionInfo()
            error = process.GetMemoryRegionInfo(address, region)
            require(error.Success() and region.IsReadable() and region.IsExecutable() and not region.IsWritable(),
                    'Callback allocation is not readable and executable')

        # Both imports publish only while every thread is stopped. All code and
        # context are already present; graphics must be available before LML runs.
        attempted_publication = True
        graphics.write(process, graphics.SLOT, struct.pack('<Q', queue_code))
        graphics.write(process, GAME_PID_SLOT, struct.pack('<Q', lml_code))
        published = True
        record = {'pid': pid, 'creation': list(creation), 'game_profile': profile,
                  'game_sha256': graphics.profiles.game_hash(profile),
                  'callback_sha256': CALLBACK_SHA256, 'context': context,
                  'lml_code': lml_code, 'lml_size': len(shim + callback), 'game_pid_slot': GAME_PID_SLOT,
                  'original_pid': original_pid, 'kernel32_base': kernel32_base,
                  'queue_code': queue_code, 'queue_sha256': graphics.QUEUE_CALLBACK_SHA256,
                  'lml_hashes': LML_HASHES, 'initialization_observed': False,
                  'allocations_retained_until_exit': True}
        destination = HERE / 'logs' / ('lml-game-hook-' + str(pid) + '.json')
        destination.write_text(json.dumps(record, indent=2) + '\n')
        destination.chmod(0o600)
        active = HERE / 'logs/lml-active.json'
        active.write_text(json.dumps(record, indent=2) + '\n')
        active.chmod(0o600)
        success = True
        print('GTM: graphics and one-shot LML imports armed; no debugger target calls', flush=True)
    except BaseException as error:
        success = False
        print('GTM: LML setup failed: ' + str(error), flush=True)
        if attempted_publication:
            try:
                graphics.write(process, GAME_PID_SLOT, struct.pack('<Q', original_pid))
                graphics.write(process, graphics.SLOT, struct.pack('<Q', graphics.ORIGINAL))
                require(qword(process, GAME_PID_SLOT) == original_pid and
                        qword(process, graphics.SLOT) == graphics.ORIGINAL, 'Import rollback differed')
                published = False
            except BaseException as rollback:
                safe_resume = False
                print('GTM: rollback could not be verified: ' + str(rollback), flush=True)
        if not attempted_publication:
            for address in reversed(allocated):
                process.DeallocateMemory(address)
        # After any publication attempt, retain memory even on confirmed rollback.
    finally:
        if success:
            try:
                safe_resume = (qword(process, GAME_PID_SLOT) == lml_code and
                    qword(process, graphics.SLOT) == queue_code and
                    checks.read(process, lml_code, len(shim + callback)) == shim + callback and
                    checks.read(process, queue_code, len(queue_callback)) == queue_callback and
                    checks.read(process, context, len(header + easy_path + vfs_path)) == header + easy_path + vfs_path)
            except BaseException:
                safe_resume = False
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            error = process.Detach(not safe_resume)
            detached = error.Success()
        debugger.SetAsync(old_async)
        print('GTM_HOOK_RESULT ' + ('success' if success and published and safe_resume and detached else 'failure'), flush=True)
