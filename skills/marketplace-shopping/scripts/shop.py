#!/usr/bin/env python3
"""Shopping API; bundled runtime lives in assets."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets/marketplace'))
from cli import main
if __name__ == '__main__':
    from launcher import launch
    launch()
    sys.exit(main('shopping'))
