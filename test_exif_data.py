from pathlib import Path
import tempfile
import unittest

from PIL import Image, TiffImagePlugin

from exif_data import read_exif, text_value


class ExifTests(unittest.TestCase):
    def test_root_and_nested_ifd_are_read_with_compatible_keys_for_both_files(self):
        with tempfile.TemporaryDirectory(prefix="v2_exif_") as folder:
            rows = []
            for index in range(2):
                path = Path(folder) / f"{index}.jpg"
                exif = Image.Exif()
                exif[271], exif[272] = "Maker", f"Camera {index}"
                exif[34665] = {36867: f"2020:01:0{index+1} 12:00:00",
                               33434: TiffImagePlugin.IFDRational(1, 250)}
                Image.new("RGB", (64, 48), "green").save(path, exif=exif)
                rows.append(read_exif(path))
            self.assertEqual(rows[0].keys(), rows[1].keys())
            self.assertEqual(next(r["value"] for r in rows[0].values() if r["tag"] == "Model"), "Camera 0")
            self.assertEqual(next(r["value"] for r in rows[1].values() if r["tag"] == "Model"), "Camera 1")
            self.assertEqual(next(r["group"] for r in rows[0].values() if r["tag"] == "DateTimeOriginal"), "Даты")
            self.assertEqual(next(r["value"] for r in rows[0].values() if r["tag"] == "ExposureTime"), "1/250")

    def test_missing_exif_returns_empty_rows(self):
        with tempfile.TemporaryDirectory(prefix="v2_empty_exif_") as folder:
            path = Path(folder) / "photo.png"
            Image.new("RGB", (64, 48)).save(path)
            self.assertEqual(read_exif(path), {})

    def test_large_binary_tag_is_bounded_and_identified(self):
        value = text_value(bytes(range(256))*100)
        self.assertIn("25600", value)
        self.assertLess(len(value), 150)


if __name__ == "__main__":
    unittest.main()
