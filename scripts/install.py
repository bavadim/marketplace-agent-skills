"""Install selected repository skills and their required companions."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from repository import ROOT, RepoError, check_output_location, dependency_order, discover_skills, publication_files, sha256


def install(root, destination, requested=None, replace=False):
    root = Path(root).resolve()
    skills = discover_skills(root)
    names = dependency_order(skills, requested or [entry["name"] for entry in skills])
    destination = check_output_location(destination, root)
    files = publication_files(root, skills)
    existing = [name for name in names if (destination / name).exists() or (destination / name).is_symlink()]
    if existing and not replace:
        raise RepoError("Already installed; use --replace to back up and replace: " + ", ".join(existing))
    destination.mkdir(parents=True, exist_ok=True)
    if existing:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = destination.parent / "skill-backups" / stamp
    else:
        backup = None
    moved, installed = [], []
    with tempfile.TemporaryDirectory(prefix="skill-stage-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        for name in names:
            source = root / "skills" / name
            (stage / name).mkdir()
            for path in files:
                if not path.is_relative_to(source):
                    continue
                target = stage / name / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                if sha256(path) != sha256(target):
                    raise RepoError(f"Copy verification failed for {name}")
            if not (stage / name / "SKILL.md").is_file() or not (stage / name / "LICENSE").is_file():
                raise RepoError(f"Missing installation payload for {name}")
        try:
            if backup:
                backup.mkdir(parents=True)
            for name in existing:
                os.replace(destination / name, backup / name)
                moved.append(name)
            for name in names:
                os.replace(stage / name, destination / name)
                installed.append(name)
        except BaseException:
            for name in reversed(installed):
                shutil.rmtree(destination / name)
            for name in reversed(moved):
                os.replace(backup / name, destination / name)
            raise
    return {"ok": True, "installed": [str(destination / name) for name in names],
            "backup": str(backup) if backup else None,
            "message": "Installed skills are available on the next agent turn."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills", nargs="+", help="Default: all skills found under skills/")
    default_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser.add_argument("--dest", type=Path, default=default_home / "skills")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        report = install(ROOT, args.dest, args.skills, args.replace)
    except (RepoError, OSError, ValueError) as error:
        report = {"ok": False, "error": str(error)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
