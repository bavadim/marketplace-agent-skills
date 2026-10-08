"""Run each offline suite in its own process to isolate legacy module names."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from repository import ROOT


def main():
    engine = ROOT / "skills/marketplace-shopping/assets/marketplace"
    suites = [
        ("repository", ROOT / "scripts/tests", ROOT / "scripts"),
        ("shopping", ROOT / "skills/marketplace-shopping/tests", engine),
        ("orders", ROOT / "skills/marketplace-orders/tests", engine),
        ("accounts", ROOT / "skills/marketplace-accounts/tests", engine),
        ("wardrobe", ROOT / "skills/wardrobe-stylist/tests", ROOT / "skills/wardrobe-stylist/scripts"),
        ("aliexpress", engine / "vendors/aliexpress/tests", engine / "vendors/aliexpress/scripts"),
        ("ozon", engine / "vendors/ozon/tests", engine / "vendors/ozon/scripts"),
    ]
    commands = []
    for name, directory, imports in suites:
        commands.append((name, [sys.executable, "-m", "unittest", "discover", "-s", str(directory), "-p", "test_*.py"], imports))
    node = shutil.which("node")
    if not node:
        print(json.dumps({"ok": False, "error": "Node.js is required for CAPTCHA/browser helper tests"}))
        return 1
    commands += [
        ("captcha", [node, str(ROOT / "skills/captcha-assist/scripts/test_challenge.cjs")], None),
        ("ali-browser-helper", [node, str(engine / "vendors/aliexpress/tests/test_browser.cjs")], None),
    ]
    failed = []
    for name, command, imports in commands:
        env = dict(os.environ, OZON_BROWSER_SMOKE="0", MARKETPLACE_BROWSER_SMOKE="0", PYTHONDONTWRITEBYTECODE="1")
        if imports:
            env["PYTHONPATH"] = str(imports)
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            print(f"{name}: {'PASS' if result.returncode == 0 else 'FAIL'}", flush=True)
            output = (result.stdout + result.stderr).strip()
            if output:
                print(output, flush=True)
            if result.returncode:
                failed.append(name)
        except subprocess.TimeoutExpired:
            failed.append(name)
            print(f"{name}: FAIL (offline suite exceeded 180 seconds)", flush=True)
    print(json.dumps({"ok": not failed, "suites": len(commands), "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
