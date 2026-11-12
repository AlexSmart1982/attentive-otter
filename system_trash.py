"""Native system Trash, without permanent deletion as a fallback."""

import os
from pathlib import Path
import sys


TRASH_LABEL = "Корзина macOS" if sys.platform == "darwin" else "Корзина Windows"
TRASH_DESTINATION = "в корзину macOS" if sys.platform == "darwin" else "в корзину Windows"
FILE_SYSTEM_LABEL = "macOS" if sys.platform == "darwin" else "Windows"


def recycle_file(path):
    if sys.platform != "darwin":
        from windows_trash import recycle_file as windows_recycle
        return windows_recycle(path)
    import objc
    from Foundation import NSFileManager, NSURL

    original = Path(path).absolute()
    if original.is_symlink() or not original.is_file():
        raise OSError("В корзину можно отправить только обычный файл: " + str(original))
    before = original.stat(follow_symlinks=False)
    with objc.autorelease_pool():
        success, destination, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(original)), None, None)
        if not success or destination is None:
            raise OSError("macOS не смогла отправить файл в корзину: " + str(error))
        result = Path(str(destination.path()))
    if os.path.lexists(original):
        raise OSError("macOS не подтвердила отправку файла в корзину: " + str(original))
    after = result.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise OSError("Не удалось подтвердить файл в корзине: " + str(result))
    return str(result)
