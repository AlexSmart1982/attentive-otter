"""Windows file handles that deny writes and rename the verified object itself."""

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import hashlib
import os
from pathlib import Path


_protected = ContextVar("comparison_protected_files", default=())


class LockedFile:
    def __init__(self, path, create=False):
        if os.name != "nt":
            raise OSError("Защищённое применение файлов поддерживается только в Windows")
        import win32con
        import win32file
        import pywintypes
        self.path = Path(path).absolute()
        try:
            access = win32con.GENERIC_READ | win32con.DELETE
            if create:
                access |= win32con.GENERIC_WRITE
            self.handle = win32file.CreateFile(str(self.path), access, win32con.FILE_SHARE_READ, None,
                                              win32con.CREATE_NEW if create else win32con.OPEN_EXISTING,
                                              0x00200000, None)  # FILE_FLAG_OPEN_REPARSE_POINT
        except pywintypes.error as error:
            raise OSError("Не удалось защитить исходный файл от изменений: " + str(self.path)) from error
        try:
            info = win32file.GetFileInformationByHandle(self.handle)
            if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError("Исходник должен быть обычным файлом: " + str(self.path))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            self.handle.Close()
            self.handle = None

    def chunks(self, cancel_event=None):
        import win32file
        import pywintypes
        win32file.SetFilePointer(self.handle, 0, 0)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Применение отменено")
            try:
                _, data = win32file.ReadFile(self.handle, 1024*1024)
            except pywintypes.error as error:
                if error.winerror == 38:  # ERROR_HANDLE_EOF
                    return
                raise
            if not data:
                return
            yield data

    def digest(self, cancel_event=None):
        value = hashlib.sha256()
        for data in self.chunks(cancel_event):
            value.update(data)
        return value.hexdigest()

    def identity(self):
        import win32file
        info = win32file.GetFileInformationByHandle(self.handle)
        return [info[4], info[8], info[9]]

    def delete(self):
        import win32file
        win32file.SetFileInformationByHandle(self.handle, 4, True)

    def copy_to(self, destination, expected, on_created=None):
        import win32file
        target = LockedFile(destination, create=True)
        try:
            if on_created is not None:
                on_created(target)
            value = hashlib.sha256()
            for data in self.chunks():
                _, written = win32file.WriteFile(target.handle, data)
                if written != len(data):
                    raise OSError("Неполная запись файла: " + str(destination))
                value.update(data)
            win32file.FlushFileBuffers(target.handle)
            if value.hexdigest() != expected or target.digest() != expected:
                raise OSError("Проверка копирования не пройдена: " + str(destination))
            # Preserve all three timestamps without PyTime's millisecond rounding.
            from ctypes.wintypes import FILETIME
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            times = [FILETIME() for _ in range(3)]
            pointers = [ctypes.byref(value) for value in times]
            if not kernel.GetFileTime(ctypes.c_void_p(int(self.handle)), *pointers):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel.SetFileTime(ctypes.c_void_p(int(target.handle)), *pointers):
                raise ctypes.WinError(ctypes.get_last_error())
            os.chmod(destination, self.path.stat().st_mode)
            win32file.FlushFileBuffers(target.handle)
            return target
        except BaseException:
            try:
                # Remove only the object we created, even if its name has changed.
                win32file.SetFileInformationByHandle(target.handle, 4, True)
            finally:
                target.close()
            raise

    def rename(self, destination):
        destination = Path(destination).absolute()
        # https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_rename_info
        class RenameInfo(ctypes.Structure):
            _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("RootDirectory", ctypes.c_void_p),
                        ("FileNameLength", ctypes.c_uint32), ("FileName", ctypes.c_wchar * 1)]
        encoded = str(destination).encode("utf-16-le")
        buffer = ctypes.create_string_buffer(max(ctypes.sizeof(RenameInfo), RenameInfo.FileName.offset + len(encoded) + 2))
        record = RenameInfo.from_buffer(buffer)
        record.FileNameLength = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + RenameInfo.FileName.offset, encoded, len(encoded))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        rename = kernel.SetFileInformationByHandle
        rename.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        rename.restype = ctypes.c_int
        if not rename(int(self.handle), 3, buffer, len(buffer)):  # FileRenameInfo, no replacement
            raise ctypes.WinError(ctypes.get_last_error())
        self.path = destination

    def move_to(self, destination, expected, on_created=None):
        if self.digest() != expected:
            raise OSError("Исходный файл изменился после поиска: " + str(self.path))
        try:
            self.rename(destination)
            return
        except OSError as error:
            if error.winerror != 17:  # ERROR_NOT_SAME_DEVICE
                raise
        target = self.copy_to(destination, expected, on_created=on_created)
        try:
            if target.digest() != expected:
                raise OSError("Проверка копирования не пройдена: " + str(destination))
            import win32file
            win32file.SetFileInformationByHandle(self.handle, 4, True)  # FileDispositionInfo
            self.close()
            self.handle, target.handle = target.handle, None
            self.path = target.path
        finally:
            target.close()

    def allow_recycle(self):
        """Keep bytes immutable while allowing the shell to rename into the bin."""
        import win32con
        import win32file
        handle = win32file.CreateFile(str(self.path), win32con.GENERIC_READ,
                                      win32con.FILE_SHARE_READ | win32con.FILE_SHARE_DELETE,
                                      None, win32con.OPEN_EXISTING, 0, None)
        self.close()
        self.handle = handle


@contextmanager
def protected_files():
    files = []
    token = _protected.set(files)
    try:
        yield files
    finally:
        for file in files:
            file.close()
        _protected.reset(token)


def protected_file(path):
    path = Path(path).absolute()
    return next((file for file in _protected.get() if file.handle is not None and file.path == path), None)
