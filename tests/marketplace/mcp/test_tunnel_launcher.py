import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[3] / 'marketplace-tunnel.sh'


class TunnelLauncherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = Path(tmp.name)
        self.env = dict(os.environ, PATH=f'{p}:{os.environ["PATH"]}', MMCP_SSH_STATE=str(p / 'state'))
        for name, body in {
            'ssh': '''for arg; do
  case "$arg" in
    -f*|ForkAfterAuthentication=yes) echo 'SSH would detach' >&2; exit 99 ;;
  esac
done
cmd=${!#}
case "$cmd" in
  'python3 - '*) cat >/dev/null ;;
  *) exec bash -c "$cmd" ;;
esac
''',
            'curl': '''printf '%s' "${TEST_MCP_STATUS:-401}"\n''',
        }.items():
            f = p / name
            f.write_text('#!/usr/bin/env bash\n' + body)
            f.chmod(0o700)
        self.command = [str(LAUNCHER), 'pilot@example.test', '/home/pilot/.mmcp-pilot/test.sock']

    def test_ready_tunnel_stays_in_foreground(self):
        with subprocess.Popen(self.command, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True) as proc, selectors.DefaultSelector() as sel:
            try:
                assert proc.stdout is not None
                sel.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(sel.select(timeout=6), 'no tunnel status')
                self.assertIn('SSH tunnel ready:', proc.stdout.readline())
                self.assertIsNone(proc.poll())
                self.assertEqual(proc.communicate(timeout=6)[0], '')
                self.assertEqual(proc.returncode, 0)
            finally:
                if proc.poll() is None:
                    proc.kill()

    def test_http_startup_failure_returns_nonzero_without_ready_message(self):
        result = subprocess.run(self.command, env=dict(self.env, TEST_MCP_STATUS='502'),
                                input='', capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('SSH tunnel ready:', result.stdout)
        self.assertIn('502', result.stderr)
