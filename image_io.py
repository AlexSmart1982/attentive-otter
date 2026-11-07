"""Image decoding shared by the desktop pipeline and the legacy experiments."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps
import pillow_heif


def read_image(path):
    """Preserve the OpenCV/HEIC decoding paths used to calibrate the features."""
    if Path(path).suffix.lower() == ".heic":
        decoded = pillow_heif.read_heif(path)
        image = Image.frombytes(decoded.mode, decoded.size, decoded.data).convert("RGB")
        return np.array(image)[:, :, ::-1].copy()
    image = cv2.imread(str(path))
    if image is None:
        pillow_heif.register_heif_opener()
        try:
            with Image.open(path) as decoded:
                rgb = ImageOps.exif_transpose(decoded).convert("RGB")
                image = np.array(rgb)[:, :, ::-1].copy()
        except Exception as error:
            raise ValueError(f"Cannot decode image: {path}") from error
    return image


def correct_stride(kernel_size=5, desired_stride=1, h=800, w=600):
    return next(stride for stride in range(desired_stride, 0, -1)
                if (h-kernel_size) % stride == 0 and (w-kernel_size) % stride == 0)
