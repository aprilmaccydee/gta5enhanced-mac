"""Experimental fixed-build LML bootstrap plus the verified graphics correction."""
import watch_gta_queue

watch = watch_gta_queue.watch_gta
watch.HELPER = watch.HERE / 'install_lml_bootstrap.py'
watch.INSTALLER_ENTRY = 'install_lml_game'
watch.FIXTURE_VALIDATED = True  # Exact callback/shim passed all seven owned cases and review.
original_checks = watch.runtime_checks


def lml_checks():
    checks = original_checks()
    checks.RUNTIME_SHA256.update({
        'lib/wine/x86_64-windows/kernel32.dll': '9a8c4977b366a00c947b0456efac8ee3cbbd951e6f5e467dba1aba72082fba26',
        str(watch.GAME.parent / 'version.dll'): 'bc5e691f41729df02e3d777f7dd1937dfa1c62e763fa58c8084a944e3cdc47a7',
        str(watch.GAME.parent / 'vfs.dll'): '65c7ce43cbf08bf2206de7d1fffbb50c0e05e196efd1880ef2ccebf5c1d784cf',
        str(watch.GAME.parent / 'EasyHookPatch.dll'): '4a4cbce1efc158739da9d310362fe0365a3c8cec6d57f264a0dbe4722f1fec38',
    })
    return checks


watch.runtime_checks = lml_checks

if __name__ == '__main__':
    raise SystemExit(watch.main())
