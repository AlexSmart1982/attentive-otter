"""Build the image-only OpenCV wheel used by the macOS distribution.

The ordinary macOS wheel includes a Homebrew FFmpeg dependency tree unrelated
to photo comparison. This reproducible recipe keeps the original OpenCV version
and image operations while building its JPEG/PNG dependencies from included source.
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import urllib.request


CMAKE_ARGS = " ".join((
    "-DBUILD_LIST=core,imgproc,imgcodecs,python3", "-DBUILD_TESTS=OFF",
    "-DBUILD_PERF_TESTS=OFF", "-DBUILD_EXAMPLES=OFF", "-DWITH_FFMPEG=OFF",
    "-DWITH_GSTREAMER=OFF", "-DWITH_QT=OFF", "-DWITH_COCOA=OFF",
    "-DWITH_AVIF=OFF", "-DWITH_OPENEXR=OFF", "-DWITH_JASPER=OFF",
    "-DWITH_OPENJPEG=OFF", "-DWITH_TIFF=OFF", "-DWITH_WEBP=OFF",
    "-DWITH_GDAL=OFF", "-DWITH_GDCM=OFF", "-DWITH_OPENCL=OFF",
    "-DWITH_IPP=OFF", "-DWITH_LAPACK=OFF", "-DWITH_EIGEN=OFF",
    "-DWITH_ITT=OFF", "-DWITH_PROTOBUF=OFF", "-DBUILD_JPEG=ON",
    "-DBUILD_PNG=ON", "-DBUILD_ZLIB=ON"))


def main():
    if sys.platform != "darwin":
        raise SystemExit("This recipe is for macOS")
    folder = Path.home() / ".cache/attentive_otter_opencv_v1" / platform.machine()
    folder.mkdir(parents=True, exist_ok=True)
    wheels = list(folder.glob("opencv_python-4.12.0.88-*.whl"))
    if not wheels:
        with urllib.request.urlopen("https://pypi.org/pypi/opencv-python/4.12.0.88/json") as response:
            data = json.load(response)
        spec = next(item for item in data["urls"] if item["packagetype"] == "sdist")
        archive = folder / spec["filename"]
        urllib.request.urlretrieve(spec["url"], archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != spec["digests"]["sha256"]:
            raise RuntimeError("OpenCV source checksum does not match PyPI")
        env = {**os.environ, "CMAKE_ARGS": CMAKE_ARGS, "CMAKE_BUILD_PARALLEL_LEVEL": "3",
               "MACOSX_DEPLOYMENT_TARGET": "14.0"}
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                        "--wheel-dir", str(folder), str(archive)], env=env, check=True)
        wheels = list(folder.glob("opencv_python-4.12.0.88-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one native OpenCV wheel")
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--force-reinstall",
                    str(wheels[0])], check=True)


if __name__ == "__main__":
    main()
