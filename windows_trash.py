"""Windows 8+ recycling, explicitly refusing permanent-delete fallback."""

import os
from pathlib import Path


def recycle_file(path):
    if os.name != "nt":
        raise OSError("Отправка в корзину поддерживается в этой сборке только в Windows")
    import pythoncom
    import pywintypes
    from win32com.shell import shell, shellcon
    from win32com.server.policy import DesignatedWrapPolicy
    from win32com.server.exception import COMException

    class Sink(DesignatedWrapPolicy):
        _com_interfaces_ = [shell.IID_IFileOperationProgressSink]
        _public_methods_ = ("StartOperations FinishOperations PreRenameItem PostRenameItem "
                           "PreMoveItem PostMoveItem PreCopyItem PostCopyItem PreDeleteItem "
                           "PostDeleteItem PreNewItem PostNewItem UpdateProgress ResetTimer "
                           "PauseTimer ResumeTimer").split()

        def __init__(self):
            self._wrap_(self)
            self.destination = None

        def __getattr__(self, name):
            if name in self._public_methods_:
                return lambda *args: None
            raise AttributeError(name)

        def PreDeleteItem(self, flags, item):
            if not flags & shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE:
                raise COMException("Для этого файла корзина недоступна", hresult=-2147467260)

        def PostDeleteItem(self, flags, item, result, recycled):
            if recycled:
                self.destination = recycled.GetDisplayName(shellcon.SIGDN_FILESYSPATH)

    original = Path(path)
    if not original.is_file() or original.is_symlink():
        raise OSError("В корзину можно отправить только обычный файл: "+str(original))
    original = original.resolve(strict=True)
    pythoncom.CoInitialize()
    try:
        operation = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None,
                                               pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IFileOperation)
        # FOFX_RECYCLEONDELETE, FOFX_ADDUNDORECORD, FOFX_EARLYFAILURE.
        operation.SetOperationFlags(0x00080000 | 0x20000000 | 0x00100000 |
                                    shellcon.FOF_NOERRORUI | shellcon.FOF_SILENT | shellcon.FOF_NOCONFIRMATION)
        sink = Sink()
        wrapped = pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)
        item = shell.SHCreateItemFromParsingName(str(original), None, shell.IID_IShellItem)
        operation.DeleteItem(item, wrapped)
        result = operation.PerformOperations()
        if result or operation.GetAnyOperationsAborted():
            raise OSError("Windows не смогла отправить файл в корзину: "+str(original))
        if not sink.destination or original.exists():
            raise OSError("Windows не подтвердила отправку файла в корзину: "+str(original))
        return sink.destination
    except pywintypes.com_error as error:
        raise OSError("Ошибка корзины Windows: "+str(error)) from error
    finally:
        pythoncom.CoUninitialize()
