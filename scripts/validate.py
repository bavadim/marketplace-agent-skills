"""Validate the installable skill repository without reading runtime artifacts."""
import argparse
import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from repository import ROOT, RepoError, discover_skills, publication_files


def frontmatter(path, yaml):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
    if not match:
        raise RepoError(f"Missing skill frontmatter: {path.name}")
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict):
        raise RepoError("Skill frontmatter must be a mapping")
    return data


def validate(root=ROOT):
    try:
        import yaml
    except ImportError as error:
        raise RepoError("Install requirements.txt for validation (PyYAML is missing)") from error
    root = Path(root).resolve()
    skills = discover_skills(root)
    files = publication_files(root, skills)
    issues = []
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    for entry in skills:
        directory = root / entry["path"]
        skill_md = directory / "SKILL.md"
        try:
            data = frontmatter(skill_md, yaml)
            if data.get("name") != entry["name"]:
                issues.append(f"{entry['path']}/SKILL.md: name differs from directory")
            description = data.get("description", "")
            if not isinstance(description, str) or not description.strip() or len(description) > 1024:
                issues.append(f"{entry['path']}/SKILL.md: invalid discovery description")
            metadata_path = directory / "agents/openai.yaml"
            metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            interface = metadata.get("interface", {})
            if not interface.get("display_name") or not 25 <= len(interface.get("short_description", "")) <= 64:
                issues.append(f"{entry['path']}/agents/openai.yaml: invalid UI name/description")
            if f"${entry['name']}" not in interface.get("default_prompt", ""):
                issues.append(f"{entry['path']}/agents/openai.yaml: prompt must reference the skill")
            implicit = metadata.get("policy", {}).get("allow_implicit_invocation", True)
            if not isinstance(implicit, bool):
                issues.append(f"{entry['path']}/agents/openai.yaml: implicit invocation must be boolean")
            if (directory / "LICENSE").read_text(encoding="utf-8") != license_text:
                issues.append(f"{entry['path']}/LICENSE: differs from repository MIT license")
        except (OSError, ValueError, AttributeError, yaml.YAMLError):
            issues.append(f"{entry['path']}: invalid or missing skill metadata/license")

    credential_patterns = {
        "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "credential-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b"),
        "private-home-path": re.compile(r"/home/(?!USER(?:/|\b)|username(?:/|\b)|example(?:/|\b))[\w.-]+/", re.I),
        "embedded-url-password": re.compile(r"https?://[^\s/:]+:[^\s/@]{8,}@"),
    }
    js_files, shell_files = [], []
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError:
            issues.append(f"{relative}: not UTF-8 text")
            continue
        for category, pattern in credential_patterns.items():
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                issues.append(f"{relative}:{line}: {category}")
        try:
            if path.suffix == ".py":
                ast.parse(text, filename=relative)
            elif path.suffix == ".json":
                json.loads(text)
            elif path.suffix in (".yaml", ".yml"):
                yaml.safe_load(text)
            elif path.suffix in (".js", ".cjs"):
                js_files.append(path)
            elif path.suffix == ".sh":
                shell_files.append(path)
        except (SyntaxError, ValueError, yaml.YAMLError):
            issues.append(f"{relative}: syntax/serialization error")
        if path.suffix == ".md":
            # Ignore fenced examples; inline local Markdown links must resolve.
            markdown = re.sub(r"```.*?```", "", text, flags=re.S)
            for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)\)", markdown):
                target = match.group(1).strip("<>")
                if target.startswith("#") or urlsplit(target).scheme:
                    continue
                local = (path.parent / unquote(target.split("#", 1)[0])).resolve()
                if not local.is_relative_to(root) or not local.exists():
                    line = text.count("\n", 0, match.start()) + 1
                    issues.append(f"{relative}:{line}: unresolved local Markdown link")
    node = shutil.which("node")
    if js_files and not node:
        issues.append("Node.js is required for JavaScript syntax checks")
    elif node:
        for path in js_files:
            result = subprocess.run([node, "--check", str(path)], text=True, capture_output=True, timeout=20)
            if result.returncode:
                issues.append(f"{path.relative_to(root)}: JavaScript syntax error")
    bash = shutil.which("bash")
    if shell_files and not bash:
        issues.append("Bash is required for shell syntax checks")
    elif bash:
        for path in shell_files:
            result = subprocess.run([bash, "-n", str(path)], text=True, capture_output=True, timeout=20)
            if result.returncode:
                issues.append(f"{path.relative_to(root)}: Bash syntax error")
    return {"ok": not issues, "skills": len(skills), "files": len(files), "issues": issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        report = validate(args.root)
    except (RepoError, OSError, ValueError) as error:
        report = {"ok": False, "issues": [str(error)]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
