"""Native macOS application bundle; build separately on arm64 and x86_64."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
heif_data, heif_binaries, heif_hidden = collect_all('pillow_heif')
a = Analysis([str(root / 'desktop.pyw')], pathex=[str(root)],
    binaries=heif_binaries,
    datas=[(str(root / 'assets'), 'assets'), (str(root / 'LICENSE'), '.'),
           (str(root / 'COPYRIGHT.txt'), '.'), (str(root / 'appearance_profile.json'), '.'),
           (str(root / 'review_annotations.json'), '.')] + heif_data,
    hiddenimports=heif_hidden + ['Foundation', 'objc', 'darwin_files'],
    excludes=['matplotlib', 'pandas', 'numba', 'llvmlite', 'tkinter', 'IPython',
              'pytest', 'notebook', 'PySide2', 'PySide6', 'PyQt6', 'win32com'],
    hookspath=[], runtime_hooks=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Attentive_Otter',
          console=False, strip=False, upx=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AttentiveOtter')
app = BUNDLE(coll, name='Attentive_Otter.app', icon=str(root / 'assets/application.icns'),
             bundle_identifier='io.github.alexsmart1982.attentive-otter', version='1.0.0',
             info_plist={'CFBundleDisplayName': 'Attentive Otter', 'CFBundleVersion': '1.0.0.2',
                         'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
                         'NSPrincipalClass': 'NSApplication'})
