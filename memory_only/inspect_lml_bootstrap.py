"""Read-only inspection of this experiment's one recorded GTA launch."""
import hashlib
import json
import struct
import subprocess

import lldb
import install_lml_bootstrap as install


def run(debugger):
    target = debugger.GetSelectedTarget()
    process = target.GetProcess()
    result = {'passed': False, 'detached': False}
    record = None
    try:
        active = install.HERE / 'logs/lml-active.json'
        active_bytes = active.read_bytes()
        install.require(len(active_bytes) < 4096, 'Unexpected record size')
        record = json.loads(active_bytes)
        pid = record['pid']
        result['pid'] = pid
        install.require(process.IsValid() and process.GetState() == lldb.eStateStopped and
                        process.GetProcessID() == pid, 'Recorded stopped process required')
        install.require(list(install.creation_time(pid)) == record['creation'], 'Recorded process has exited')
        comm = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'comm='], text=True, timeout=5).strip()
        install.require(comm == install.checks.paths.GAME_COMM, 'Wrong executable')
        profile = record.get('game_profile', 'rockstar')
        install.graphics.profiles.validate_game_file(install.checks.GAME, profile)
        install.checks.validate_image(process, 0x140000000, install.checks.GAME)
        callback = install.pinned(install.CALLBACK, install.CALLBACK_SHA256)
        context, code = record['context'], record['lml_code']
        shim = b'\x48\xb9' + struct.pack('<Q', context) + b'\xe9\x01\0\0\0\x90'
        install.require(install.checks.read(process, code, len(shim + callback)) == shim + callback,
                        'Recorded callback differs')
        install.require(install.qword(process, install.GAME_PID_SLOT) == code, 'PID import differs')
        data = install.checks.read(process, context, 112)
        pointers = struct.unpack_from('<7Q', data)
        install.require(pointers[:5] == (record['original_pid'], install.checks.NTDLL + 0x27390,
            install.checks.NTDLL + 0x34980, install.checks.KERNELBASE + 0x392d0,
            install.checks.KERNELBASE + 0xd8c0), 'Callback API context differs')
        values = struct.unpack_from('<8I3Q', data, 56)
        names = ['state', 'attempts', 'stage', 'result', 'easy_error', 'vfs_error', 'proc_error',
                 'reserved', 'easy_module', 'vfs_module', 'initialize_safe']
        result.update(dict(zip(names, values)))
        mappings = subprocess.check_output(['/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn'],
                                          text=True, stderr=subprocess.DEVNULL, timeout=5).splitlines()
        result['mapped_lml_modules'] = sorted({line.rsplit('/', 1)[-1] for line in mappings
            if line.lower().endswith(('/vfs.dll', '/easyhookpatch.dll', '/easyhook64.dll'))})
        install.require(active.read_bytes() == active_bytes, 'Active record changed')
        result['passed'] = True
    except BaseException as error:
        result['error'] = str(error)
    finally:
        if process.IsValid() and process.GetState() == lldb.eStateStopped:
            result['detached'] = process.Detach().Success()
        result['passed'] = result['passed'] and result['detached']
        if record:
            path = install.HERE / 'logs' / ('lml-inspection-' + str(record['pid']) + '.json')
            path.write_text(json.dumps(result, indent=2) + '\n')
            path.chmod(0o600)
        print(json.dumps(result), flush=True)
        print('GTM_LML_INSPECTION_RESULT ' + ('success' if result['passed'] else 'failure'), flush=True)
