"""Build, verify and package a native macOS .app without changing old releases."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
NAME = "Attentive_Otter_1.0_BETA_2"


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def verify(bundle, output):
    environment = {k: v for k, v in os.environ.items() if k not in (
        "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")}
    environment.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", QT_QPA_PLATFORM="offscreen")
    run(bundle / "Contents/MacOS/Attentive_Otter", "--verify-deployment", output,
        cwd=bundle.parent, env=environment, timeout=180)
    report = json.loads(output.read_text(encoding="utf-8"))
    if not report.get("ok") or not report.get("frozen"):
        raise RuntimeError("macOS deployment verification failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() not in ("arm64", "x86_64"):
        raise SystemExit("Build with native CPython on macOS (arm64 or x86_64)")
    architecture = "arm64" if platform.machine() == "arm64" else "x64"
    output = args.output.resolve()
    package = output / (NAME + "_macOS_" + architecture)
    if package.exists():
        raise SystemExit("Output already exists; choose a new location")
    package.mkdir(parents=True)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", ROOT / "Attentive_Otter_macOS.spec", cwd=ROOT)
    bundle = package / "Attentive_Otter.app"
    run("/usr/bin/ditto", ROOT / "dist/Attentive_Otter.app", bundle)
    verify(bundle, package / "deployment-check.json")
    with tempfile.TemporaryDirectory(prefix="otter_перенос_") as directory:
        relocated = Path(directory) / "Новое_место.app"
        run("/usr/bin/ditto", bundle, relocated)
        verify(relocated, package / "relocation-check.json")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", relocated)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle)
    import cv2
    import pillow_heif
    from PyQt5.QtCore import qVersion
    inputs = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in ROOT.iterdir() if p.is_file() and p.suffix in (".py", ".pyw", ".spec")}
    info = dict(built_at=datetime.now(timezone.utc).isoformat(), platform=platform.platform(),
        architecture=architecture, python=sys.version, qt=qVersion(), heif=pillow_heif.libheif_info(),
        git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        packages={d.metadata["Name"]: d.version for d in metadata.distributions()}, source_files=inputs,
        signing="ad-hoc; no Apple Developer ID or notarization")
    (package / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (package / "opencv-build.txt").write_text(cv2.getBuildInformation(), encoding="utf-8")
    for name in ("LICENSE", "COPYRIGHT.txt"):
        shutil.copy2(ROOT / name, package / name)
    (package / "README.txt").write_text(
        "Attentive Otter 1.0 BETA 2 — macOS " + architecture + "\n\n"
        "Requires macOS 13 or newer; tested on macOS 15. Python is included.\n"
        "Copy Attentive_Otter.app to Applications and launch it.\n"
        "This beta is ad-hoc signed, without Developer ID/notarization. If macOS blocks it,\n"
        "use System Settings > Privacy & Security > Open Anyway after trying to open it.\n"
        "Do not edit the same archive in other applications while applying changes.\n"
        "macOS locks are advisory. Files are verified before moves; recovery uses a durable journal.\n"
        "GPL-3.0-only. Corresponding source and licenses:\n"
        "https://github.com/AlexSmart1982/attentive-otter/releases/tag/v1.0.0-beta.2\n",
        encoding="utf-8")
    # Sources and notices are collected separately before final distribution.
    print("Verified bundle: " + str(package), flush=True)


if __name__ == "__main__":
    main()
