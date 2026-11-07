import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dotfiles-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "user"
        self.home.mkdir()
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def install(self, profile="portable", dry_run=False):
        installer.install(self.home, profile, dry_run)

    def backups(self):
        return sorted((self.home / ".local/state/arch-hyprland-dotfiles/backups").glob("*"))

    def test_preview_writes_nothing(self):
        self.install(dry_run=True)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_install_renders_paths_and_is_idempotent(self):
        self.install()
        config = self.home / ".config/waybar/config.jsonc"
        value = config.read_text()
        self.assertNotIn("@HOME@", value)
        self.assertIn(str(self.home), value)
        wallpaper = self.home / "Pictures/wallpapers/dotfiles-default.jpeg"
        self.assertEqual(wallpaper.read_bytes(), (ROOT / "assets/wallpaper.jpeg").read_bytes())
        self.assertIn('output = ""', (self.home / ".config/hypr/monitors.lua").read_text())
        self.assertTrue((self.home / ".config/waybar/scripts/wallpaper-picker.sh").stat().st_mode & 0o111)
        self.install()
        self.assertEqual(len(self.backups()), 1)

    def test_restore_preserves_original_runtime_and_later_changes(self):
        kitty = self.home / ".config/kitty/kitty.conf"
        kitty.parent.mkdir(parents=True)
        kitty.write_text("original")
        kitty.chmod(0o600)
        tasks = self.home / ".config/waybar/.todo/tasks.sqlite3"
        tasks.parent.mkdir(parents=True)
        tasks.write_bytes(b"private tasks")
        wallpaper = self.home / "Pictures/wallpapers/dotfiles-default.jpeg"
        wallpaper.parent.mkdir(parents=True)
        wallpaper.write_bytes(b"existing wallpaper")
        self.install()
        self.assertEqual(tasks.read_bytes(), b"private tasks")
        self.assertEqual(wallpaper.read_bytes(), b"existing wallpaper")
        kitty.write_text("edited after installation")
        backup = self.backups()[0]
        installer.restore(backup, dry_run=True)
        self.assertEqual(kitty.read_text(), "edited after installation")
        installer.restore(backup)
        self.assertEqual(kitty.read_text(), "original")
        self.assertEqual(kitty.stat().st_mode & 0o777, 0o600)
        self.assertEqual((backup / "before-restore/.config/kitty/kitty.conf").read_text(),
                         "edited after installation")
        self.assertFalse((self.home / ".config/hypr/hyprland.lua").exists())
        self.assertEqual(tasks.read_bytes(), b"private tasks")
        kitty.write_text("after restore")
        installer.restore(backup)
        self.assertEqual(kitty.read_text(), "after restore")

    def test_leaf_symlink_is_backed_up_without_overwriting_its_target(self):
        external = Path(self.temp.name) / "original.conf"
        external.write_text("do not touch")
        kitty = self.home / ".config/kitty/kitty.conf"
        kitty.parent.mkdir(parents=True)
        kitty.symlink_to(external)
        self.install()
        self.assertFalse(kitty.is_symlink())
        self.assertEqual(external.read_text(), "do not touch")
        installer.restore(self.backups()[0])
        self.assertTrue(kitty.is_symlink())
        self.assertEqual(kitty.readlink(), external)

    def test_directory_symlink_refused_before_any_changes(self):
        external = Path(self.temp.name) / "outside"
        external.mkdir()
        (self.home / ".config").mkdir()
        (self.home / ".config/waybar").symlink_to(external)
        with self.assertRaisesRegex(ValueError, "симлинк"):
            self.install()
        self.assertEqual(list(external.iterdir()), [])
        self.assertFalse((self.home / ".config/hypr").exists())
        self.assertEqual(self.backups(), [])

    def test_laptop_profile_uses_original_monitor_layout(self):
        self.install(profile="laptop")
        value = (self.home / ".config/hypr/monitors.lua").read_text()
        self.assertIn('mirror = "eDP-1"', value)
        self.assertIn("scale = 1.5", value)

    def test_failed_write_rolls_back_original_files(self):
        original = self.home / ".config/dunst/dunstrc"
        original.parent.mkdir(parents=True)
        original.write_text("original notifications")
        real_write = installer.atomic_write
        count = 0

        def fail_once(path, data, mode=0o644):
            nonlocal count
            if "/backups/" not in str(path):
                count += 1
                if count == 3:
                    raise OSError("simulated disk failure")
            return real_write(path, data, mode)

        with patch.object(installer, "atomic_write", side_effect=fail_once):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.install()
        self.assertEqual(original.read_text(), "original notifications")
        self.assertFalse((self.home / ".config/hypr/hyprland.lua").exists())
        self.assertTrue(json.loads((self.backups()[0] / "manifest.json").read_text())["restored"])

    def test_restore_rejects_path_traversal(self):
        self.install()
        manifest_path = self.backups()[0] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["entries"][0]["path"] = "../outside"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Некорректный путь"):
            installer.restore(self.backups()[0])
        self.assertTrue((self.home / ".config/hypr/hyprland.lua").exists())


if __name__ == "__main__":
    unittest.main()
