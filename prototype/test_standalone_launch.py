"""Outer launcher tests: every process operation is mocked; no Wine/game runs.

PYTHONDONTWRITEBYTECODE=1 python3 prototype/test_standalone_launch.py
"""
import contextlib
import io
from pathlib import Path
import sys
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory_only"))
import watch_gta
import watch_gta_standalone as standalone
import game_profiles


class EntryPointTests(unittest.TestCase):
    def test_default_profile_is_not_mutated(self):
        before = watch_gta.GAME_SHA256, watch_gta.INSTALLER_ENTRY, watch_gta.HELPER
        for lml in (False, True):
            watch = standalone.configured_watcher(True, lml)
            self.assertEqual(watch.GAME_SHA256, standalone.game_hash("standalone"))
            self.assertEqual(before, (watch_gta.GAME_SHA256, watch_gta.INSTALLER_ENTRY, watch_gta.HELPER))
        self.assertEqual(watch_gta.GAME_SHA256, standalone.game_hash("rockstar"))

    def test_incompatible_file_rejected_before_process_inspection_or_launch(self):
        watch = standalone.configured_watcher(True)
        with tempfile.TemporaryDirectory(prefix="gtm-standalone-test-") as directory:
            watch.GAME = Path(directory) / "GTA5_Enhanced.exe"
            watch.GAME.write_bytes(b"owned deliberately incompatible fixture")
            with mock.patch.object(watch, "matching_pids") as inspect, \
                    mock.patch.object(standalone.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(RuntimeError, "does not match.*standalone"):
                    standalone.wait_for_game(watch, True)
                inspect.assert_not_called()
                launch.assert_not_called()

    def test_existing_game_rejected_before_direct_launch(self):
        watch = standalone.configured_watcher(True)
        with mock.patch.object(standalone, "validate_game_file"), \
                mock.patch.object(watch, "matching_pids", return_value=[123]), \
                mock.patch.object(standalone.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "Close the existing game"):
                standalone.wait_for_game(watch, True)
            launch.assert_not_called()

    def test_direct_launch_uses_only_fixed_path_flags_and_private_output(self):
        watch = standalone.configured_watcher(True)
        fake_child = mock.Mock()
        fake_child.poll.return_value = None
        with mock.patch.object(standalone, "validate_game_file") as validate, \
                mock.patch.object(watch, "matching_pids", side_effect=[[], [123]]), \
                mock.patch.object(standalone.subprocess, "Popen", return_value=fake_child) as launch, \
                mock.patch.object(Path, "is_file", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(standalone.wait_for_game(watch, True), 123)
            self.assertEqual(validate.call_args_list, [mock.call(watch.GAME, "standalone")] * 2)
        launch.assert_called_once()
        args, kwargs = launch.call_args
        self.assertEqual(args[0], [str(watch.paths.CX / "bin/wine"), "--debugmsg=-all", "--bottle",
                                  watch.BOTTLE.name, "--cx-app", watch.paths.GAME_COMM, "--",
                                  "-nobattleye", "-disableSpatialAudioClient"])
        self.assertEqual(kwargs["cwd"], watch.paths.GAME.parent)
        self.assertEqual(kwargs["env"]["CX_BOTTLE_PATH"], str(watch.BOTTLE.parent))
        for name in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[name], standalone.subprocess.DEVNULL)

    def test_manual_mode_does_not_launch(self):
        watch = standalone.configured_watcher(False)
        with mock.patch.object(standalone, "validate_game_file"), \
                mock.patch.object(watch, "matching_pids", side_effect=[[], [123]]), \
                mock.patch.object(standalone.subprocess, "Popen") as launch, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(standalone.wait_for_game(watch, False), 123)
            launch.assert_not_called()

    def test_lml_requires_explicit_selection_and_adds_its_pins(self):
        ordinary = standalone.configured_watcher()
        lml = standalone.configured_watcher(lml=True)
        self.assertEqual(ordinary.HELPER.name, "install_loader_hook.py")
        self.assertEqual(ordinary.INSTALLER_ENTRY, "install_queue_game_standalone")
        self.assertEqual(lml.HELPER.name, "install_lml_bootstrap.py")
        self.assertEqual(lml.INSTALLER_ENTRY, "install_lml_game_standalone")
        self.assertEqual(len(ordinary.runtime_checks().RUNTIME_SHA256), 8)
        self.assertEqual(len(lml.runtime_checks().RUNTIME_SHA256), 11)

    def test_cli_rejects_executable_pid_and_unapproved_game_arguments(self):
        for extra in (["--game", "other.exe"], ["--pid", "123"], ["--", "arbitrary"]):
            with self.subTest(arguments=extra), \
                    mock.patch.object(sys, "argv", ["standalone", *extra]), \
                    mock.patch.object(standalone, "configured_watcher") as configure, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    standalone.main(True)
                self.assertEqual(error.exception.code, 2)
                configure.assert_not_called()


class PidImportTests(unittest.TestCase):
    """Synthetic image/readback data only; these tests never access a process."""

    def setUp(self):
        self.base = 0x6FFFFC000000
        self.original = self.base + game_profiles.PID_RVA
        self.routine = bytes(range(24))
        image = bytearray(0xA00)
        struct.pack_into("<I", image, 0x3C, 0x80)
        struct.pack_into("<H", image, 0x86, 1)
        struct.pack_into("<H", image, 0x94, 0xF0)
        struct.pack_into("<IIII", image, 0x188 + 8, 0x800, 0x20000, 0x800, 0x200)
        image[0x850:0x868] = self.routine
        self.image = bytes(image)
        self.process = object()
        self.checks = SimpleNamespace(CX=Path("/owned-fixture-runtime"),
                                      read=mock.Mock(), validate_image=mock.Mock())
        self.kernel32 = self.checks.CX / "lib/wine/x86_64-windows/kernel32.dll"
        self.mappings = "n" + str(self.kernel32)

    def validate(self, original=None, routine=None, mappings=None):
        self.checks.read.side_effect = [struct.pack("<Q", self.original if original is None else original),
                                        self.routine if routine is None else routine]
        with mock.patch.object(Path, "read_bytes", return_value=self.image), \
                mock.patch.object(Path, "is_file", return_value=True), \
                mock.patch.object(Path, "is_symlink", return_value=False), \
                mock.patch.object(game_profiles.hashlib, "sha256") as digest:
            digest.return_value.hexdigest.return_value = game_profiles.KERNEL32_SHA256
            return game_profiles.validate_pid_import(
                self.process, self.checks, self.mappings if mappings is None else mappings)

    def test_valid_bound_import_and_routine(self):
        self.assertEqual(self.validate(), (self.original, self.base))
        self.checks.validate_image.assert_called_once_with(self.process, self.base, self.kernel32)
        self.assertEqual(self.checks.read.call_args_list,
                         [mock.call(self.process, game_profiles.GAME_PID_SLOT, 8),
                          mock.call(self.process, self.original, 24)])

    def test_unresolved_import_is_rejected(self):
        for original in (0x1000, 0x7FFC18BA36E0):
            with self.subTest(original=hex(original)), \
                    self.assertRaisesRegex(RuntimeError, "has not resolved"):
                self.validate(original=original)
        self.checks.validate_image.assert_not_called()

    def test_invalid_mapped_image_is_rejected_before_routine_read(self):
        self.checks.validate_image.side_effect = RuntimeError("Mapped PE identity differs")
        with self.assertRaisesRegex(RuntimeError, "Mapped PE identity differs"):
            self.validate()
        self.checks.validate_image.assert_called_once_with(self.process, self.base, self.kernel32)
        self.checks.read.assert_called_once_with(self.process, game_profiles.GAME_PID_SLOT, 8)

    def test_altered_mapped_routine_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Mapped original PID routine differs"):
            self.validate(routine=b"\xCC" * 24)

    def test_absent_kernel32_mapping_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "mapping absent"):
            self.validate(mappings="")
        self.checks.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
