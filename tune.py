"""Tune on a small, curated sample, never on the full archive.

Usage: python tune.py sample.csv --output tuning.json [--grid grid.json]

CSV columns: path,source_id,split. Paths are relative to the CSV directory.
source_id identifies one original exposure and ALL its derived versions.
Different exposures (including adjacent burst frames) need different IDs.
split is train, validation, or optional test; one source_id must belong to one
split. This legacy runner uses train/validation only; benchmark.py freezes a
configuration before evaluating test.
Each split needs duplicate pairs and pairs of different exposures. Maximum
sample size is 1000 images; this is an exhaustive sample benchmark.

Grid JSON is a list of objects with channels, threshold, l_threshold, kernel,
desired_stride, height, width. Thresholds use OpenCV's 8-bit LAB encoding.
The default grid is a baseline plus three provisional L thresholds.
Results measure this curated pair distribution, not archive-wide precision.
"""

import argparse
import contextlib
import csv
import io
import itertools
import json
import os
from pathlib import Path
import time

import numpy as np

import comparsion as cp


POPCOUNT = np.array([int(i).bit_count() for i in range(256)], dtype=np.uint8)


def read_sample(manifest):
    manifest = Path(manifest)
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"path", "source_id", "split"} <= set(reader.fieldnames or ()):
            raise ValueError("CSV needs path,source_id,split columns")
        rows = list(reader)
    if len(rows) > 1000:
        raise ValueError("Use a curated sample of at most 1000 images, not the full archive")
    sources, scenes, paths = {}, {}, set()
    for row in rows:
        if row["split"] not in ("train", "validation", "test") or not row["source_id"].strip():
            raise ValueError("Use train/validation/test and nonempty source_id")
        previous = sources.setdefault(row["source_id"], row["split"])
        if previous != row["split"]:
            raise ValueError("A source_id occurs in both splits: " + row["source_id"])
        if row.get("scene_id"):
            previous_scene = scenes.setdefault(row["scene_id"], row["split"])
            if previous_scene != row["split"]:
                raise ValueError("A scene_id occurs in both splits: " + row["scene_id"])
        path = (manifest.parent / row["path"]).resolve()
        if not path.is_file():
            raise ValueError("Image does not exist: " + str(path))
        identity = os.path.normcase(str(path))
        if identity in paths:
            raise ValueError("Repeated image path: " + str(path))
        paths.add(identity)
        row["path"] = str(path)
    for split in ("train", "validation", "test"):
        selected = [row for row in rows if row["split"] == split]
        if not selected and split == "test":
            continue
        labels = [a["source_id"] == b["source_id"]
                  for a, b in itertools.combinations(selected, 2)]
        if not any(labels) or all(labels):
            raise ValueError(split + " needs both duplicate and different-image pairs")
    return rows


def score_pairs(rows, config):
    """Pack masks to avoid retaining float64 descriptors for the whole sample.

Keep the V1 scoring convention: compare both orientations of the later image
against the original orientation of the earlier image, using original counts.
This matches pipeline's lower triangle for the given input order.
"""
    packed, counts = [], []
    for row in rows:
        hashes = cp.Img(row["path"], **config).hashes
        counts.append(float(hashes[0].sum()))
        packed.append(np.packbits(hashes.astype(bool), axis=1))
    scores, labels = [], []
    for i in range(len(rows)):
        for j in range(i):
            intersection = POPCOUNT[np.bitwise_and(packed[i], packed[j][0])].sum(axis=1).max()
            denominator = counts[i] + counts[j]
            scores.append(2 * float(intersection) / denominator if denominator else 0.0)
            labels.append(rows[i]["source_id"] == rows[j]["source_id"])
    return np.array(scores), np.array(labels, dtype=bool)


def metrics(scores, labels, threshold):
    # A zero score carries no evidence, including when both maps are empty.
    predicted = (scores >= threshold) & (scores > 0)
    tp = int(np.sum(predicted & labels))
    fp = int(np.sum(predicted & ~labels))
    positives, negatives = int(labels.sum()), int((~labels).sum())
    return {"threshold": float(threshold), "tp": tp, "fp": fp,
            "fn": positives - tp, "tn": negatives - fp,
            "recall": tp / positives,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "false_positive_rate": fp / negatives,
            "candidate_pairs": tp + fp}


def describe_errors(rows, scores, labels, threshold):
    """Keep reviewable examples and recall by transformation against originals."""
    false_positives, false_negatives, transformations = [], [], {}
    offset = 0
    for i, a in enumerate(rows):
        for b in rows[:i]:
            score, duplicate = float(scores[offset]), bool(labels[offset])
            offset += 1
            predicted = score >= threshold and score > 0
            pair = {"a": a["path"], "b": b["path"], "score": score}
            if predicted and not duplicate:
                false_positives.append(pair)
            if not predicted and duplicate:
                false_negatives.append(pair)
            if duplicate:
                ta, tb = a.get("transformation"), b.get("transformation")
                if ta == "original" or tb == "original":
                    name = tb if ta == "original" else ta
                    if name:
                        item = transformations.setdefault(name, {"found": 0, "total": 0})
                        item["total"] += 1
                        item["found"] += int(predicted)
    for item in transformations.values():
        item["recall"] = item["found"] / item["total"]
    return {"false_positive_examples": sorted(false_positives, key=lambda p: -p["score"])[:20],
            "missed_duplicate_examples": sorted(false_negatives, key=lambda p: p["score"])[:20],
            "original_to_variant": transformations}


def choose_threshold(scores, labels, target_recall):
    order = np.argsort(-scores)
    ordered_scores, ordered_labels = scores[order], labels[order]
    tp, fp = np.cumsum(ordered_labels), np.cumsum(~ordered_labels)
    ends = np.flatnonzero(np.r_[ordered_scores[:-1] != ordered_scores[1:], True]
                          & (ordered_scores > 0))
    if not len(ends):
        return metrics(scores, labels, 1.0)
    recall = tp[ends] / labels.sum()
    eligible = ends[recall >= target_recall]
    if not len(eligible):
        eligible = ends[recall == recall.max()]
    best = min(eligible, key=lambda i: (int(fp[i]), -int(tp[i]), -float(ordered_scores[i])))
    return metrics(scores, labels, ordered_scores[best])


def run(manifest, grid, output, target_recall=0.98):
    rows = read_sample(manifest)
    if not grid:
        raise ValueError("Grid must contain at least one configuration")
    results = []
    for index, config in enumerate(grid):
        defaults = {"channels": ["L", "a", "b"], "threshold": 9,
                    "l_threshold": None, "kernel": 6, "desired_stride": 2,
                    "height": 800, "width": 600, "use_float16": True}
        unexpected = set(config) - defaults.keys()
        if unexpected:
            raise ValueError("Unknown grid parameters: " + ", ".join(sorted(unexpected)))
        config = defaults | config
        started = time.perf_counter()
        print(f"Starting configuration {index + 1}/{len(grid)}: {config}", flush=True)
        validation_rows = [row for row in rows if row["split"] == "validation"]
        with contextlib.redirect_stdout(io.StringIO()):
            train_scores, train_labels = score_pairs(
                [row for row in rows if row["split"] == "train"], config)
            train = choose_threshold(train_scores, train_labels, target_recall)
            valid_scores, valid_labels = score_pairs(validation_rows, config)
        validation = metrics(valid_scores, valid_labels, train["threshold"])
        result = {"config": config, "train": train, "validation": validation,
                  "validation_at_0_8": metrics(valid_scores, valid_labels, 0.8),
                  "validation_review": describe_errors(
                      validation_rows, valid_scores, valid_labels, train["threshold"]),
                  "train_target_met": train["recall"] >= target_recall,
                  "seconds": time.perf_counter() - started}
        results.append(result)
        print(f"{index + 1}/{len(grid)} {config}: validation "
              f"recall={validation['recall']:.3f}, FP={validation['fp']}", flush=True)
    report = {"manifest": str(Path(manifest).resolve()), "images": len(rows),
              "target_train_recall": target_recall, "results": results,
              "note": "Validation compares configurations; final evaluation needs a separate test set. "
                      "No archive-scale quality claim follows from this sample."}
    with Path(output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--grid", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-recall", type=float, default=0.98)
    args = parser.parse_args()
    if not 0 < args.target_recall <= 1:
        parser.error("--target-recall must be in (0, 1]")
    if args.output.exists():
        parser.error("Output exists; choose a new report path")
    if args.grid:
        grid = json.loads(args.grid.read_text(encoding="utf-8"))
    else:
        grid = [{"channels": ["a", "b"]}] + [
            {"channels": ["L", "a", "b"], "l_threshold": value}
            for value in (9, 18, 27)]
    run(args.manifest, grid, args.output, args.target_recall)


if __name__ == "__main__":
    main()
