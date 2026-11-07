"""Immutable read windows and identities shared by indexing and review export."""

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import stat


@contextmanager
def source_snapshot(path):
    path = Path(path)
    handle = None
    try:
        if os.name == "nt":
            import win32con
            import win32file
            handle = win32file.CreateFile(str(path), win32con.GENERIC_READ,
                                          win32con.FILE_SHARE_READ, None, win32con.OPEN_EXISTING,
                                          0x00200000, None)  # Open reparse point itself, never its target.
            info = win32file.GetFileInformationByHandle(handle)
            if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
                raise OSError("Источник должен быть обычным файлом: " + str(path))
        status = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(status.st_mode):
            raise OSError("Источник должен быть обычным файлом: " + str(path))
        identity = {"device": status.st_dev, "inode": status.st_ino,
                    "bytes": status.st_size, "mtime_ns": status.st_mtime_ns,
                    "ctime_ns": status.st_ctime_ns}
        if handle is not None:
            class BasicInfo(ctypes.Structure):
                _fields_ = [("creation", ctypes.c_int64), ("access", ctypes.c_int64),
                            ("write", ctypes.c_int64), ("change", ctypes.c_int64),
                            ("attributes", ctypes.c_uint32)]
            basic = BasicInfo()
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            get_info = kernel.GetFileInformationByHandleEx
            get_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            get_info.restype = ctypes.c_int
            if not get_info(int(handle), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                raise ctypes.WinError(ctypes.get_last_error())
            identity["change_time"] = basic.change
        yield identity
        after = path.stat(follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns):
            raise OSError("Файл изменился во время чтения: " + str(path))
    finally:
        if handle is not None:
            handle.Close()
