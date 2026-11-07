# Windows x64 desktop distribution, built with PyInstaller 6.16.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
heif_data, heif_binaries, heif_hidden = collect_all('pillow_heif')
a = Analysis(
    [str(root/'desktop.pyw')],
    pathex=[str(root)],
    binaries=heif_binaries,
    datas=[(str(root/'assets'), 'assets'),
           (str(root/'LICENSE'), '.'),
           (str(root/'COPYRIGHT.txt'), '.'),
           (str(root/'appearance_profile.json'), '.'),
           (str(root/'review_annotations.json'), '.')] + heif_data,
    hiddenimports=heif_hidden + ['win32timezone', 'pythoncom', 'pywintypes',
                                'win32com.shell.shell', 'win32com.server.policy',
                                'win32com.server.exception'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'pandas', 'numba', 'llvmlite', 'tkinter', 'IPython',
              'pytest', 'notebook', 'PySide2', 'PySide6', 'PyQt6'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Attentive_Otter',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon=str(root/'assets'/'application.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AttentiveOtter')
