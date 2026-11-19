import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class LauncherTests(unittest.TestCase):
    def run_launcher(self, display, session_type, xset_status=0, autostart=False):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            xset = bin_dir / "xset"
            xset.write_text(
                '#!/bin/sh\nprintf "xset: %s\\n" "$*"\n'
                f"exit {xset_status}\n"
            )
            terminal = bin_dir / "x-terminal-emulator"
            terminal.write_text('#!/bin/sh\necho "terminal launched"\n')
            xset.chmod(0o755)
            terminal.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:/usr/bin:/bin",
                "DISPLAY": display,
                "XDG_SESSION_TYPE": session_type,
            }
            command = ["/usr/bin/bash", str(Path(__file__).resolve().parents[1] / "start_manual.sh")]
            if autostart:
                command.append("--autostart")
            return subprocess.run(
                command,
                env=env, capture_output=True, text=True, timeout=5,
            )

    def test_x11_launcher_disables_blanking_before_opening_terminal(self):
        result = self.run_launcher(":99", "x11")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("xset: s off -dpms", result.stdout)
        self.assertLess(result.stdout.index("xset:"), result.stdout.index("terminal launched"))

    def test_no_display_or_wayland_does_not_change_x11_settings(self):
        for display, session_type in (("", "tty"), (":99", "wayland")):
            with self.subTest(display=display, session_type=session_type):
                result = self.run_launcher(display, session_type)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("xset:", result.stdout)
                self.assertIn("terminal launched", result.stdout)

    def test_xset_failure_warns_but_still_launches_piamp(self):
        result = self.run_launcher(":99", "x11", xset_status=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: Could not disable display blanking/power saving.", result.stderr)
        self.assertIn("terminal launched", result.stdout)

    def test_autostart_skips_virtual_and_missing_displays(self):
        for display in (":1", ":1.0", ":10", "", "localhost:10.0"):
            with self.subTest(display=display):
                result = self.run_launcher(display, "x11", autostart=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Skipping PiAmp autostart", result.stdout)
                self.assertNotIn("terminal launched", result.stdout)
                self.assertNotIn("xset:", result.stdout)

    def test_autostart_runs_on_small_screen(self):
        for display in (":0", ":0.0", "unix:0", "unix:0.0"):
            with self.subTest(display=display):
                result = self.run_launcher(display, "x11", autostart=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("terminal launched", result.stdout)


if __name__ == "__main__":
    unittest.main()
