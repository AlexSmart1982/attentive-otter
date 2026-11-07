"""Create a reproducible sample from curated original photos (read only).

python prepare_sample.py originals --output tuning_sample [--svg-roundtrip]

Copies originals and generates JPEG recompressions, quarter turns, downscales,
and a JPEG -> PNG -> JPEG chain. No mirrors or crops. SVG mode embeds a PNG
in SVG, renders using QtSvg, and re-encodes as JPEG. Derived versions start
from an RGB reference limited to --max-edge pixels; original copies stay intact.

Input photos must represent distinct exposures. Exact decoded pixel copies
are grouped automatically; pre-existing near-duplicates require manual review
of sample.csv. All versions of one source stay in the same split.
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil

from PIL import Image, ImageOps
import pillow_heif


def open_rgb(path):
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            background = Image.new("RGBA", rgba.size, "white")
            image = Image.alpha_composite(background, rgba)
        return image.convert("RGB")


def pixel_id(image):
    digest = hashlib.sha256()
    digest.update(str(image.size).encode("ascii"))
    digest.update(image.tobytes())
    return digest.hexdigest()


def jpeg_roundtrip(image, quality):
    encoded = io.BytesIO()
    image.save(encoded, format="JPEG", quality=quality, subsampling=2)
    encoded.seek(0)
    with Image.open(encoded) as decoded:
        return decoded.convert("RGB")


_svg_app = None


def svg_roundtrip(image, folder):
    global _svg_app
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QByteArray
    from PyQt5.QtGui import QImage, QPainter
    from PyQt5.QtSvg import QSvgRenderer
    from PyQt5.QtWidgets import QApplication

    if QApplication.instance() is None:
        _svg_app = QApplication([])
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    embedded = base64.b64encode(encoded.getvalue()).decode("ascii")
    width, height = image.size
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}"><image width="{width}" height="{height}" '
           f'xlink:href="data:image/png;base64,{embedded}"/></svg>')
    (folder / "embedded.svg").write_text(svg, encoding="utf-8")
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError("QtSvg could not load generated SVG")
    canvas = QImage(width, height, QImage.Format_RGB32)
    canvas.fill(0xFFFFFFFF)
    painter = QPainter(canvas)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    png = folder / "svg_render.png"
    if not canvas.save(str(png), "PNG"):
        raise RuntimeError("Could not save rendered SVG")
    rendered = open_rgb(png)
    rendered.save(folder / "svg_render_q75.jpg", quality=75, subsampling=2)
    return [(png, "embedded_svg_to_png"),
            (folder / "svg_render_q75.jpg", "embedded_svg_to_png_to_jpeg_q75")]


def variants(source, image, folder, include_svg):
    folder.mkdir(parents=True)
    original = folder / ("original" + source.suffix.lower())
    shutil.copy2(source, original)
    result = [(original, "original")]
    png = folder / "decoded.png"
    image.save(png)
    result.append((png, "decoded_rgb_png"))
    for quality in (95, 75, 50, 25):
        path = folder / f"jpeg_q{quality}.jpg"
        image.save(path, quality=quality, subsampling=2)
        result.append((path, f"jpeg_q{quality}"))
    for degrees, operation in [(90, Image.Transpose.ROTATE_90),
                               (180, Image.Transpose.ROTATE_180),
                               (270, Image.Transpose.ROTATE_270)]:
        path = folder / f"rotate_{degrees}_q75.jpg"
        image.transpose(operation).save(path, quality=75, subsampling=2)
        result.append((path, f"rotate_ccw_{degrees}_jpeg_q75"))
    for factor in (2, 4):
        size = tuple(max(1, dimension // factor) for dimension in image.size)
        path = folder / f"downscale_{factor}_q75.jpg"
        image.resize(size, Image.Resampling.LANCZOS).save(path, quality=75, subsampling=2)
        result.append((path, f"downscale_{factor}_lanczos_jpeg_q75"))
    # Actually encode and decode every stage, rather than relabel one JPEG.
    intermediate = jpeg_roundtrip(image, 75)
    buffer = io.BytesIO()
    intermediate.save(buffer, format="PNG")
    buffer.seek(0)
    with Image.open(buffer) as decoded:
        path = folder / "jpeg_png_jpeg_q50.jpg"
        decoded.save(path, format="JPEG", quality=50, subsampling=2)
    result.append((path, "jpeg_q75_to_png_to_jpeg_q50"))
    if include_svg:
        result.extend(svg_roundtrip(image, folder))
    return result


def prepare(source_root, output, max_sources=40, seed=42, include_svg=False,
            max_edge=1600, selection=None):
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    if not source_root.is_dir():
        raise ValueError("Input must be a folder of curated original photos")
    if output.exists() or output == source_root or source_root in output.parents:
        raise ValueError("Output must be a new folder outside the input folder")
    if not 4 <= max_sources <= 60:
        raise ValueError("Use 4 to 60 source images for this initial benchmark")
    if not 64 <= max_edge <= 4096:
        raise ValueError("Use max_edge between 64 and 4096 pixels")
    pillow_heif.register_heif_opener()
    supported = {".jpg", ".jpeg", ".png", ".heic"}
    candidates = sorted(p for p in source_root.rglob("*")
                        if p.is_file() and p.suffix.lower() in supported)
    selected = {}
    if selection is not None:
        with Path(selection).open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                path = (source_root / row["path"]).resolve()
                if row["split"] not in ("train", "validation", "test") or not path.is_file():
                    raise ValueError("Invalid selection row: " + str(row))
                if path in selected:
                    raise ValueError("Repeated selection path: " + str(path))
                selected[path] = row
        if not 4 <= len(selected) <= max_sources:
            raise ValueError("Selection must contain 4 to max_sources files")
        candidates = [p for p in candidates if p.resolve() in selected]
        if len(candidates) != len(selected):
            raise ValueError("Selection contains files outside the supported input set")
    rng = random.Random(seed)
    rng.shuffle(candidates)
    sources, skipped = {}, []
    for path in candidates:
        try:
            image = open_rgb(path)
            identity = pixel_id(image)
        except Exception as error:
            skipped.append({"path": str(path), "reason": str(error)})
            continue
        if identity in sources:
            previous = sources[identity]["path"]
            if selected and selected[path.resolve()]["split"] != selected[Path(previous)]["split"]:
                raise ValueError("Exact pixel copies occur in different selection splits")
            sources[identity]["exact_pixel_copies"].append(str(path))
            continue
        sources[identity] = {"path": str(path), "exact_pixel_copies": []}
        if len(sources) >= max_sources:
            break
    if len(sources) < 4:
        raise ValueError("Need at least 4 distinct original images for two disjoint splits")
    ids = sorted(sources)
    rng.shuffle(ids)
    validation = set(ids[:max(2, round(len(ids) * 0.25))])
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for index, identity in enumerate(ids):
        source = Path(sources[identity]["path"])
        image = open_rgb(source)
        if pixel_id(image) != identity:
            raise RuntimeError("Source changed during preparation: " + str(source))
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        chosen = selected.get(source, {})
        split = chosen.get("split", "validation" if identity in validation else "train")
        scene = chosen.get("scene_id", identity)
        for path, transformation in variants(source, image, output / "images" / identity, include_svg):
            rows.append({"path": path.relative_to(output).as_posix(),
                         "source_id": identity, "scene_id": scene, "split": split,
                         "transformation": transformation})
        print(f"{index + 1}/{len(ids)} {source.name}: {split}", flush=True)
    with (output / "sample.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["path", "source_id", "scene_id", "split", "transformation"])
        writer.writeheader()
        writer.writerows(rows)
    provenance = {"input": str(source_root), "seed": seed, "sources": sources,
                  "svg_roundtrip": include_svg, "max_reference_edge": max_edge,
                  "images": len(rows), "skipped": skipped,
                  "pillow_version": Image.__version__,
                  "selection": str(Path(selection).resolve()) if selection else None,
                  "note": "Review source_id for pre-existing near-duplicates before tuning."}
    with (output / "sources.json").open("x", encoding="utf-8") as stream:
        json.dump(provenance, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return output / "sample.csv"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-sources", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--svg-roundtrip", action="store_true")
    parser.add_argument("--max-edge", type=int, default=1600)
    parser.add_argument("--selection", type=Path,
                        help="Optional CSV: path (relative to originals), split (train/validation/test), scene_id")
    args = parser.parse_args()
    print(prepare(args.source, args.output, args.max_sources, args.seed,
                  args.svg_roundtrip, args.max_edge, args.selection))


if __name__ == "__main__":
    main()
