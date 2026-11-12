"""Collect matching source and notices for the native macOS build."""

from concurrent.futures import ThreadPoolExecutor
import importlib.metadata as metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

from release_materials import request, fetch, extract_notices, is_notice, sha256


PYPI = ("PyQt5", "PyQt5-sip", "numpy", "scipy", "opencv-python", "Pillow",
        "pillow-heif", "piexif", "psutil", "PyInstaller", "pyinstaller-hooks-contrib",
        "pyobjc-core", "pyobjc-framework-Cocoa", "altgraph", "macholib", "packaging",
        "setuptools", "wheel", "scikit-build", "cmake", "ninja")


def prepare_materials(destination):
    import cv2
    import pillow_heif
    from PyQt5.QtCore import qVersion
    if sys.version_info[:3] != (3, 13, 8) or qVersion() != "5.15.17":
        raise RuntimeError("Update the macOS source inventory for changed Python/Qt")
    if "FFMPEG:                      YES" in cv2.getBuildInformation():
        raise RuntimeError("Use build_macos_opencv.py before preparing a distribution")
    info = pillow_heif.libheif_info()
    if info["libheif"] != "1.20.2" or "4.1+1-1d117be" not in str(info) or "1.0.16" not in str(info):
        raise RuntimeError("Update the HEIF source inventory: " + str(info))
    sources, licenses = destination / "sources", destination / "licenses"
    sources.mkdir(parents=True)
    licenses.mkdir()
    specs = []
    for name in PYPI:
        version = metadata.version(name)
        with request(f"https://pypi.org/pypi/{name}/{version}/json") as response:
            data = json.load(response)
        sdist = next(item for item in data["urls"] if item["packagetype"] == "sdist")
        specs.append(dict(component=name, version=version, file=sdist["filename"],
                          url=sdist["url"], expected_sha256=sdist["digests"]["sha256"]))
    for module in ("qtbase", "qtdeclarative", "qtimageformats", "qtsvg", "qtwebsockets", "qtmacextras"):
        filename = module + "-everywhere-opensource-src-5.15.17.tar.xz"
        url = "https://download.qt.io/archive/qt/5.15/5.15.17/submodules/" + filename
        with request(url + ".sha256") as response:
            checksum = response.read().decode().split()[0]
        specs.append(dict(component=module, version="5.15.17", file=filename,
                          url=url, expected_sha256=checksum))
    extras = (
        ("CPython", "3.13.8", "Python-3.13.8.tar.xz",
         "https://www.python.org/ftp/python/3.13.8/Python-3.13.8.tar.xz"),
        ("pillow-heif-build-recipes", "1.1.1", "pillow-heif-v1.1.1-upstream.tar.gz",
         "https://codeload.github.com/bigcat88/pillow_heif/tar.gz/refs/tags/v1.1.1"),
        ("libheif", "1.20.2", "libheif-1.20.2.tar.gz",
         "https://github.com/strukturag/libheif/releases/download/v1.20.2/libheif-1.20.2.tar.gz"),
        ("libde265", "1.0.16", "libde265-1.0.16.tar.gz",
         "https://github.com/strukturag/libde265/releases/download/v1.0.16/libde265-1.0.16.tar.gz"),
        ("x265", "4.1+1-1d117be", "x265-1d117be.tar.gz",
         "https://bitbucket.org/multicoreware/x265_git/get/1d117be.tar.gz"))
    specs.extend(dict(component=n, version=v, file=f, url=u) for n, v, f, u in extras)
    cache = Path(tempfile.gettempdir()) / "otter-macos-source-cache"
    cache.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda spec: fetch(spec, cache), specs))
    for archive, record in results:
        shutil.copy2(archive, sources / record["file"])
        extract_notices(archive, licenses / "upstream" / record["component"])
    for dist in metadata.distributions():
        for relative in dist.files or []:
            if is_notice(str(relative)):
                path = dist.locate_file(relative)
                if path.is_file():
                    parts = [p for p in relative.parts if p not in ("..", ".")]
                    target = licenses / "installed" / dist.metadata["Name"] / Path(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
    python_notice = Path(sys.base_prefix) / "LICENSE.txt"
    if python_notice.exists():
        shutil.copy2(python_notice, licenses / "Python-LICENSE.txt")
    # Preserve wheel provenance for independently rebuilding the custom OpenCV.
    wheel_root = Path.home() / ".cache/attentive_otter_opencv_v2" / platform.machine()
    wheel = next(wheel_root.glob("opencv_python-*.whl"))
    index = dict(archives=[record for _, record in results], heif_runtime=info,
                 qt_runtime=qVersion(), opencv_wheel=dict(file=wheel.name, sha256=sha256(wheel)),
                 opencv_build_recipe="build_macos_opencv.py")
    (destination / "sources.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    (destination / "README.txt").write_text(
        "macOS dependency sources and notices\n\n"
        "sources.json records exact versions, URLs and SHA256. Sources are in the matching\n"
        "Corresponding_Source archive. Original notices are preserved under licenses/.\n"
        "OpenCV 4.12.0 is built from opencv-python 4.12.0.88 source using build_macos_opencv.py,\n"
        "without FFmpeg/video dependencies; bundled JPEG/PNG/zlib source is in that archive.\n"
        "The original OpenCV image operations and full regression suite are used.\n"
        "Qt 5.15.17 source modules match macOS wheels. PyQt5 is GPLv3; Qt is LGPL/GPL.\n"
        "PyObjC is MIT. Python is PSF; NumPy/SciPy/psutil are BSD; Pillow is MIT-CMU.\n"
        "pillow-heif is BSD; libheif/libde265 are LGPLv3; x265 is GPLv2-or-later,\n"
        "combined in this GPLv3 distribution. Installed HEIF notices may show older versions;\n"
        "the actual runtime is checked and its matching sources are listed in sources.json.\n"
        "macOS system frameworks are supplied by the operating system.\n"
        "Full terms, including exceptions and bundled third-party components, are in the notices.\n",
        encoding="utf-8")
