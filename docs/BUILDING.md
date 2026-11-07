# Building the Windows application

This release was built with **CPython 3.13.8 x64** and **PyInstaller 6.16.0** on Windows. The exact application-source hashes and package versions are recorded in [build-info.json](../build-info.json).

## Run from source

Use a short checkout path on Windows. For example:

```powershell
git clone -c core.longpaths=true https://github.com/AlexSmart1982/attentive-otter.git C:\AO
cd C:\AO
```

For the complete source ZIP, extract to a short path as well; the preserved upstream notices include long filenames.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\pythonw.exe -B desktop.pyw
```

The current runtime includes Windows-specific filesystem and Recycle Bin code. A macOS port is [planned](ROADMAP.md).

## Run tests

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -v
```

Tests create temporary fixtures. They must not use a personal photo archive for destructive operations.

## Build portable packages

```powershell
.\.venv\Scripts\python.exe -B -X utf8 build_windows.py --output C:\Temp\Attentive_Otter_release
```

Choose an empty output location. The builder refuses to overwrite existing release folders. It snapshots the source, builds the EXE, collects dependency sources and notices, runs the test suite, verifies the executable, checks a relocated copy, and writes paired application/source folders with SHA256 manifests.

Dependency sources are cached in `%TEMP%\clone-sifter-source-cache`; the cache name is retained from an earlier internal project name. Building may require internet access. Use a drive with room for the application, source archives and temporary build copies.

For a direct EXE build from prepared materials:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm PhotoComparison.spec
```

The resulting folder is `dist\AttentiveOtter`, containing `Attentive_Otter.exe` and `_internal`. Use `build_windows.py` for a distribution that also includes notices and the corresponding source package.

## Native dependencies and source bundle

The custom **Corresponding Source ZIP** in the [release](https://github.com/AlexSmart1982/attentive-otter/releases/tag/v1.0.0-beta.1) contains `third_party/sources`, upstream build instructions, patches and notices. The Git repository records their inventory in [third_party/sources.json](../third_party/sources.json).

The application build uses published upstream wheels. Rebuilding native third-party libraries requires the compilers and tools described by their upstream projects. Byte-for-byte reproducibility of the EXE is not claimed.
