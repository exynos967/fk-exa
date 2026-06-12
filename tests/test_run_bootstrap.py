import importlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _import_run_with_stubbed_check_call():
    commands = []

    def fake_check_call(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return 0

    sys.modules.pop("run", None)
    with (
        patch("os.execv"),
        patch("subprocess.check_call", side_effect=fake_check_call),
    ):
        module = importlib.import_module("run")
    return module, commands


class RunBootstrapTests(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop("run", None)

    def test_importing_run_does_not_install_browsers(self) -> None:
        _, commands = _import_run_with_stubbed_check_call()

        browser_commands = [
            command
            for command in commands
            if command[:3] == [sys.executable, "-m", "camoufox"]
        ]

        self.assertEqual(browser_commands, [])

    def test_ensure_service_browsers_calls_camoufox(self) -> None:
        run, _ = _import_run_with_stubbed_check_call()

        with (
            patch.object(run, "_ensure_camoufox_browser") as ensure_camoufox,
        ):
            run._ensure_service_browsers("exa")

        ensure_camoufox.assert_called_once_with()

    def test_camoufox_browser_ready_uses_cli_path(self) -> None:
        run, _ = _import_run_with_stubbed_check_call()

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "camoufox").write_text("ok", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[sys.executable, "-m", "camoufox", "path"],
                returncode=0,
                stdout=f"{temp_dir}\n",
                stderr="",
            )

            with patch.object(run.subprocess, "run", return_value=completed) as mock_run:
                self.assertTrue(run._camoufox_browser_ready())

        mock_run.assert_called_once()

    def test_camoufox_browser_ready_accepts_file_path(self) -> None:
        run, _ = _import_run_with_stubbed_check_call()

        with tempfile.TemporaryDirectory() as temp_dir:
            binary_path = Path(temp_dir, "camoufox-bin")
            binary_path.write_text("ok", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                args=[sys.executable, "-m", "camoufox", "path"],
                returncode=0,
                stdout=f"{binary_path}\n",
                stderr="",
            )

            with patch.object(run.subprocess, "run", return_value=completed):
                self.assertTrue(run._camoufox_browser_ready())



if __name__ == "__main__":
    unittest.main()
