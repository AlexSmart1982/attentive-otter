"""Read all standard EXIF IFDs without decoding a full-resolution preview."""

from PIL import Image, ExifTags
import pillow_heif


LABELS = {271: "Производитель", 272: "Камера", 305: "Программа", 274: "Ориентация",
          306: "Изменение", 36867: "Дата съёмки", 36868: "Оцифровка", 33434: "Выдержка",
          33437: "Диафрагма", 34855: "ISO", 37386: "Фокусное расстояние", 37380: "Экспокоррекция",
          41989: "Фокусное (35 мм)", 40962: "Ширина", 40963: "Высота", 42036: "Объектив"}


def text_value(value):
    if isinstance(value, bytes):
        if len(value) < 512:
            try:
                return value.decode("utf-8").strip("\x00")
            except UnicodeDecodeError:
                pass
        return f"Бинарные данные: {len(value)} байт · {value[:24].hex(' ')}"
    if isinstance(value, (tuple, list)):
        return ", ".join(text_value(v) for v in value)
    if hasattr(value, "numerator") and hasattr(value, "denominator") and not isinstance(value, int):
        return f"{value.numerator}/{value.denominator}" if value.denominator != 1 else str(value.numerator)
    return str(value).strip("\x00")


def read_exif(path):
    pillow_heif.register_heif_opener()
    with Image.open(path) as image:
        exif = image.getexif()
        rows = {}
        def add(tags, group, gps=False):
            for tag, value in tags.items():
                if tag in (34665, 34853, 40965):
                    continue
                raw = (ExifTags.GPSTAGS if gps else ExifTags.TAGS).get(tag, f"0x{tag:04X}")
                label = raw if gps else LABELS.get(tag, raw)
                section = group
                if not gps:
                    if tag in (271, 272, 305, 42033, 42035, 42036):
                        section = "Камера"
                    elif tag in (306, 36867, 36868, 37520, 37521, 37522, 36880, 36881, 36882):
                        section = "Даты"
                rows[f"{group}:{tag}"] = {"group": section, "label": label, "tag": raw, "value": text_value(value)}
        add(exif, "Изображение")
        for ifd, group, gps in ((34665, "Съёмка", False), (34853, "GPS", True)):
            tags = exif.get_ifd(ifd)
            add(tags, group, gps)
            if ifd == 34665 and 40965 in tags:
                add(exif.get_ifd(40965), "Совместимость")
        return rows
