#!/usr/bin/env python3
"""Install the headless driver in a dedicated user environment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


# Standalone skill installations do not include the repository requirements.txt.
# The repository test checks these bootstrap pins against that single manifest.
RUNTIME_REQUIREMENTS = (
    'playwright==1.58.0',
    'patchright==1.63.0',
    'Pillow==12.1.1',
    'beautifulsoup4==4.14.3',
    'pycryptodomex==3.20.0',
    'lz4==4.4.5',
)


def runtime_python():
    root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home()/'.local/share')))
    return root/'ozon-bom/venv/bin/python'


def main():
    os.umask(0o077)
    python = runtime_python()
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(python.parent.parent)
    commands = [[str(python), '-m', 'pip', 'install', '--disable-pip-version-check', *RUNTIME_REQUIREMENTS]]
    if not (shutil.which('chromium') or shutil.which('chromium-browser')):
        commands.append([str(python), '-m', 'playwright', 'install', 'chromium'])
        commands.append([str(python), '-m', 'patchright', 'install', 'chromium'])
    for command in commands:
        run = subprocess.run(command, capture_output=True, text=True, timeout=300)
        if run.returncode:
            print(json.dumps({'status':'browser_setup_failed','step':command[2], 'exit_code':run.returncode}))
            return 2
    print(json.dumps({'status':'ready','python':str(python),'browser':'chromium-headless'}))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from launcher import launch
    launch()
    raise SystemExit(main())
