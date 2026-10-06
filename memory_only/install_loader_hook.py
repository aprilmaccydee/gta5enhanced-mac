"""Fixed CrossOver/GTA compatibility hook, installed while all threads stop.

No target function is called by the debugger and no breakpoint is installed.
The sole loader edit is kernelbase's existing LdrLoadDll import pointer. The
fixed callback invokes the original loader, retaining System32 D3D12Core after
a normal successful D3D12 load. No Wine or game file is modified.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import struct
import subprocess
import lldb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location('gtm_fixed_image_checks', HERE / 'image_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
profile_spec = importlib.util.spec_from_file_location('gtm_game_profiles', HERE / 'game_profiles.py')
profiles = importlib.util.module_from_spec(profile_spec)
profile_spec.loader.exec_module(profiles)
SLOT = checks.KERNELBASE + 0xc9e40
ORIGINAL = checks.NTDLL + 0x15680
CALLBACK = HERE / 'core_callback.bin'
CALLBACK_SHA256 = '04e22dd0d994126885594eab93a37b6db60123a12461dd8e04b509eb23688d7c'
GAME_SHA256 = 'b48e80f5fc899eb89e5dcc9f3b68e9551cc8dcfac78b2a940b207e569e9987ea'
QUEUE_CALLBACK = HERE / 'core_queue_callback.bin'
QUEUE_CALLBACK_SHA256 = 'b4a41423708b02cffeea00ad6300a70b66f52752fe4bc2d84403a71f3ee15837'
QUEUE_NATIVE_HASHES = {
    'lib64/apple_gptk/external/libd3dshared.dylib': '5131e631eee8b542eadf48f4df9fd662d9aeeb59139137e0e6e14047dc434995',
    'lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal': '05a7beaed4494a4f5f53d3f626a82fffc3b70146436a908b7048a0632a49e1a8',
}


def write(process, address, data):
    error = lldb.SBError()
    size = process.WriteMemory(address, data, error)
    if error.Fail() or size != len(data):
        raise RuntimeError('Memory write failed: ' + str(error))
    if checks.read(process, address, len(data)) != data:
        raise RuntimeError('Memory write readback differed')


def install_fixture(debugger):
    _install(debugger, True)


def install_game(debugger):
    _install(debugger, False)


def install_queue_game(debugger):
    _install(debugger, False, queue=True)


def install_queue_game_standalone(debugger):
    _install(debugger, False, queue=True, profile='standalone')


def restore_game(debugger):
    """Restore only our recorded GTA loader import; retain code and DLL refs."""
    _restore_game(debugger)


def _restore_game(debugger):
    process = debugger.GetSelectedTarget().GetProcess()
    success = False
    touched = False
    safe_resume = True
    detached = False
    try:
        if not process.IsValid() or process.GetState() != lldb.eStateStopped:
            raise RuntimeError('An attached stopped process is required')
        pid = process.GetProcessID()
        comm = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'comm='], text=True, timeout=5).strip()
        if comm != r'C:\Program Files\Rockstar Games\Grand Theft Auto V Enhanced\GTA5_Enhanced.exe':
            raise RuntimeError('The target is not the fixed GTA process')
        if hashlib.sha256(checks.GAME.read_bytes()).hexdigest() != GAME_SHA256:
            raise RuntimeError('Game fingerprint changed')
        checks.validate_image(process, 0x140000000, checks.GAME)
        checks.validate_image(process, checks.KERNELBASE, checks.CX / 'lib/wine/x86_64-windows/kernelbase.dll')
        checks.validate_image(process, checks.NTDLL, checks.CX / 'lib/wine/x86_64-windows/ntdll.dll')
        mappings = subprocess.check_output(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn'], text=True, stderr=subprocess.DEVNULL, timeout=5)
        core = checks.CX / 'lib/wine/x86_64-windows/d3d12core.dll'
        if 'n' + str(checks.GAME) not in mappings.splitlines() or 'n' + str(core) not in mappings.splitlines():
            raise RuntimeError('Expected game and core mappings are required before restoration')
        path = HERE / 'logs' / ('game-hook-' + str(pid) + '.json')
        record = json.loads(path.read_text())
        expected_sha256 = CALLBACK_SHA256
        if (record['pid'] != pid or record['fixture'] or record['slot'] != SLOT or
                record['original'] != ORIGINAL or record['callback_sha256'] != expected_sha256):
            raise RuntimeError('The installation record differs from this fixed hook')
        current = struct.unpack('<Q', checks.read(process, SLOT, 8))[0]
        if current != record['callback']:
            raise RuntimeError('The loader import no longer points to our recorded callback')
        code = checks.read(process, record['callback'], record['size'])
        if hashlib.sha256(code).hexdigest() != expected_sha256:
            raise RuntimeError('The installed callback bytes changed')
        touched = True
        write(process, SLOT, struct.pack('<Q', ORIGINAL))
        success = True
        record['original_loader_restored'] = True
        record['core_mapping_present_at_restore'] = True
        record['callback_memory_retained_for_inflight_calls'] = True
        path.write_text(json.dumps(record, indent=2) + '\n')
        print('GTM: original Wine loader import restored; core and callback memory retained', flush=True)
    except BaseException as exc:
        success = False
        print('GTM: restoration failed: ' + str(exc), flush=True)
    finally:
        if touched:
            try:
                safe_resume = struct.unpack('<Q', checks.read(process, SLOT, 8))[0] in (ORIGINAL, record['callback'])
            except BaseException:
                safe_resume = False
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            error = process.Detach(not safe_resume)
            detached = error.Success()
        print('GTM_RESTORE_RESULT ' + ('success' if success and safe_resume and detached else 'failure'), flush=True)


def _install(debugger, fixture, queue=False, profile='rockstar'):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    allocation = lldb.LLDB_INVALID_ADDRESS
    installed = False
    operation_ok = False
    touched_slot = False
    detached = False
    old_async = debugger.GetAsync()
    callback_path = QUEUE_CALLBACK if queue else CALLBACK
    callback_sha256 = QUEUE_CALLBACK_SHA256 if queue else CALLBACK_SHA256
    try:
        debugger.SetAsync(False)
        if not process.IsValid() or process.GetState() != lldb.eStateStopped:
            raise RuntimeError('An attached stopped process is required')
        pid = process.GetProcessID()
        comm = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'comm='], text=True, timeout=5).strip()
        expected = r'C:\GTMProbe\GTMLoaderProbe.exe' if fixture else r'C:\Program Files\Rockstar Games\Grand Theft Auto V Enhanced\GTA5_Enhanced.exe'
        executable = ROOT / 'prototype/bottles/GTMLoaderPrototype/drive_c/GTMProbe/GTMLoaderProbe.exe' if fixture else checks.GAME
        if comm != expected:
            raise RuntimeError('Process outside the fixed compatibility scope')
        mappings = subprocess.check_output(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn'], text=True, stderr=subprocess.DEVNULL, timeout=5)
        if 'n' + str(executable) not in mappings.splitlines():
            raise RuntimeError('Expected local executable mapping is absent')
        if not fixture and 'n' + str(checks.APPLE_D3D12) in mappings.splitlines():
            raise RuntimeError('D3D12 is already mapped; startup was missed and this hook cannot retry initialization')
        if not fixture:
            profiles.validate_game_file(executable, profile)
        if queue:
            for name, expected_hash in QUEUE_NATIVE_HASHES.items():
                if hashlib.sha256((checks.CX / name).read_bytes()).hexdigest() != expected_hash:
                    raise RuntimeError('Pinned native graphics fingerprint changed: ' + name)
        for path, expected_hash in (
            (checks.CX / 'lib/wine/x86_64-windows/kernelbase.dll', checks.KERNELBASE_SHA256),
            (checks.CX / 'lib/wine/x86_64-windows/ntdll.dll', checks.NTDLL_SHA256),
            (checks.APPLE_D3D12, checks.APPLE_D3D12_SHA256),
        ):
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError('Pinned runtime fingerprint changed: ' + path.name)
        checks.validate_image(process, 0x140000000, executable)
        checks.validate_image(process, checks.KERNELBASE, checks.CX / 'lib/wine/x86_64-windows/kernelbase.dll')
        checks.validate_image(process, checks.NTDLL, checks.CX / 'lib/wine/x86_64-windows/ntdll.dll')
        if not fixture and profile == 'standalone':
            profiles.validate_pid_import(process, checks, mappings.splitlines())
        if struct.unpack('<Q', checks.read(process, SLOT, 8))[0] != ORIGINAL:
            raise RuntimeError('Loader import is not the expected original function')
        if target.GetNumBreakpoints():
            raise RuntimeError('This installation requires no user breakpoints')
        callback = callback_path.read_bytes()
        if not 0 < len(callback) <= 4096 or hashlib.sha256(callback).hexdigest() != callback_sha256:
            raise RuntimeError('Fixed callback fingerprint mismatch')
        error = lldb.SBError()
        allocation = process.AllocateMemory(4096, lldb.ePermissionsReadable | lldb.ePermissionsExecutable, error)
        if error.Fail() or allocation == lldb.LLDB_INVALID_ADDRESS:
            raise RuntimeError('Debugger allocation failed: ' + str(error))
        write(process, allocation, callback)
        region = lldb.SBMemoryRegionInfo()
        error = process.GetMemoryRegionInfo(allocation, region)
        if error.Fail() or not region.IsReadable() or not region.IsExecutable():
            raise RuntimeError('Callback mapping is not readable and executable')
        touched_slot = True
        write(process, SLOT, struct.pack('<Q', allocation))
        installed = True
        record = {'pid':pid, 'fixture':fixture, 'callback':allocation, 'size':len(callback),
                  'callback_sha256':callback_sha256, 'slot':SLOT, 'original':ORIGINAL,
                  'readable':region.IsReadable(), 'writable':region.IsWritable(), 'executable':region.IsExecutable()}
        record['shared_queue_forwarding'] = queue
        record['game_profile'] = 'fixture' if fixture else profile
        if not fixture:
            record['game_sha256'] = profiles.game_hash(profile)
        if queue:
            record['native_graphics_hashes'] = QUEUE_NATIVE_HASHES
        prefix = 'queue-game-hook-' if queue else ('fixture-hook-' if fixture else 'game-hook-')
        destination = HERE / 'logs' / (prefix + str(pid) + '.json')
        destination.write_text(json.dumps(record, indent=2) + '\n')
        destination.chmod(0o600)
        print(f'GTM: loader import points to verified callback 0x{allocation:x}; no target function executed', flush=True)
        operation_ok = True
    except BaseException as exc:
        operation_ok = False
        print('GTM: hook installation failed: ' + str(exc), flush=True)
        # Roll back any partial pointer write before resuming the process.
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            try:
                current = struct.unpack('<Q', checks.read(process, SLOT, 8))[0]
                if current != ORIGINAL and allocation != lldb.LLDB_INVALID_ADDRESS:
                    write(process, SLOT, struct.pack('<Q', ORIGINAL))
                installed = False
            except BaseException as rollback:
                print('GTM: loader pointer rollback could not be confirmed: ' + str(rollback), flush=True)
        if not installed and allocation != lldb.LLDB_INVALID_ADDRESS:
            # Allocation is deliberately retained if pointer rollback is uncertain.
            try:
                if struct.unpack('<Q', checks.read(process, SLOT, 8))[0] == ORIGINAL:
                    process.DeallocateMemory(allocation)
            except BaseException:
                pass
    finally:
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            safe_resume = not touched_slot
            if touched_slot:
                try:
                    current = struct.unpack('<Q', checks.read(process, SLOT, 8))[0]
                    safe_resume = current == ORIGINAL or (
                        current == allocation and
                        checks.read(process, allocation, len(callback)) == callback)
                except BaseException:
                    safe_resume = False
            if not safe_resume:
                operation_ok = False
                print('GTM: loader pointer is unverified; detaching with the target kept stopped', flush=True)
            error = process.Detach(not safe_resume)
            detached = error.Success()
            if not detached:
                print('GTM: detach failed: ' + str(error), flush=True)
        debugger.SetAsync(old_async)
        print('GTM_HOOK_RESULT ' + ('success' if operation_ok and installed and detached else 'failure'), flush=True)
