#!/usr/bin/env python3
"""Provider-neutral account connection entrypoint."""
from pathlib import Path
import sys

runtime = Path(__file__).resolve().parent / 'marketplace'
if not (runtime / 'cli.py').is_file():
    raise SystemExit('Marketplace MCP runtime is missing.')
sys.path.insert(0, str(runtime))
from cli import main

if __name__ == '__main__':
    from launcher import launch
    launch()
    sys.exit(main('accounts'))
