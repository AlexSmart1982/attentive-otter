"""Export file metadata and undecided retention choices for manual review.

python export_review.py scan/report.json --output experiments/review_01

Pair relation labels describe content. Each file has its own keep/remove/
undecided decision. This module exports data and performs no file actions.
"""

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path

from PIL import Image
import pillow_heif
from source_snapshot import source_snapshot
from scan import check_cancel


def readable(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


def filled_exif_count(exif, detailed):
    def filled(value):
        if value is None:
            return False
        if isinstance(value, (str, bytes)):
            return bool(value.strip().strip("\x00" if isinstance(value, str) else b"\x00"))
        if isinstance(value, (tuple, list, dict)):
            return bool(value)
        return True  # Numeric zero is a valid EXIF value.
    return sum(filled(value) for tags in (exif, detailed) for key, value in tags.items()
               if key not in (34665, 34853, 40965))


def file_metadata(path, group_id, expected, cancel_event):
    with source_snapshot(path) as identity:
        if identity != expected:
            raise OSError("Файл изменился после поиска. Запустите поиск заново: " + str(path))
        stat = path.stat()
        with Image.open(path) as image:
            exif = image.getexif()
            detailed = exif.get_ifd(34665)
            row = {"group_id": group_id, "path": str(path), "filename": path.name,
                   "stored_width": image.width, "stored_height": image.height,
                   "detected_format": image.format, "camera": readable(exif.get(272)),
                   "captured_at": readable(detailed.get(36867) or exif.get(306)),
                   "exif_tags": len(exif) + len(detailed),
                   "exif_filled_tags": filled_exif_count(exif, detailed),
                   "bytes": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                   "created_at": datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_ctime)).isoformat(),
                   "decision": "undecided"}
        value = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                check_cancel(cancel_event)
                block = stream.read(1024*1024)
                if not block:
                    break
                value.update(block)
        row["sha256"] = value.hexdigest()
        return row


def export_review(report_path, output, progress=None, cancel_event=None):
    report_path, output = Path(report_path).resolve(), Path(output).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if output.exists():
        raise ValueError("Use a new output directory")
    annotation_path = Path(__file__).with_name("review_annotations.json")
    annotations = json.loads(annotation_path.read_text(encoding="utf-8"))
    annotation_root = annotation_path.parent / annotations["source_root"]
    known = {}
    for pair in annotations["pairs"]:
        key = frozenset(str((annotation_root / pair[name]).resolve()) for name in ("path1", "path2"))
        known[key] = pair
    pillow_heif.register_heif_opener()
    groups = report.get("review_groups", report["candidate_groups"])
    identities = report.get("source_identities", {})
    files, file_groups = [], {}
    total = sum(len(members) for members in groups)
    for group_id, members in enumerate(groups, 1):
        for filename in members:
            check_cancel(cancel_event)
            path = Path(filename)
            row = file_metadata(path, group_id, identities.get(filename), cancel_event)
            files.append(row)
            file_groups[str(path)] = group_id
            if progress and (len(files) % 20 == 0 or len(files) == total):
                progress({"stage": "metadata", "done": len(files), "total": total})
    pair_source = report_path.with_name("review_pairs.csv")
    if not pair_source.exists():
        pair_source = report_path.with_name("matches.csv")
    pairs = []
    with pair_source.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            check_cancel(cancel_event)
            annotation = known.get(frozenset((row["path1"], row["path2"])), {})
            pairs.append({"group_id": file_groups[row["path1"]],
                          "collage_pair_id": annotation.get("id"),
                          "file1": Path(row["path1"]).name, "file2": Path(row["path2"]).name,
                          "path1": row["path1"], "path2": row["path2"],
                          "similarity": float(row["similarity"]),
                          "score_band": row.get("category", "copy_candidate"),
                          "relation": annotation.get("relation", "unknown"),
                          "annotation_confidence": annotation.get("confidence"),
                          "confirmed_by": annotation.get("confirmed_by")})
    # A metadata callback or a long pair export may allow another edit. Do not
    # publish a result that no longer describes the indexed objects.
    for row in files:
        check_cancel(cancel_event)
        with source_snapshot(row["path"]) as identity:
            if identity != identities[row["path"]]:
                raise OSError("Файл изменился при подготовке результатов: " + row["path"])
    output.mkdir(parents=True, exist_ok=False)
    for filename, records in (("files.csv", files), ("pairs.csv", pairs)):
        if records:
            with (output / filename).open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=records[0].keys())
                writer.writeheader()
                writer.writerows(records)
    document = {"schema_version": 1, "scan_report": str(report_path), "source_root": report["source"],
                "source_roots": report.get("sources", [report["source"]]),
                "scanned_files": report["files"], "review_threshold": report["review_threshold"],
                "scan_errors": report.get("errors", []),
                "decision_values": ["keep", "remove", "undecided"],
                "note": "Retention is decided per file, independently of pair relation. "
                        "No decisions have been made automatically and no files are modified.",
                "files": files, "pairs": pairs}
    (output / "review.json").write_text(json.dumps(document, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Exported {len(groups)} groups, {len(files)} files, {len(pairs)} pairs to {output}")
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    export_review(arguments.report, arguments.output)
