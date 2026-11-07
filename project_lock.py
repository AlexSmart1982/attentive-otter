"""Process-wide and cross-process exclusion for a saved comparison.

The lock file is intentionally persistent: unlinking a lock file introduces a
race with another opener. The OS releases its lock even when the process dies.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path


_writer = ContextVar("comparison_project_writer", default=None)


@contextmanager
def project_lock(filename):
    path = Path(str(Path(filename).resolve()) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import win32con
        import win32file
        import pywintypes
        try:
            handle = win32file.CreateFile(str(path), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                          0, None, win32con.OPEN_ALWAYS, 0, None)
        except (OSError, pywintypes.error) as error:
            raise OSError("Сравнение занято другим сохранением или применением: " + str(filename)) from error
        try:
            yield Path(filename).resolve()
        finally:
            handle.Close()
    else:
        import fcntl
        with path.open("a+b") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise OSError("Сравнение занято другим сохранением или применением") from error
            try:
                yield Path(filename).resolve()
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


@contextmanager
def project_writer(locked_filename):
    """Allow only the transaction's own baseline/checkpoint writes to reuse its lock."""
    token = _writer.set(locked_filename)
    try:
        yield
    finally:
        _writer.reset(token)


def owns_write_lock(filename):
    return _writer.get() == Path(filename).resolve()
