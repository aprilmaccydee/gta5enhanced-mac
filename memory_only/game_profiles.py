"""Explicit local game identities; no executable replacement or binary editing."""
import hashlib
import struct

PROFILES = {
    'rockstar': 'b48e80f5fc899eb89e5dcc9f3b68e9551cc8dcfac78b2a940b207e569e9987ea',
    'standalone': '827538303fa80cd65a81dabfe67f5ffc4c8c2f90a3bad2d01c0a38099cdb1cbc',
}
GAME_BASE = 0x140000000
GAME_PID_SLOT = GAME_BASE + 0x51213fc
PID_RVA = 0x20650
KERNEL32_SHA256 = '9a8c4977b366a00c947b0456efac8ee3cbbd951e6f5e467dba1aba72082fba26'


def game_hash(profile):
    if profile not in PROFILES:
        raise RuntimeError('Unknown fixed game profile')
    return PROFILES[profile]


def validate_game_file(path, profile):
    if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != game_hash(profile):
        raise RuntimeError('Game executable does not match the selected ' + profile + ' profile')


def kernel32_pid_bytes(data):
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    section = pe + 24 + struct.unpack_from('<H', data, pe + 20)[0]
    for index in range(count):
        _, rva, raw_size, offset = struct.unpack_from('<IIII', data, section + index * 40 + 8)
        if rva <= PID_RVA and PID_RVA + 24 <= rva + raw_size:
            return data[offset + PID_RVA - rva:offset + PID_RVA - rva + 24]
    raise RuntimeError('Pinned PID routine is outside the expected image sections')


def validate_pid_import(process, checks, mappings):
    """Require an actual Wine-bound import, not the standalone file's disk IAT."""
    kernel32 = checks.CX / 'lib/wine/x86_64-windows/kernel32.dll'
    if not kernel32.is_file() or kernel32.is_symlink():
        raise RuntimeError('Pinned kernel32 file missing or symlink')
    data = kernel32.read_bytes()
    if hashlib.sha256(data).hexdigest() != KERNEL32_SHA256:
        raise RuntimeError('Pinned kernel32 fingerprint changed')
    if 'n' + str(kernel32) not in mappings:
        raise RuntimeError('Pinned kernel32 mapping absent')
    original = struct.unpack('<Q', checks.read(process, GAME_PID_SLOT, 8))[0]
    base = original - PID_RVA
    if not (0x10000 <= base < 0x800000000000) or base & 0xffff:
        raise RuntimeError('Game PID import has not resolved to the expected Wine routine')
    checks.validate_image(process, base, kernel32)
    if checks.read(process, original, 24) != kernel32_pid_bytes(data):
        raise RuntimeError('Mapped original PID routine differs')
    return original, base
