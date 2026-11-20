"""Private per-profile sandbox credential, kept outside goal storage."""
import os
import stat
import tempfile
from pathlib import Path


class TokenFile:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError('DREAM_TOKEN_FILE must be absolute')

    def read(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'r', encoding='ascii') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Файл токена должен быть приватным (0600)')
            return stream.read(4097).strip() or None

    def write(self, token):
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = parent.stat()
        if parent.is_symlink() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Каталог токена должен быть приватным (0700)')
        fd, tmp = tempfile.mkstemp(dir=parent, prefix='.dream-token-')
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(token + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            Path(tmp).unlink(missing_ok=True)
