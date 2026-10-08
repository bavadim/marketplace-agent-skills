"""Repository tests using temporary, synthetic checkouts only."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repository import ROOT, ROOT_FILES, RepoError, dependency_order, discover_skills, publication_files
from install import install
from validate import validate


SHOPPING = "marketplace-shopping"
ORDERS = "marketplace-orders"
CAPTCHA = "captcha-assist"


def fixture(directory):
    root = Path(directory) / "repo"
    root.mkdir()
    for name in ROOT_FILES:
        (root / name).write_text("", encoding="utf-8")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    (root / "LICENSE").write_text(license_text, encoding="utf-8")
    for name in ("scripts", ".github/workflows"):
        (root / name).mkdir(parents=True)
        (root / name / "example.md").write_text("Synthetic documentation", encoding="utf-8")
    for name in (SHOPPING, ORDERS, CAPTCHA):
        path = root / "skills" / name
        (path / "agents").mkdir(parents=True)
        (path / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Synthetic offline skill for installation tests.\n---\nInstructions.\n",
            encoding="utf-8")
        (path / "LICENSE").write_text(license_text, encoding="utf-8")
        (path / "agents/openai.yaml").write_text(
            f'interface:\n  display_name: "{name}"\n  short_description: "Synthetic offline installation test"\n  default_prompt: "Use ${name} for this test."\n',
            encoding="utf-8")
    return root


class RepositoryTests(unittest.TestCase):
    def test_required_closure_is_ordered_optional_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            skills = discover_skills(fixture(tmp))
            self.assertEqual(dependency_order(skills, [ORDERS, SHOPPING]), [SHOPPING, ORDERS])
            with self.assertRaises(RepoError):
                dependency_order(skills, ["unknown"])

    def test_cycles_are_rejected(self):
        skills = [{"name": "one", "requires": ["two"]}, {"name": "two", "requires": ["one"]}]
        with self.assertRaises(RepoError):
            dependency_order(skills, ["one"])

    def test_discovery_rejects_invalid_folders_and_missing_companions(self):
        for fault in ("invalid-name", "missing-instructions", "missing-companion"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = fixture(tmp)
                if fault == "invalid-name":
                    (root / "skills/not.a.skill").mkdir()
                elif fault == "missing-instructions":
                    (root / "skills" / CAPTCHA / "SKILL.md").unlink()
                else:
                    shutil.rmtree(root / "skills" / SHOPPING)
                with self.assertRaises(RepoError):
                    discover_skills(root)

    def test_publication_ignores_workspace_data_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(tmp)
            (root / "runs").mkdir()
            (root / "runs/photo.png").write_bytes(b"PRIVATE_SYNTHETIC")
            cache = root / "skills" / SHOPPING / "__pycache__"
            cache.mkdir()
            (cache / "runtime.pyc").write_bytes(b"cache")
            files = publication_files(root)
            self.assertFalse(any("runs" in path.parts or "__pycache__" in path.parts for path in files))

    def test_local_private_state_is_excluded_from_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(tmp)
            private = ("session.json", "session-cache.json", "storage-state.json", "cookies.txt", "account.key")
            for name in private:
                (root / "skills" / SHOPPING / name).write_text("PRIVATE_SYNTHETIC_ONLY")
            cache = root / "skills" / SHOPPING / ".runtime/profiles/default"
            cache.mkdir(parents=True)
            (cache / "Cookies").write_text("PRIVATE_SYNTHETIC_ONLY")
            shared_state = root / "skills/.secrets"
            shared_state.mkdir()
            (shared_state / "session.json").write_text("PRIVATE_SYNTHETIC_ONLY")
            destination = Path(tmp) / "installed"
            install(root, destination, [SHOPPING])
            for name in private:
                self.assertFalse((destination / SHOPPING / name).exists())
            self.assertFalse((destination / SHOPPING / ".runtime").exists())
            self.assertFalse(any("PRIVATE_SYNTHETIC_ONLY" in path.read_text()
                                 for path in publication_files(root)))

    def test_unsupported_binary_media_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(tmp)
            (root / "skills" / SHOPPING / "product.png").write_bytes(b"SYNTHETIC_ONLY")
            with self.assertRaises(RepoError):
                publication_files(root)

    def test_symlinks_are_rejected(self):
        for folder_link in (False, True):
            with self.subTest(folder_link=folder_link), tempfile.TemporaryDirectory() as tmp:
                root = fixture(tmp)
                if folder_link:
                    outside = Path(tmp) / "outside-skill"
                    (root / "skills" / CAPTCHA).rename(outside)
                    (root / "skills" / CAPTCHA).symlink_to(outside, target_is_directory=True)
                else:
                    (root / "skills" / SHOPPING / "linked.md").symlink_to(root / "README.md")
                with self.assertRaises(RepoError):
                    publication_files(root)

    def test_install_includes_required_payload_preserves_other_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, destination = fixture(tmp), Path(tmp) / "installed"
            other = destination / "unrelated"
            other.mkdir(parents=True)
            (other / "keep.txt").write_text("keep")
            report = install(root, destination, [ORDERS])
            self.assertTrue(report["ok"])
            self.assertEqual([Path(path).name for path in report["installed"]], [SHOPPING, ORDERS])
            self.assertEqual((destination / SHOPPING / "LICENSE").read_bytes(), (root / "LICENSE").read_bytes())
            self.assertTrue((destination / ORDERS / "SKILL.md").is_file())
            self.assertFalse((destination / CAPTCHA).exists())
            self.assertEqual((other / "keep.txt").read_text(), "keep")

    def test_existing_destination_is_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, destination = fixture(tmp), Path(tmp) / "installed"
            install(root, destination, [SHOPPING])
            marker = destination / SHOPPING / "local.txt"
            marker.write_text("preserve")
            with self.assertRaises(RepoError):
                install(root, destination, [ORDERS])
            self.assertEqual(marker.read_text(), "preserve")
            self.assertFalse((destination / ORDERS).exists())

    def test_replacement_keeps_backup_outside_discovery_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, destination = fixture(tmp), Path(tmp) / "installed"
            install(root, destination, [SHOPPING])
            (destination / SHOPPING / "local.txt").write_text("original")
            report = install(root, destination, [ORDERS], replace=True)
            backup = Path(report["backup"])
            self.assertFalse(backup.is_relative_to(destination))
            self.assertEqual((backup / SHOPPING / "local.txt").read_text(), "original")
            self.assertFalse((destination / SHOPPING / "local.txt").exists())

    def test_failed_transaction_restores_all_original_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, destination = fixture(tmp), Path(tmp) / "installed"
            install(root, destination, [ORDERS])
            for name in (SHOPPING, ORDERS):
                (destination / name / "local.txt").write_text(name)
            import install as installer
            real_replace = installer.os.replace

            def failing_replace(source, target):
                if "skill-stage-" in str(source) and Path(target) == destination / ORDERS:
                    raise OSError("Synthetic move failure")
                real_replace(source, target)

            with patch.object(installer.os, "replace", side_effect=failing_replace):
                with self.assertRaises(OSError):
                    install(root, destination, [ORDERS], replace=True)
            for name in (SHOPPING, ORDERS):
                self.assertEqual((destination / name / "local.txt").read_text(), name)

    def test_installation_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(tmp)
            with self.assertRaises(RepoError):
                install(root, root / "skills", replace=True)

    def test_validation_reports_metadata_links_syntax_and_secret_without_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = fixture(tmp)
            secret = "ghp_" + "a" * 36
            (root / "README.md").write_text(f"[missing](missing.md)\n{secret}\n")
            (root / "scripts/broken.py").write_text("def broken(\n")
            (root / "skills" / ORDERS / "agents/openai.yaml").write_text('interface:\n  short_description: "tiny"\n')
            report = validate(root)
            self.assertFalse(report["ok"])
            errors = json.dumps(report["issues"])
            for expected in ("credential-token", "unresolved local Markdown link", "syntax", "UI name/description"):
                self.assertIn(expected, errors)
            self.assertNotIn(secret, errors)

    def test_standalone_browser_setup_uses_shared_pins_without_requirement_file(self):
        source = ROOT / "skills/marketplace-shopping/assets/marketplace/vendors/ozon/scripts/setup_browser.py"
        manifest = {line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.startswith("#") and not line.lower().startswith("pyyaml==")}
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "setup_browser.py"
            shutil.copy2(source, copied)
            spec = importlib.util.spec_from_file_location("standalone_setup", copied)
            setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(setup)
            python = Path(tmp) / "venv/bin/python"
            result = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with (patch.object(setup, "runtime_python", return_value=python),
                  patch.object(setup.os, "umask"), patch.object(setup.venv, "EnvBuilder") as builder,
                  patch.object(setup.shutil, "which", return_value="/synthetic/chromium"),
                  patch.object(setup.subprocess, "run", return_value=result) as run,
                  patch("sys.stdout", new_callable=io.StringIO) as output):
                self.assertEqual(setup.main(), 0)
                builder.return_value.create.assert_called_once_with(python.parent.parent)
                run.assert_called_once()
                command = run.call_args.args[0]
                self.assertEqual(command[:5], [str(python), "-m", "pip", "install", "--disable-pip-version-check"])
                self.assertEqual(set(command[5:]), manifest)
                self.assertEqual(json.loads(output.getvalue())["status"], "ready")


if __name__ == "__main__":
    unittest.main()
