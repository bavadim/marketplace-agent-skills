import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


class StandaloneSetupTests(unittest.TestCase):
    def test_standalone_browser_setup_uses_shared_pins_without_requirement_file(self):
        source = ROOT / "mcp_servers/marketplace/runtime/marketplace/vendors/ozon/scripts/setup_browser.py"
        manifest = {line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#") and not line.lower().startswith("pyyaml==")}
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "setup_browser.py"
            shutil.copy2(source, copied)
            spec = importlib.util.spec_from_file_location("standalone_setup", copied)
            assert spec is not None and spec.loader is not None
            setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(setup)
            python = Path(tmp) / "venv/bin/python"
            result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with (patch.object(setup, "runtime_python", return_value=python),
                  patch.object(setup.os, "umask"), patch.object(setup.venv, "EnvBuilder") as builder,
                  patch.object(setup.shutil, "which", return_value="/synthetic/chromium"),
                  patch.object(setup.subprocess, "run", return_value=result) as run,
                  patch("sys.stdout", new_callable=io.StringIO) as output):
                self.assertEqual(setup.main(), 0)
                builder.return_value.create.assert_called_once_with(python.parent.parent)
                run.assert_called_once()
                command = run.call_args.args[0]
                self.assertEqual(command[:5], [str(python), "-m", "pip", "install", "--disable-pip-version-check"])
                self.assertEqual(set(command[5:]), manifest)
                self.assertEqual(json.loads(output.getvalue())["status"], "ready")
