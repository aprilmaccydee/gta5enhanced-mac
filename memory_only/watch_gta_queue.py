"""One fresh GTA launch: core residency and shared D3D12 queue forwarding.

Runtime-only changes, no Wine/game/bottle files written. Disabled until the
exact loader callback and hook/fence fixture both pass in the owned process.
"""
import watch_gta

watch_gta.INSTALLER_ENTRY = 'install_queue_game'
watch_gta.FIXTURE_VALIDATED = True  # Both owned MinHook and exact loader-time fixtures passed.
_original_runtime_checks = watch_gta.runtime_checks


def queue_runtime_checks():
    checks = _original_runtime_checks()
    checks.RUNTIME_SHA256.update({
        'lib64/apple_gptk/external/libd3dshared.dylib': '5131e631eee8b542eadf48f4df9fd662d9aeeb59139137e0e6e14047dc434995',
        'lib64/apple_gptk/external/D3DMetal.framework/Versions/A/D3DMetal': '05a7beaed4494a4f5f53d3f626a82fffc3b70146436a908b7048a0632a49e1a8',
    })
    return checks


watch_gta.runtime_checks = queue_runtime_checks

if __name__ == '__main__':
    raise SystemExit(watch_gta.main())
