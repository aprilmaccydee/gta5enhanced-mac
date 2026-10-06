"""Portable locations for the fixed, fingerprint-checked compatibility profile.

GTM_CROSSOVER_APP and GTM_BOTTLE_PATH select local installations, never a
different Windows executable. GTM_ZIG or PATH selects the fixture compiler;
callers still require the exact tested compiler and runtime hashes.
"""
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "memory_only"
_user_app = Path.home() / "Applications/CrossOver.app"
_default_app = _user_app if _user_app.is_dir() else Path("/Applications/CrossOver.app")
APP = Path(os.environ.get("GTM_CROSSOVER_APP", str(_default_app))).expanduser().resolve()
CX = APP / "Contents/SharedSupport/CrossOver"
_default_bottle = Path.home() / "Library/Application Support/CrossOver/Bottles/Rockstar Games Launcher"
BOTTLE = Path(os.environ.get("GTM_BOTTLE_PATH", str(_default_bottle))).expanduser().resolve()
GAME = BOTTLE / "drive_c/Program Files/Rockstar Games/Grand Theft Auto V Enhanced/GTA5_Enhanced.exe"
GAME_COMM = r"C:\Program Files\Rockstar Games\Grand Theft Auto V Enhanced\GTA5_Enhanced.exe"
_zig = os.environ.get("GTM_ZIG") or shutil.which("zig")
ZIG = Path(_zig).expanduser().resolve() if _zig else None


def windows_path(path):
    """CrossOver's normal Z: mapping for a project-owned fixture file."""
    return "Z:" + str(Path(path).resolve()).replace("/", "\\")
