"""Read-only candidate scan with a persistent compact index.

python scan.py folder --output experiments/scan_01 --cache experiments/cache.sqlite
The CLI includes nearby frames down to the profile's review threshold.

Cached RGB block means project to 12 achromatic block means, a lower bound on thumbnail RMSE
(Jensen's inequality). A cKDTree range query therefore retains every pair
passing the final RMSE cutoff, up to the explicit floating-point tolerance.
This is exact filtering for this score, not approximate nearest neighbours.
Worst-case dense collections can still produce quadratically many candidates.
"""

import argparse
from collections import OrderedDict
from contextlib import closing, ExitStack
import csv
import json
from pathlib import Path
import sqlite3
import time
import zlib

import cv2
import numpy as np
from scipy.spatial import cKDTree

from features import appearance_vectors, read_orientations
from source_snapshot import source_snapshot


SCHEMA = "rgb96x72-blur0.8-block4x3-v2-mean64-opencv-" + cv2.__version__
DEFAULT_PROFILE = json.loads(Path(__file__).with_name("appearance_profile.json").read_text(encoding="utf-8"))
DEFAULT_THRESHOLD = DEFAULT_PROFILE["threshold"]
DEFAULT_REVIEW_THRESHOLD = DEFAULT_PROFILE["review_threshold"]


class ScanCancelled(Exception):
    pass


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled("Поиск остановлен")


def source_folders(source):
    values = [source] if isinstance(source, (str, Path)) else source
    roots = list(dict.fromkeys(Path(p).resolve() for p in values))
    if not roots or any(not p.is_dir() for p in roots):
        raise ValueError("Выберите существующие папки-источники")
    return roots


def collect_files(roots):
    return sorted({p.resolve() for root in roots for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".heic")})


def ensure_cache_schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS features "
               "(path TEXT PRIMARY KEY, bytes INTEGER, mtime INTEGER, schema TEXT, coarse BLOB, thumbnail BLOB)")
    if "thumbnail" not in {row[1] for row in db.execute("PRAGMA table_info(features)")}:
        db.execute("ALTER TABLE features ADD COLUMN thumbnail BLOB")
    if "identity" not in {row[1] for row in db.execute("PRAGMA table_info(features)")}:
        db.execute("ALTER TABLE features ADD COLUMN identity TEXT")


def coarse_vectors(fine):
    # Accumulate 576 pixels in float64; cache only the rounded final means.
    return fine.reshape(len(fine), 4, 24, 3, 24, 3).mean(axis=(2, 4), dtype=np.float64).astype(np.float32).reshape(len(fine), 36)


def index_vectors(coarse):
    """Average RGB within each block; Jensen preserves the RMSE lower bound."""
    coarse = np.asarray(coarse, dtype=np.float64)
    return coarse.reshape(*coarse.shape[:-1], 12, 3).mean(axis=-1)


def similarity(a, b):
    difference = a[:, None, :] - b[None, :, :]
    return float(1 - np.sqrt(np.mean(difference * difference, axis=-1)).min())


def encode_thumbnail(fine):
    """Appearance vectors originate from uint8 pixels; retain them losslessly."""
    return zlib.compress(np.rint(fine*255).astype(np.uint8).tobytes(), level=1)


def decode_thumbnail(payload):
    data = np.frombuffer(zlib.decompress(payload), dtype=np.uint8)
    if data.size not in (2*96*72*3, 4*96*72*3):
        raise ValueError("Invalid cached thumbnail dimensions")
    return data.reshape(-1, 96*72*3).astype(np.float32)/255


class FineCache:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self.entries = OrderedDict()
        self.database = None
        self.source_decodes = self.memory_hits = self.disk_hits = 0
        self.source_seconds = self.disk_seconds = 0.0
        self.identities = {}

    def get(self, path):
        if path in self.entries:
            self.memory_hits += 1
            self.entries.move_to_end(path)
            return self.entries[path]
        payload = None
        started = time.perf_counter()
        if self.database is not None:
            row = self.database.execute("SELECT thumbnail FROM features WHERE path=?", (path,)).fetchone()
            payload = row[0] if row else None
        if payload is not None:
            fine = decode_thumbnail(payload)
            self.disk_hits += 1
            self.disk_seconds += time.perf_counter()-started
        else:
            with source_snapshot(path) as identity:
                if path in self.identities and identity != self.identities[path]:
                    raise OSError("Файл изменился после индексации: " + str(path))
                fine = appearance_vectors(read_orientations(path))
            self.source_decodes += 1
            self.source_seconds += time.perf_counter()-started
        self.entries[path] = fine
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)
        return fine


def candidate_ids(tree, coarse, owners, image_id, threshold):
    # Keep compatibility with previously built 36-D trees in experiments.
    if tree.m == 12:
        coarse = index_vectors(coarse)
    elif tree.m != 36:
        raise ValueError("Expected a 12-D or 36-D candidate index")
    # Bound float32 subtraction/square/mean/sqrt and the final 1-RMSE rounding.
    # gamma_n = n*u/(1-n*u) bounds even a sequential sum of all 20736 terms.
    # The extra 8*u also covers rounding both cached block projections. This
    # deliberately overestimates the error of numpy's pairwise reduction.
    unit = np.finfo(np.float32).eps / 2
    radius = np.sqrt(tree.m) * ((1 - threshold + 2*unit) / (1 - (96*72*3+8)*unit) + 8*unit)
    found = set()
    for vector in coarse:
        found.update(int(owners[position]) for position in tree.query_ball_point(vector, radius)
                     if owners[position] > image_id)
    return sorted(found)


def scan(source, output, cache, threshold=DEFAULT_THRESHOLD, review_threshold=None,
         persist_thumbnails=True, progress=None, cancel_event=None):
    overall_started = time.perf_counter()
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    if review_threshold is None:
        review_threshold = threshold
    if not 0 < review_threshold <= threshold:
        raise ValueError("review_threshold must be in (0, threshold]")
    roots = source_folders(source)
    source, output, cache = roots[0], Path(output).resolve(), Path(cache).resolve()
    if output.exists() or any(output == root or root in output.parents for root in roots):
        raise ValueError("Use an existing source and a new output outside it")
    if any(cache == root or root in cache.parents for root in roots):
        raise ValueError("Keep the index outside the source folder")
    check_cancel(cancel_event)
    files = collect_files(roots)
    output.mkdir(parents=True, exist_ok=False)
    cache.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    fine_cache = FineCache()
    paths, features, errors, unchanged = [], [], [], 0
    thumbnail_bytes = 0
    encode_seconds = 0.0
    with closing(sqlite3.connect(cache)) as db:
        ensure_cache_schema(db)
        for index, path in enumerate(files):
            check_cancel(cancel_event)
            try:
                with source_snapshot(path) as identity:
                    row = db.execute("SELECT bytes,mtime,schema,coarse,length(thumbnail),identity FROM features WHERE path=?",
                                     (str(path),)).fetchone()
                    if (row and tuple(row[:3]) == (identity['bytes'], identity['mtime_ns'], SCHEMA)
                            and row[5] == json.dumps(identity, sort_keys=True)
                            and (not persist_thumbnails or row[4] is not None)):
                        coarse = np.frombuffer(row[3], dtype=np.float32).reshape(-1, 36).copy()
                        unchanged += 1
                        if persist_thumbnails:
                            thumbnail_bytes += row[4]
                    else:
                        fine = fine_cache.get(str(path))
                        coarse = coarse_vectors(fine)
                        payload = None
                        if persist_thumbnails:
                            encode_started = time.perf_counter()
                            payload = encode_thumbnail(fine)
                            encode_seconds += time.perf_counter()-encode_started
                            thumbnail_bytes += len(payload)
                        db.execute("INSERT OR REPLACE INTO features "
                                   "(path,bytes,mtime,schema,coarse,thumbnail,identity) VALUES (?,?,?,?,?,?,?)",
                                   (str(path), identity['bytes'], identity['mtime_ns'], SCHEMA, coarse.tobytes(), payload,
                                    json.dumps(identity, sort_keys=True)))
                paths.append(str(path))
                features.append(coarse)
                fine_cache.identities[str(path)] = identity
            except Exception as error:
                errors.append({"path": str(path), "error": str(error)})
            if (index+1) % 50 == 0 or index+1 == len(files):
                db.commit()
                print(f"Indexed {index+1}/{len(files)}, cached={unchanged}, errors={len(errors)}", flush=True)
                if progress:
                    progress({"stage": "index", "done": index+1, "total": len(files)})
    indexed_at = time.perf_counter()
    initial_source_decodes = fine_cache.source_decodes
    owners = np.repeat(np.arange(len(paths)), [len(item) for item in features])
    tree = cKDTree(index_vectors(np.concatenate(features))) if features else None
    tree_built_at = time.perf_counter()
    parents = list(range(len(paths)))
    review_parents = list(range(len(paths)))

    def find(i, mapping):
        while mapping[i] != i:
            mapping[i] = mapping[mapping[i]]
            i = mapping[i]
        return i

    candidates = matches = review_matches = 0
    candidate_seconds = lookup_seconds = comparison_seconds = 0.0
    with (ExitStack() as resources,
          (output / "matches.csv").open("x", encoding="utf-8", newline="") as stream,
          (output / "review_pairs.csv").open("x", encoding="utf-8", newline="") as review_stream):
        if persist_thumbnails:
            fine_cache.database = resources.enter_context(closing(sqlite3.connect(cache)))
        writer = csv.writer(stream)
        writer.writerow(["path1", "path2", "similarity"])
        review_writer = csv.writer(review_stream)
        review_writer.writerow(["path1", "path2", "similarity", "category"])
        for i, path in enumerate(paths):
            check_cancel(cancel_event)
            stage_started = time.perf_counter()
            selected = candidate_ids(tree, features[i], owners, i, review_threshold)
            candidate_seconds += time.perf_counter()-stage_started
            for j in selected:
                check_cancel(cancel_event)
                candidates += 1
                stage_started = time.perf_counter()
                a, b = fine_cache.get(path), fine_cache.get(paths[j])
                lookup_seconds += time.perf_counter()-stage_started
                stage_started = time.perf_counter()
                score = similarity(a, b)
                comparison_seconds += time.perf_counter()-stage_started
                if score >= review_threshold:
                    category = "copy_candidate" if score >= threshold else "similar_frame"
                    review_writer.writerow([path, paths[j], score, category])
                    review_matches += 1
                    review_parents[find(i, review_parents)] = find(j, review_parents)
                if score >= threshold:
                    writer.writerow([path, paths[j], score])
                    matches += 1
                    parents[find(i, parents)] = find(j, parents)
            if (i+1) % 100 == 0 or i+1 == len(paths):
                stream.flush()
                review_stream.flush()
                print(f"Queried {i+1}/{len(paths)}, candidates={candidates}, matches={matches}", flush=True)
                if progress:
                    progress({"stage": "compare", "done": i+1, "total": len(paths)})
    groups = {}
    review_groups = {}
    for i, path in enumerate(paths):
        groups.setdefault(find(i, parents), []).append(path)
        review_groups.setdefault(find(i, review_parents), []).append(path)
    groups = [members for members in groups.values() if len(members) > 1]
    review_groups = [members for members in review_groups.values() if len(members) > 1]
    finished_at = time.perf_counter()
    report = {"source": str(source), "schema": SCHEMA, "threshold": threshold,
              "sources": [str(root) for root in roots],
              "candidate_index": "ckdtree_gray12", "candidate_dimensions": 12,
              "review_threshold": review_threshold,
              "files": len(paths), "cached_files": unchanged, "errors": errors,
              "source_identities": fine_cache.identities,
              "all_possible_pairs": len(paths)*(len(paths)-1)//2,
              "verified_candidates": candidates, "matches": matches, "candidate_groups": groups,
              "review_matches": review_matches, "similar_frame_pairs": review_matches-matches,
              "review_groups": review_groups,
              "index_seconds": indexed_at-started,
              "query_seconds": time.perf_counter()-indexed_at,
              "coarse_bytes": sum(item.nbytes for item in features),
              "persist_thumbnails": persist_thumbnails, "thumbnail_payload_bytes": thumbnail_bytes,
              "cache_database_bytes": cache.stat().st_size,
              "source_decodes_initial": initial_source_decodes,
              "source_decodes_verification": fine_cache.source_decodes-initial_source_decodes,
              "thumbnail_disk_hits": fine_cache.disk_hits, "fine_memory_hits": fine_cache.memory_hits,
              "timings": {"preparation_seconds": indexed_at-started,
                          "source_decode_and_feature_seconds": fine_cache.source_seconds,
                          "thumbnail_compression_seconds": encode_seconds,
                          "index_build_seconds": tree_built_at-indexed_at,
                          "candidate_search_seconds": candidate_seconds,
                          "fine_lookup_seconds": lookup_seconds,
                          "thumbnail_disk_read_decode_seconds": fine_cache.disk_seconds,
                          "fine_comparison_seconds": comparison_seconds,
                          "total_seconds": finished_at-overall_started},
              "note": "Groups are connected components for review. copy_candidate and similar_frame "
                      "are score bands, not ground-truth labels or retention decisions. "
                      "All archive retention decisions belong to the user; no file actions are performed."}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"Done: {matches} copy candidates, {review_matches-matches} similar-frame pairs, "
          f"{len(review_groups)} review groups", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--review-threshold", type=float,
                        help="Defaults to min(profile review threshold, --threshold)")
    parser.add_argument("--no-thumbnail-cache", action="store_true",
                        help="Benchmark the previous coarse-only cache strategy")
    arguments = parser.parse_args()
    scan(arguments.source, arguments.output, arguments.cache, arguments.threshold,
         min(DEFAULT_REVIEW_THRESHOLD, arguments.threshold)
         if arguments.review_threshold is None else arguments.review_threshold,
         persist_thumbnails=not arguments.no_thumbnail_cache)
