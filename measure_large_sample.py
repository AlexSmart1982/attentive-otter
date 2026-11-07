"""Read-only full-sample experiment; generate variants in memory, not on disk."""

import argparse
import csv
import json
from pathlib import Path
import time

import cv2
import numpy as np
from scipy.spatial import cKDTree

from alignment import align_translation
from features import appearance_vectors, read_orientations
from scan import coarse_vectors


def projection(coarse, method):
    blocks = coarse.reshape(*coarse.shape[:-1], 12, 3)
    if method == "rgb36":
        return coarse / np.sqrt(36)
    if method == "gray12":
        return blocks.mean(axis=-1) / np.sqrt(12)
    if method == "color3":
        return blocks.mean(axis=-2) / np.sqrt(3)
    if method == "combined15":
        return np.concatenate((blocks.mean(axis=-1)/np.sqrt(24),
                               blocks.mean(axis=-2)/np.sqrt(6)), axis=-1)
    raise ValueError(method)


def measure(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or source in output.parents:
        raise ValueError("Use a new output outside the originals")
    output.mkdir(parents=True)
    paths = sorted(p for p in source.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".heic", ".png"))
    names, ids, variants, coarse, residuals = [], [], [], [], []
    started = time.perf_counter()
    methods = ["original", "shift", "jpeg_q35", "downscale_q50", "shift_jpeg_q35"]
    for source_id, path in enumerate(paths):
        orientations = read_orientations(path)
        original_fine = appearance_vectors(orientations)
        original = orientations[0]
        factor = 512 / max(original.shape[:2])
        size = (round(original.shape[1]*factor), round(original.shape[0]*factor))
        image = cv2.resize(original, size, interpolation=cv2.INTER_AREA)
        translated = cv2.warpAffine(image, np.array([[1, 0, 1.5], [0, 1, -.75]], dtype=np.float32),
                                    size, borderMode=cv2.BORDER_REFLECT_101)
        def jpeg(im, quality):
            ok, encoded = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            return cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        down = cv2.resize(image, (max(1, size[0]//2), max(1, size[1]//2)), interpolation=cv2.INTER_AREA)
        images = [None, translated, jpeg(image, 35),
                  cv2.resize(jpeg(down, 50), size), jpeg(translated, 35)]
        for kind, candidate in zip(methods, images):
            if candidate is None:
                fine = original_fine
            else:
                turns = (0, 1, 2, 3) if candidate.shape[0] == candidate.shape[1] else (0, 2)
                fine = appearance_vectors([np.ascontiguousarray(np.rot90(candidate, turn)) for turn in turns])
                _, result = align_translation(image, candidate)
                residuals.append({"source": path.name, "variant": kind,
                                  "dx": result["dx"], "dy": result["dy"],
                                  "rmse_before": result["before"]["rmse"],
                                  "rmse_after": result["after"]["rmse"],
                                  "reduction": result["rmse_reduction"],
                                  "local_energy_share": result["after"]["top_1_percent_energy_share"]})
            coarse.append(coarse_vectors(fine))
            names.append(path.name)
            ids.append(source_id)
            variants.append(kind)
        if (source_id+1) % 25 == 0 or source_id+1 == len(paths):
            print(f"Measured {source_id+1}/{len(paths)}, {time.perf_counter()-started:.1f}s", flush=True)
    owners = np.repeat(np.arange(len(coarse)), [len(row) for row in coarse])
    points = np.concatenate(coarse)
    expected = len(paths)*10  # Five versions per source: all ten within-source pairs.
    indexes = []
    for method in ("rgb36", "gray12", "color3", "combined15"):
        projected = projection(points, method)
        start = time.perf_counter()
        tree = cKDTree(projected)
        build_seconds = time.perf_counter()-start
        offsets = np.r_[0, np.cumsum([len(row) for row in coarse])]
        found = candidates = 0
        active = set()
        per_file = np.zeros(len(coarse), dtype=int)
        for i in range(len(coarse)):
            found_ids = set()
            for vector in projected[offsets[i]:offsets[i+1]]:
                found_ids.update(int(owners[p]) for p in tree.query_ball_point(vector, .03+3e-6)
                                 if owners[p] > i)
            for j in found_ids:
                candidates += 1
                per_file[i] += 1
                per_file[j] += 1
                found += ids[i] == ids[j]
                active.update((i, j))
        result = {"method": method, "files": len(coarse), "all_pairs": len(coarse)*(len(coarse)-1)//2,
                  "candidates": candidates, "known_copy_pairs": expected, "retained_copy_pairs": found,
                  "copy_pair_recall": found/expected, "active_files": len(active),
                  "candidates_per_file_mean": float(per_file.mean()),
                  "candidates_per_file_p95": float(np.quantile(per_file, .95)),
                  "build_seconds": build_seconds, "query_seconds": time.perf_counter()-start-build_seconds}
        indexes.append(result)
        print(json.dumps(result), flush=True)
    normal = [row[:2] for row in coarse if len(row) == 2]
    np.savez_compressed(output/"coarse_sample.npz", coarse=np.stack(normal),
                        all_coarse=points, owners=owners, source_ids=np.asarray(ids),
                        names=np.asarray(names), variants=np.asarray(variants))
    with (output/"alignment_variants.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=residuals[0].keys())
        writer.writeheader()
        writer.writerows(residuals)
    alignment_summary = {}
    for kind in methods[1:]:
        rows = [r for r in residuals if r["variant"] == kind]
        alignment_summary[kind] = {name: np.quantile([r[name] for r in rows], [0, .5, .95, 1]).tolist()
                                   for name in ["rmse_before", "rmse_after", "reduction", "local_energy_share"]}
    report = {"sources": len(paths), "files": len(coarse), "generated_in_memory": True,
              "alignment_grid_long_edge": 512, "shift_in_comparison_pixels": [1.5, -.75],
              "seconds": time.perf_counter()-started, "indexes": indexes,
              "alignment_summary": alignment_summary,
              "note": "Known positives are generated variants. Cross-source candidates are not automatically false positives."}
    (output/"report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    measure(args.source, args.output)
