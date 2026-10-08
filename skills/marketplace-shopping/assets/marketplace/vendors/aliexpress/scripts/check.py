#!/usr/bin/env python3
"""Run offline regression tests without accessing an account or a real cart."""
import argparse
from pathlib import Path
import shutil
import subprocess
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    tests = Path(__file__).resolve().parents[1] / 'tests'
    suite = unittest.defaultTestLoader.discover(str(tests), pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    node = shutil.which('node')
    if node is None:
        print('Node.js is required to complete the browser contract tests.')
        return 2
    browser = subprocess.run([node, str(tests / 'test_browser.cjs')], check=False)
    return 0 if result.wasSuccessful() and browser.returncode == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
