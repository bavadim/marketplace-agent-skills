"""Exercise Bash routing with synthetic id/getent/sg commands, without networking."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ENGINE = ROOT / "skills/marketplace-shopping/assets/marketplace"
sys.path.insert(0, str(ENGINE))
from providers import transport

SHIMS = [ENGINE / "python.sh", ROOT / "skills/wardrobe-stylist/scripts/python.sh"]
ENTRYPOINTS = [ROOT / f"skills/{skill}/scripts/{script}.py" for skill, script in (
    ("marketplace-shopping", "shop"), ("marketplace-accounts", "accounts"),
    ("marketplace-orders", "orders"), ("wardrobe-stylist", "style"))]
PAYLOAD = """import json,os,sys
print(json.dumps({'argv':sys.argv[1:],'stdin':sys.stdin.read(),'gid':os.getegid(),
                  'launched':os.environ.get('_SKILL_PYTHON_LAUNCH_PID') == str(os.getpid())}))
"""


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shim-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "sg.log"
        self.env = dict(os.environ, PATH=str(self.bin), PYTHONDONTWRITEBYTECODE="1",
                        SHIM_TEST_LOG=str(self.log), SHIM_TEST_GID="1000")
        self.env.pop("ISPROXYGO", None)
        self.env.pop("_SKILL_PYTHON_LAUNCH_PID", None)
        self.write_command("id", r'''#!/bin/bash
case "$1" in
-g) printf '%s\n' "${SHIM_TEST_GID:-1000}";;
-G) printf '%s\n' '1000';;
-un) printf '%s\n' 'synthetic-user';;
*) exit 2;;
esac
''')
        self.write_command("getent", r'''#!/bin/bash
[[ "$1" == group && "$2" == proxygo ]] || exit 2
[[ ${SHIM_TEST_GROUP_MISSING:-0} == 0 ]] || exit 2
printf 'proxygo:x:22222:%s\n' "${SHIM_TEST_MEMBERS-synthetic-user}"
''')
        self.write_command("sg", r'''#!/bin/bash
[[ "$1" == proxygo && "$2" == -c ]] || exit 2
printf 'called\n' >> "$SHIM_TEST_LOG"
[[ ${SHIM_TEST_SG_FAILURE:-0} == 0 ]] || exit 9
export SHIM_TEST_GID=22222
exec /bin/sh -c "$3"
''')

    def write_command(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_shim(self, shim, *args, env=None, code=PAYLOAD, stdin=""):
        return subprocess.run(["/bin/bash", str(shim), sys.executable, "-c", code, *args],
                              env=env or self.env, input=stdin, text=True, capture_output=True, timeout=10)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_default_does_not_require_or_switch_a_group(self):
        env = dict(self.env, PATH="", MARKETPLACE_NETWORK_GROUP="proxygo")
        for shim in SHIMS:
            with self.subTest(shim=shim):
                result = self.run_shim(shim, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["gid"], os.getegid())
                self.assertTrue(json.loads(result.stdout)["launched"])
        self.assertEqual(self.calls(), [])

    def test_variable_presence_selects_proxy_even_if_empty_or_zero(self):
        for shim in SHIMS:
            for value in ("1", "", "0"):
                with self.subTest(shim=shim, value=value):
                    self.log.unlink(missing_ok=True)
                    result = self.run_shim(shim, env=dict(self.env, ISPROXYGO=value))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(json.loads(result.stdout)["launched"])
                    self.assertEqual(self.calls(), ["called"])

    def test_existing_proxy_group_is_not_entered_again(self):
        result = self.run_shim(SHIMS[0], env=dict(self.env, ISPROXYGO="1", SHIM_TEST_GID="22222"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_shell_metacharacters_and_stdin_survive_both_modes(self):
        sentinel = self.root / "must-not-exist"
        args = ["", "with spaces", "quote ' and double \"", "line\none", "перец — MIDI",
                f"$(touch {sentinel}) ; `touch {sentinel}` $HOME \\tail"]
        for shim in SHIMS:
            for enabled in (False, True):
                with self.subTest(shim=shim, enabled=enabled):
                    env = dict(self.env)
                    if enabled:
                        env["ISPROXYGO"] = "1"
                    result = self.run_shim(shim, *args, env=env, stdin="{\"query\":\"томаты\"}\n")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    actual = json.loads(result.stdout)
                    self.assertEqual(actual["argv"], args)
                    self.assertEqual(actual["stdin"], "{\"query\":\"томаты\"}\n")
                    self.assertFalse(sentinel.exists())

    def test_shim_and_script_paths_with_shell_metacharacters(self):
        directory = self.root / "space ' ; unicode-путь"
        directory.mkdir()
        shim = directory / "python ' ; shim.sh"
        shim.write_bytes(SHIMS[0].read_bytes())
        script = directory / "script ' ; name.py"
        script.write_text(PAYLOAD)
        result = subprocess.run(["/bin/bash", str(shim), sys.executable, str(script), "exact argument"],
                                input="stdin", env=dict(self.env, ISPROXYGO="1"),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["argv"], ["exact argument"])

    def test_missing_group_fails_without_running_python(self):
        result = self.run_shim(SHIMS[0], env=dict(self.env, ISPROXYGO="1", SHIM_TEST_GROUP_MISSING="1"))
        self.assertEqual(result.returncode, 126)
        self.assertIn("proxygo group unavailable", result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.calls(), [])

    def test_missing_sg_fails_without_fallback(self):
        (self.bin / "sg").unlink()
        result = self.run_shim(SHIMS[0], env=dict(self.env, ISPROXYGO="1"))
        self.assertEqual(result.returncode, 126)
        self.assertIn("sg unavailable", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_nonmember_fails_without_password_prompt(self):
        result = self.run_shim(SHIMS[0], env=dict(self.env, ISPROXYGO="1", SHIM_TEST_MEMBERS="another-user"))
        self.assertEqual(result.returncode, 126)
        self.assertIn("cannot use proxygo", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_sg_failure_is_propagated_without_fallback(self):
        result = self.run_shim(SHIMS[0], env=dict(self.env, ISPROXYGO="1", SHIM_TEST_SG_FAILURE="1"))
        self.assertEqual(result.returncode, 9)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.calls(), ["called"])

    def test_interpreter_status_and_output_are_preserved(self):
        code = "import sys; print('stdout'); print('stderr',file=sys.stderr); sys.exit(7)"
        for enabled in (False, True):
            env = dict(self.env)
            if enabled:
                env["ISPROXYGO"] = "1"
            result = self.run_shim(SHIMS[0], env=env, code=code)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, "stdout\n")
            self.assertEqual(result.stderr, "stderr\n")

    def test_all_public_entrypoints_use_the_shim_and_keep_their_api(self):
        for entrypoint in ENTRYPOINTS:
            for enabled in (False, True):
                with self.subTest(entrypoint=entrypoint, enabled=enabled):
                    self.log.unlink(missing_ok=True)
                    env = dict(self.env)
                    if enabled:
                        env["ISPROXYGO"] = "1"
                    result = subprocess.run([sys.executable, str(entrypoint), "capabilities"],
                                            env=env, text=True, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIsInstance(json.loads(result.stdout), dict)
                    self.assertEqual(self.calls(), ["called"] if enabled else [])

    def test_nested_marketplace_call_does_not_repeat_sg(self):
        request = {"method": "marketplace.call", "params": {"request": {"method": "capabilities"}}}
        result = subprocess.run([sys.executable, str(ENTRYPOINTS[-1]), "call", "--request", "-"],
                                input=json.dumps(request), env=dict(self.env, ISPROXYGO="1"),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.assertEqual(self.calls(), ["called"])

    def test_provider_clis_inherit_routing_without_legacy_parameters(self):
        with patch.object(transport, "provider_lock", return_value=contextlib.nullcontext()), \
             patch.object(transport, "run_json", return_value={"status": "ok"}) as run:
            for provider in ("ozon", "aliexpress"):
                transport.legacy(provider, ["search", "--query", "synthetic"])
                self.assertEqual(run.call_args.args[1], ["search", "--query", "synthetic"])


if __name__ == "__main__":
    unittest.main()
