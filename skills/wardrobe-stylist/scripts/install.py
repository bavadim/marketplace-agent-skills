#!/usr/bin/env python3
"""Install only this skill, keeping a recoverable backup of any prior version."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def install(destination):
    source = Path(__file__).resolve().parent.parent
    root = Path(destination).expanduser().resolve()
    target = root / source.name
    if source == target:
        return {"installed": str(target), "status": "already_installed"}
    if target.is_symlink():
        raise ValueError("Refusing to replace a symlink skill directory")
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".wardrobe-stylist-install-", dir=root))
    backup = None
    replaced = False
    try:
        staged = staging / source.name
        shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        if manifest(staged) != manifest(source):
            raise ValueError("Staged copy verification failed")
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            backup = root.parent / "skill-backups" / source.name / stamp
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        staged.rename(target)
        replaced = True
        if manifest(target) != manifest(source):
            raise ValueError("Installed copy verification failed")
    except Exception:
        if backup and backup.exists():
            if target.exists():
                shutil.rmtree(target)
            backup.rename(target)
        elif replaced and target.exists():
            shutil.rmtree(target)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"installed": str(target), "backup": str(backup) if backup else None,
            "verified_files": len(manifest(target)), "status": "installed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", default=str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"))
    args = parser.parse_args()
    print(json.dumps(install(args.destination), ensure_ascii=False))
