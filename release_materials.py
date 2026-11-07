"""Collect upstream license notices and versioned dependency source archives."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile


FFMPEG_REVISION = "ea9240e39bc0d6a69d2b1f0ba4513bdc7612a41e"
PYPI_SOURCES = ("PyQt5", "PyQt5-sip", "numpy", "scipy", "opencv-python", "Pillow",
                "pillow-heif", "piexif", "psutil", "PyInstaller")
EXPECTED = {"PyQt5": "5.15.11", "PyQt5-Qt5": "5.15.2", "PyQt5-sip": "12.17.1",
            "numpy": "2.2.6", "scipy": "1.16.2", "opencv-python": "4.12.0.88",
            "Pillow": "11.3.0", "pillow-heif": "1.1.1", "piexif": "1.1.3",
            "psutil": "7.1.0", "PyInstaller": "6.16.0", "pywin32": "311"}


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": "Clone-Sifter-release-builder/1.0"}), timeout=90)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_specs():
    for name, version in EXPECTED.items():
        if metadata.version(name) != version:
            raise RuntimeError(f"Dependency changed: {name}; update the source/notice inventory")
    if sys.version_info[:3] != (3, 13, 8):
        raise RuntimeError("This dependency source inventory is for CPython 3.13.8")
    import pillow_heif
    from PyQt5.QtCore import qVersion
    info = pillow_heif.libheif_info()
    if (qVersion() != "5.15.2" or info["libheif"] != "1.20.2"
            or "4.1+1-1d117be" not in str(info) or "1.0.16" not in str(info)):
        raise RuntimeError("Native dependencies changed; update the source inventory")
    specs = []
    for name in PYPI_SOURCES:
        version = metadata.version(name)
        with request(f"https://pypi.org/pypi/{name}/{version}/json") as response:
            data = json.load(response)
        sdist = next(item for item in data["urls"] if item["packagetype"] == "sdist")
        specs.append(dict(component=name, version=version, file=sdist["filename"],
                          url=sdist["url"], expected_sha256=sdist["digests"]["sha256"]))
    for module in ("qtbase", "qtdeclarative", "qtimageformats", "qtsvg", "qtwebsockets"):
        filename = module + "-everywhere-src-5.15.2.tar.xz"
        url = "https://download.qt.io/archive/qt/5.15/5.15.2/submodules/" + filename
        with request(url + ".sha256") as response:
            checksum = response.read().decode().split()[0]
        specs.append(dict(component=module, version="5.15.2", file=filename,
                          url=url, expected_sha256=checksum))
    extras = [
        ("CPython", "3.13.8", "Python-3.13.8.tar.xz",
         "https://www.python.org/ftp/python/3.13.8/Python-3.13.8.tar.xz"),
        ("pywin32", "311", "pywin32-b311.tar.gz",
         "https://codeload.github.com/mhammond/pywin32/tar.gz/refs/tags/b311"),
        ("pillow-heif-build-recipes", "1.1.1", "pillow-heif-v1.1.1-upstream.tar.gz",
         "https://codeload.github.com/bigcat88/pillow_heif/tar.gz/refs/tags/v1.1.1"),
        ("libheif", "1.20.2", "libheif-1.20.2.tar.gz",
         "https://github.com/strukturag/libheif/releases/download/v1.20.2/libheif-1.20.2.tar.gz"),
        ("libde265", "1.0.16", "libde265-1.0.16.tar.gz",
         "https://github.com/strukturag/libde265/releases/download/v1.0.16/libde265-1.0.16.tar.gz"),
        ("x265", "4.1+1-1d117be", "x265-1d117be.tar.gz",
         "https://bitbucket.org/multicoreware/x265_git/get/1d117be.tar.gz"),
        ("FFmpeg", "4.4.6", "ffmpeg-4.4.6.tar.gz",
         "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/refs/tags/n4.4.6"),
        ("OpenCV-FFmpeg-build-recipes", FFMPEG_REVISION, "opencv-ffmpeg-build-recipes.tar.gz",
         f"https://codeload.github.com/opencv/opencv_3rdparty/tar.gz/{FFMPEG_REVISION}"),
        ("libvpx", "1.15.2", "libvpx-1.15.2.tar.gz",
         "https://codeload.github.com/webmproject/libvpx/tar.gz/refs/tags/v1.15.2"),
        ("libaom", "3.12.1", "libaom-3.12.1.tar.gz",
         "https://aomedia.googlesource.com/aom/+archive/refs/tags/v3.12.1.tar.gz"),
        ("OpenH264", "1.8.0", "openh264-1.8.0.tar.gz",
         "https://codeload.github.com/cisco/openh264/tar.gz/refs/tags/v1.8.0"),
    ]
    specs += [dict(component=name, version=version, file=filename, url=url)
              for name, version, filename, url in extras]
    return specs, info


def fetch(spec, cache):
    # URL-specific cache avoids collisions; a completed sidecar protects partial downloads.
    key = hashlib.sha256(spec["url"].encode()).hexdigest()[:16]
    target = cache/(key + "-" + spec["file"])
    sidecar = target.with_name(target.name + ".sha256")
    if target.exists() and sidecar.exists():
        checksum = sha256(target)
        if checksum == sidecar.read_text().strip() and checksum == spec.get("expected_sha256", checksum):
            return target, {**spec, "sha256": checksum, "bytes": target.stat().st_size}
    for attempt in range(3):
        try:
            partial = target.with_name(target.name + ".partial")
            with request(spec["url"]) as response, partial.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024*1024)
            checksum = sha256(partial)
            if checksum != spec.get("expected_sha256", checksum):
                raise RuntimeError("Upstream SHA256 mismatch: " + spec["file"])
            if not (tarfile.is_tarfile(partial) or zipfile.is_zipfile(partial)):
                raise RuntimeError("Downloaded data is not a source archive: " + spec["file"])
            partial.replace(target)
            sidecar.write_text(checksum, encoding="ascii")
            print("Source: " + spec["file"], flush=True)
            return target, {**spec, "sha256": checksum, "bytes": target.stat().st_size}
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def is_notice(name):
    return bool(re.match(r"^(licen[cs]e|copying|copyright|notice|authors|patents|exception)([._-]|$)",
                         PurePosixPath(name).name, re.IGNORECASE))


def extract_notices(archive, destination):
    """Copy notice text only; never execute or unpack untrusted archive paths."""
    def save(name, data):
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if not parts or any(part in ("..", "/") or ":" in part for part in parts):
            raise ValueError("Invalid archive path")
        target = destination.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                if not item.is_dir() and item.file_size < 2_000_000 and is_notice(item.filename):
                    save(item.filename, bundle.read(item))
    else:
        with tarfile.open(archive) as bundle:
            for item in bundle:
                if item.isfile() and item.size < 2_000_000 and is_notice(item.name):
                    save(item.name, bundle.extractfile(item).read())


def prepare_materials(destination):
    sources = destination/"sources"
    licenses = destination/"licenses"
    sources.mkdir(parents=True)
    licenses.mkdir()
    cache = Path(tempfile.gettempdir())/"clone-sifter-source-cache"
    cache.mkdir(exist_ok=True)
    specs, heif_info = source_specs()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda spec: fetch(spec, cache), specs))
    for archive, record in results:
        shutil.copy2(archive, sources/record["file"])
        extract_notices(archive, licenses/"upstream"/record["component"])
    for distribution in metadata.distributions():
        for relative in distribution.files or []:
            if is_notice(str(relative)):
                path = distribution.locate_file(relative)
                if path.is_file():
                    parts = [part for part in relative.parts if part not in ("..", ".")]
                    target = licenses/"installed"/distribution.metadata["Name"]/Path(*parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
    python_notice = Path(sys.base_prefix)/"LICENSE.txt"
    shutil.copy2(python_notice, licenses/"Python-LICENSE.txt")
    # Verify the FFmpeg binary is exactly the one referenced by OpenCV 4.12.0 sources.
    import cv2
    ffmpeg = next(Path(cv2.__file__).parent.glob("*ffmpeg*.dll"))
    if hashlib.md5(ffmpeg.read_bytes()).hexdigest() != "e5c6936240201064b15bcecf1816e8f4":
        raise RuntimeError("FFmpeg DLL changed; update its corresponding source inventory")
    index = {"archives": [record for _, record in results], "heif_runtime": heif_info,
             "ffmpeg_dll_sha256": sha256(ffmpeg), "ffmpeg_build_revision": FFMPEG_REVISION}
    (destination/"sources.json").write_text(json.dumps(index, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (destination/"README.txt").write_text(README, encoding="utf-8-sig")


README = """СТОРОННИЕ КОМПОНЕНТЫ Attentive Otter 1.0 BETA

Исходники в сжатом виде находятся в папке Source/third_party/sources передаваемого
комплекта. sources.json содержит точные версии, URL, размер и SHA256 каждого архива.
SHA256 сверены с PyPI/Qt там, где они опубликованы; для остальных архивов вычислены
при загрузке с указанных upstream-адресов. Архивы не являются Python wheels.
Лицензии и уведомления сохранены без правок в licenses/installed и licenses/upstream.
Уведомления извлечены также из исходных архивов, включая нативные библиотеки.

Основные компоненты и лицензии:
  Python 3.13.8 — PSF; отдельные встроенные компоненты имеют собственные лицензии.
  PyQt5 5.15.11 — GPLv3; PyQt5-sip 12.17.1 — BSD-2-Clause.
  Qt 5.15.2 — LGPLv3/GPLv3; сторонние части Qt имеют собственные лицензии.
  NumPy 2.2.6, SciPy 1.16.2, psutil 7.1.0 — BSD.
  OpenCV / opencv-python 4.12.0.88 — Apache-2.0; FFmpeg 4.4.6 — LGPL-2.1-or-later.
  Pillow 11.3.0 — MIT-CMU; piexif 1.1.3 — MIT; pywin32 311 — PSF.
  pillow-heif 1.1.1 — BSD-3-Clause (Python-обёртка).
  libheif 1.20.2, libde265 1.0.16 — LGPL-3.0-or-later;
  x265 4.1+1-1d117be — GPL-2.0-or-later (используется совместно на условиях GPLv3).
  PyInstaller 6.16.0 — GPLv2 с исключением для bootloader.
Полные условия определяются приложенными текстами, включая исключения и notices.

ВАЖНО: LICENSES_bundled.txt из wheel pillow-heif сохранён как получен от upstream,
но перечисляет устаревшие версии. Фактические версии проверены через libheif_info()
и указаны в sources.json; приложены исходники именно фактических версий.
Рецепты и патчи сборки HEIC: pillow-heif-v1.1.1-upstream.tar.gz/libheif и workflows.
x265 зафиксирован по коммиту 1d117be, который показывает установленный encoder.

Qt: приложены модули qtbase, qtdeclarative, qtimageformats, qtsvg, qtwebsockets
версии 5.15.2, соответствующие DLL и плагинам сборки; инструкции — README/INSTALL
в архивах. Полные заголовки и исходники сторонних частей находятся в этих архивах.
Ресурсы Qt не менялись; библиотеки поставляются отдельными DLL.

FFmpeg: приложены исходники 4.4.6, libvpx 1.15.2, libaom 3.12.1, OpenH264 1.8.0,
а также точный upstream-коммит сборочных скриптов OpenCV (см. sources.json).
Внутри opencv-ffmpeg-build-recipes.tar.gz: ffmpeg/download_src.sh, make_mingw.sh,
docker/, патчи, plugin wrapper и параметры сборки. MD5 поставляемой DLL сверён
с 3rdparty/ffmpeg/ffmpeg.cmake из OpenCV 4.12.0; SHA256 указан в sources.json.
OpenH264 DLL отдельно в комплект не добавляется; поддержка в FFmpeg динамическая.

NumPy/SciPy включают OpenBLAS и runtime-компоненты компилятора. Их уведомления,
включая GCC Runtime Library Exception и другие исключения, находятся в лицензиях
установленных wheels. Дополнительные notices (OpenSSL, zlib, libjpeg и т.д.) см.
в лицензиях Python/Pillow/OpenCV/Qt и исходных архивах.

Сборка самого приложения описана в README.txt папки Source. Библиотеки использованы
из опубликованных upstream wheels; нативная пересборка требует инструментов,
перечисленных в соответствующих исходниках. Системные библиотеки Windows и
компиляторы как исходники приложения не включены.
"""
