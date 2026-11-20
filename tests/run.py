"""Run offline suites in isolated subprocesses (vendor module names overlap)."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / 'mcp_servers/marketplace'
ENGINE = MARKET / 'runtime/marketplace'


def main():
    suites = [
        ('dream', 'tests/dream', ROOT),
        ('marketplace MCP', 'tests/marketplace/mcp', MARKET),
        ('marketplace runtime', 'tests/marketplace/runtime', ENGINE),
        ('Ozon', 'tests/marketplace/ozon', ENGINE / 'vendors/ozon/scripts'),
        ('AliExpress', 'tests/marketplace/aliexpress', ENGINE / 'vendors/aliexpress/scripts'),
    ]
    commands = [(name, [sys.executable, '-m', 'unittest', 'discover', '-s', directory], imports)
                for name, directory, imports in suites]
    commands += [(name, [sys.executable, script, '--self-test'], ROOT) for name, script in (
        ('stylist', 'skills/wardrobe-stylist/scripts/style.py'),
        ('forecast', 'skills/dream-daily-analysis/scripts/forecast.py'),
    )]
    commands.append(('AliExpress browser helpers', ['node', 'tests/marketplace/aliexpress/test_browser.cjs'], ROOT))
    failures = []
    for name, command, imports in commands:
        print(f'\n== {name} ==', flush=True)
        env = dict(os.environ, PYTHONPATH=str(imports), OZON_BROWSER_SMOKE='0', MARKETPLACE_BROWSER_SMOKE='0')
        try:
            if subprocess.run(command, cwd=ROOT, env=env, timeout=180).returncode:
                failures.append(name)
        except (OSError, subprocess.TimeoutExpired) as error:
            print(error, file=sys.stderr)
            failures.append(name)
    if failures:
        print('Failed: ' + ', '.join(failures), file=sys.stderr)
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
