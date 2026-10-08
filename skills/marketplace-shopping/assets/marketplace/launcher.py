"""Enter the bundled Bash launcher once before running a Python entrypoint."""
import os
from pathlib import Path
import sys


def launch():
    if os.environ.get("_SKILL_PYTHON_LAUNCH_PID") == str(os.getpid()):
        return
    shim = Path(__file__).with_name("python.sh")
    os.execv("/bin/bash", ["bash", str(shim), sys.executable, *sys.argv])
