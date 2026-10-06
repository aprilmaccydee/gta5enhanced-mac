"""Fixed image identities shared by the working LLDB loader installer.

No debugger actions occur on import. These helpers only read and compare
the mapped PE header with the fingerprint-checked local image.
"""
import importlib.util
from pathlib import Path
import struct

_spec = importlib.util.spec_from_file_location("gtm_paths", Path(__file__).resolve().with_name("paths.py"))
paths = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(paths)
ROOT, CX, GAME = paths.ROOT, paths.CX, paths.GAME
KERNELBASE = 0x6fffffc10000
NTDLL = 0x6ffffff40000
KERNELBASE_SHA256 = "ca72a76ce5770996cc9cba6989031a79eb1e63b6d9fca90d59eebab22e39191b"
NTDLL_SHA256 = "6dff64c00793ce92124f1316985c63783f539f26b392975c70f57637458d2387"
APPLE_D3D12 = CX / "lib64/apple_gptk/wine/x86_64-windows/d3d12.dll"
APPLE_D3D12_SHA256 = "bbda1c4e94ee70255c528c5689b28333ca9bece2d755ede7c4197977a534704f"


def read(process, address, size):
    import lldb
    error = lldb.SBError()
    data = process.ReadMemory(address, size, error)
    if error.Fail() or len(data) != size:
        raise RuntimeError("Expected mapped memory is unavailable")
    return data


def pe_identity(data):
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise RuntimeError("Expected PE header is absent")
    offset = struct.unpack_from("<I", data, 0x3c)[0]
    if offset > 0x300 or offset + 92 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise RuntimeError("Unexpected PE header")
    return (struct.unpack_from("<H", data, offset + 4)[0],
            struct.unpack_from("<I", data, offset + 8)[0],
            struct.unpack_from("<H", data, offset + 24)[0],
            struct.unpack_from("<I", data, offset + 24 + 56)[0],
            struct.unpack_from("<I", data, offset + 24 + 64)[0])


def validate_image(process, base, path):
    with path.open("rb") as stream:
        expected = pe_identity(stream.read(0x400))
    if pe_identity(read(process, base, 0x400)) != expected:
        raise RuntimeError("Loaded PE identity differs from " + path.name)
