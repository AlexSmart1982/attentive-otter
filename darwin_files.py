"""macOS file descriptors, verified identities and exclusive native renames.

POSIX locks are advisory: other applications must not edit the same archive
while changes are applied. Identity and content are checked before each move.
"""

import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat


class LockedFile:
    def __init__(self, path, create=False):
        self.path = Path(path).absolute()
        self.handle = None
        self._verified_digest = None
        flags = os.O_NOFOLLOW | (os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY)
        descriptor = os.open(self.path, flags, 0o600)
        self.handle = descriptor
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("Источник должен быть обычным файлом: " + str(self.path))
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._check_path()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            os.close(self.handle)
            self.handle = None

    def identity(self):
        status = os.fstat(self.handle)
        return [status.st_dev, status.st_ino >> 32, status.st_ino & 0xffffffff]

    def _check_path(self):
        status = self.path.stat(follow_symlinks=False)
        identity = [status.st_dev, status.st_ino >> 32, status.st_ino & 0xffffffff]
        if not stat.S_ISREG(status.st_mode) or identity != self.identity():
            raise OSError("Исходный путь подменён: " + str(self.path))

    def chunks(self, cancel_event=None):
        before = os.fstat(self.handle)
        os.lseek(self.handle, 0, os.SEEK_SET)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Применение отменено")
            data = os.read(self.handle, 1024 * 1024)
            if not data:
                break
            yield data
        after = os.fstat(self.handle)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OSError("Файл изменился во время чтения: " + str(self.path))

    def digest(self, cancel_event=None):
        value = hashlib.sha256()
        for data in self.chunks(cancel_event):
            value.update(data)
        self._verified_digest = value.hexdigest()
        return self._verified_digest

    def delete(self):
        self._check_path()
        self.path.unlink()

    def copy_to(self, destination, expected, on_created=None):
        self._check_path()
        if self.digest() != expected:
            raise OSError("Исходный файл изменился после поиска: " + str(self.path))
        target = LockedFile(destination, create=True)
        try:
            if on_created is not None:
                on_created(target)
            library = ctypes.CDLL(None, use_errno=True)
            copy = library.fcopyfile
            copy.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            copy.restype = ctypes.c_int
            os.lseek(self.handle, 0, os.SEEK_SET)
            # COPYFILE_STAT | COPYFILE_XATTR | COPYFILE_DATA preserves macOS metadata.
            if copy(self.handle, target.handle, None, 2 | 4 | 8):
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(destination))
            # fcopyfile does not preserve the birth time on every macOS version.
            class AttrList(ctypes.Structure):
                _fields_ = [("count", ctypes.c_ushort), ("reserved", ctypes.c_ushort),
                            ("common", ctypes.c_uint32), ("volume", ctypes.c_uint32),
                            ("directory", ctypes.c_uint32), ("file", ctypes.c_uint32),
                            ("fork", ctypes.c_uint32)]
            class Timespec(ctypes.Structure):
                _fields_ = [("seconds", ctypes.c_long), ("nanoseconds", ctypes.c_long)]
            created = os.fstat(self.handle).st_birthtime
            timestamp = Timespec(int(created), round((created - int(created)) * 1_000_000_000))
            attrs = AttrList(5, 0, 0x00000200, 0, 0, 0, 0)  # ATTR_CMN_CRTIME
            set_attrs = library.fsetattrlist
            set_attrs.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
            set_attrs.restype = ctypes.c_int
            if set_attrs(target.handle, ctypes.byref(attrs), ctypes.byref(timestamp), ctypes.sizeof(timestamp), 0):
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), str(destination))
            os.fsync(target.handle)
            if self.digest() != expected or target.digest() != expected:
                raise OSError("Проверка копирования не пройдена: " + str(destination))
            self._check_path()
            target._check_path()
            return target
        except BaseException:
            try:
                target.delete()
            finally:
                target.close()
            raise

    def rename(self, destination):
        self._check_path()
        destination = Path(destination).absolute()
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # RENAME_EXCL: never overwrite a concurrently created file or symlink.
        if rename(os.fsencode(self.path), os.fsencode(destination), 0x00000004):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
        self.path = destination
        self._check_path()

    def move_to(self, destination, expected, on_created=None):
        self._check_path()
        if self.digest() != expected:
            raise OSError("Исходный файл изменился после поиска: " + str(self.path))
        try:
            self.rename(destination)
            return
        except OSError as error:
            if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
                raise
        target = self.copy_to(destination, expected, on_created=on_created)
        try:
            if self.digest() != expected or target.digest() != expected:
                raise OSError("Проверка копирования не пройдена: " + str(destination))
            self.delete()
            self.close()
            self.handle, target.handle = target.handle, None
            self.path = target.path
        finally:
            target.close()

    def allow_recycle(self):
        self._check_path()
        expected = self._verified_digest
        if expected is None or self.digest() != expected:
            raise OSError("Исходный файл изменился перед отправкой в корзину: " + str(self.path))
