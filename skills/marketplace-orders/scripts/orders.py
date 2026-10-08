#!/usr/bin/env python3
"""Combined orders API, backed by the shared provider factory."""
from pathlib import Path
import sys
runtime=Path(__file__).resolve().parents[2]/'marketplace-shopping/assets/marketplace'
if not (runtime/'cli.py').is_file():raise SystemExit('Install marketplace-shopping alongside marketplace-orders.')
sys.path.insert(0,str(runtime))
from cli import main
if __name__ == '__main__':
    from launcher import launch
    launch()
    sys.exit(main('orders'))
