"""Legacy owned-profile location, retained only for migration to the shared runtime."""
import hashlib
import os
from pathlib import Path


def private_root():
    snap = Path.home()/'snap/chromium/common'
    root = snap/'ozon-bom' if snap.is_dir() else Path.home()/'.local/state/ozon-bom/browser'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('browser_directory_not_private')
    return root


def profile_path(session, document):
    # A fresh import gets a fresh profile; rotated cookies retain the same profile.
    binding = str(Path(session).absolute())+'|'+str(document.get('exported_at')) if session else 'anonymous'
    profile = private_root()/('profile-'+hashlib.sha256(binding.encode()).hexdigest()[:16])
    profile.mkdir(mode=0o700,exist_ok=True)
    if profile.is_symlink() or profile.stat().st_mode & 0o077:
        raise ValueError('browser_profile_not_private')
    return profile
