"""Shared, dependency-free rules for the distributable repository."""
import hashlib
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (".gitignore", "README.md", "LICENSE", "requirements.txt")
# These two entrypoints import the shared engine from their sibling skill.
REQUIRED_COMPANIONS = {
    "marketplace-accounts": ("marketplace-shopping",),
    "marketplace-orders": ("marketplace-shopping",),
}
TEXT_SUFFIXES = {".py", ".sh", ".js", ".cjs", ".md", ".yaml", ".yml", ".json", ".txt", ".html", ".css"}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
PRIVATE_DIRS = {".git", ".secrets", ".runtime", ".state", ".venv", ".codex", ".agents", ".aws", ".ssh", ".gnupg",
                "node_modules", "browser-profile", "browser-profiles", "chromium-profile", "chromium-profiles", "profiles", "sessions", "runs"}
PRIVATE_NAMES = {"session.json", "session-state.json", "cookies.sqlite", "Cookies", "Local State", "Login Data", "Web Data",
                 "Preferences", "Secure Preferences", "History", "Network Persistent State", "Xauthority",
                 "SingletonCookie", "SingletonLock", "SingletonSocket", "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys"}


class RepoError(ValueError):
    pass


def discover_skills(root=ROOT):
    """Discover installable folders directly; no separate registry is needed."""
    directory = Path(root) / "skills"
    if directory.is_symlink() or not directory.is_dir():
        raise RepoError("The skills root must be a real directory")
    entries = []
    for path in sorted(directory.iterdir()):
        if path.name in CACHE_DIRS | PRIVATE_DIRS or private_name(path.name):
            continue
        if (path.is_symlink() or not path.is_dir() or
                not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", path.name)):
            raise RepoError(f"Invalid skill directory: {path.name}")
        skill_md = path / "SKILL.md"
        if not skill_md.is_file() or skill_md.is_symlink():
            raise RepoError(f"Missing or unsupported SKILL.md: {path.name}")
        entries.append({"name": path.name, "path": f"skills/{path.name}",
                        "requires": REQUIRED_COMPANIONS.get(path.name, ())})
    if not entries:
        raise RepoError("No installable skills found")
    dependency_order(entries, [entry["name"] for entry in entries])
    return entries


def dependency_order(skills, requested):
    entries = {entry["name"]: entry for entry in skills}
    ordered, active = [], set()

    def visit(name):
        if name not in entries:
            raise RepoError(f"Unknown skill: {name}")
        if name in active:
            raise RepoError(f"Dependency cycle at {name}")
        if name in ordered:
            return
        active.add(name)
        for required in entries[name].get("requires", []):
            visit(required)
        active.remove(name)
        ordered.append(name)

    for name in requested:
        visit(name)
    return ordered


def private_name(name):
    lower = name.lower()
    return (name in PRIVATE_NAMES or lower.startswith((".env", "storage-state", "storage_state", "credentials", "auth-state", "auth_state")) or
            "cookies" in lower and lower.endswith((".json", ".txt", ".sqlite", ".sqlite-wal", ".sqlite-shm")) or
            lower.startswith(("cookies-", "login data-", "web data-", "history-")) or
            bool(re.fullmatch(r"(?:session[-_].*|.*-session(?:-.*)?)\.json", lower)) or
            lower.endswith((".pem", ".key", ".cookie", ".p12", ".pfx", ".der", ".jks", ".keytab")))


def publication_files(root=ROOT, skills=None):
    """Explicit roots, text-only files; exclude recognized local private state."""
    root = Path(root).resolve()
    skills = skills if skills is not None else discover_skills(root)
    result = []
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RepoError(f"Missing or unsupported root file: {name}")
        result.append(path)

    def walk(path):
        if path.name in PRIVATE_DIRS or private_name(path.name):
            return
        if path.is_symlink():
            raise RepoError(f"Symlinks are not distributable: {path.relative_to(root)}")
        if path.is_dir():
            if path.name in CACHE_DIRS:
                return
            if path.name.startswith(".") and path.name != ".github":
                raise RepoError(f"Unsupported hidden directory in source: {path.relative_to(root)}")
            for child in sorted(path.iterdir()):
                walk(child)
        elif path.is_file():
            if path.suffix in (".pyc", ".pyo"):
                return
            if path.suffix not in TEXT_SUFFIXES and path.name != "LICENSE":
                raise RepoError(f"Unsupported distributable file: {path.relative_to(root)}")
            result.append(path)
        else:
            raise RepoError(f"Unsupported source entry: {path.relative_to(root)}")

    for name in ("scripts", ".github"):
        directory = root / name
        if not directory.is_dir():
            raise RepoError(f"Missing repository directory: {name}")
        walk(directory)
    for entry in skills:
        directory = root / entry["path"]
        if not directory.is_dir():
            raise RepoError(f"Missing skill directory: {entry['name']}")
        walk(directory)
    return sorted(result, key=lambda path: path.relative_to(root).as_posix())


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_output_location(destination, root=ROOT):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or any(destination.is_relative_to(root / name)
                                  for name in ("skills", "scripts", ".github")):
        raise RepoError("Output must be outside the repository source directories")
    return destination
