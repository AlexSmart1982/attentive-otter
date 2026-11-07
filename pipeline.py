"""Bounded threaded preparation, one SQLite writer, cached scan and review export."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import time

import cv2
import pillow_heif

from export_review import export_review
from features import appearance_vectors, read_orientations
from preferences import SEARCH_MODES
from source_snapshot import source_snapshot
from scan import (SCHEMA, check_cancel, collect_files, coarse_vectors, encode_thumbnail,
                  ensure_cache_schema, scan, source_folders)


def prepare_one(path, size, mtime):
    with source_snapshot(path) as identity:
        if (size, mtime) != (identity['bytes'], identity['mtime_ns']):
            raise RuntimeError("Файл изменился во время чтения: " + str(path))
        fine = appearance_vectors(read_orientations(path))
        return coarse_vectors(fine).tobytes(), encode_thumbnail(fine), json.dumps(identity, sort_keys=True)


def prepare_cache(roots, cache, workers=8, progress=None, cancel_event=None):
    if not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be positive")
    check_cancel(cancel_event)
    files = collect_files(roots)
    previous_cv, previous_heif = cv2.getNumThreads(), pillow_heif.options.DECODE_THREADS
    cv2.setNumThreads(1)
    pillow_heif.options.DECODE_THREADS = 1
    pillow_heif.register_heif_opener()
    errors, decoded, cached = [], 0, 0
    started = time.perf_counter()
    try:
        with closing(sqlite3.connect(cache)) as db, ThreadPoolExecutor(max_workers=workers) as pool:
            ensure_cache_schema(db)
            pending, iterator = deque(), iter(files)
            done = 0
            while True:
                check_cancel(cancel_event)
                while len(pending) < 2*workers:
                    try:
                        path = next(iterator)
                    except StopIteration:
                        break
                    try:
                        stat = path.stat()
                        with source_snapshot(path) as identity:
                            row = db.execute("SELECT bytes,mtime,schema,thumbnail IS NOT NULL,identity "
                                             "FROM features WHERE path=?", (str(path),)).fetchone()
                        future = None if row and row == (stat.st_size, stat.st_mtime_ns, SCHEMA, 1, json.dumps(identity, sort_keys=True)) else pool.submit(
                            prepare_one, path, stat.st_size, stat.st_mtime_ns)
                        pending.append((path, stat, future, None))
                    except Exception as error:
                        pending.append((path, None, None, error))
                if not pending:
                    break
                path, stat, future, error = pending.popleft()
                try:
                    if error:
                        raise error
                    if future is None:
                        cached += 1
                    else:
                        coarse, thumbnail, identity = future.result()
                        decoded += 1
                        db.execute("INSERT OR REPLACE INTO features (path,bytes,mtime,schema,coarse,thumbnail,identity) VALUES (?,?,?,?,?,?,?)",
                                   (str(path), stat.st_size, stat.st_mtime_ns, SCHEMA, coarse, thumbnail, identity))
                except Exception as error:
                    errors.append({"path": str(path), "error": str(error)})
                done += 1
                if done % 50 == 0 or done == len(files):
                    db.commit()
                    if progress:
                        progress({"stage": "prepare", "done": done, "total": len(files),
                                  "workers": workers, "errors": len(errors)})
    finally:
        cv2.setNumThreads(previous_cv)
        pillow_heif.options.DECODE_THREADS = previous_heif
    return {"files": len(files), "decoded": decoded, "cached": cached,
            "errors": errors, "seconds": time.perf_counter()-started, "workers": workers}


def run_pipeline(sources, output, cache, workers=8, progress=None, cancel_event=None, search_mode="strict"):
    if search_mode not in SEARCH_MODES:
        raise ValueError("Unknown search mode")
    roots, output, cache = source_folders(sources), Path(output).resolve(), Path(cache).resolve()
    if output.exists() or any(output == root or root in output.parents or cache == root or root in cache.parents
                              for root in roots):
        raise ValueError("Результаты и кеш должны находиться вне папок-источников")
    output.mkdir(parents=True)
    cache.parent.mkdir(parents=True, exist_ok=True)
    preparation = prepare_cache(roots, cache, workers, progress, cancel_event)
    check_cancel(cancel_event)
    result = scan(roots, output/"scan", cache, review_threshold=SEARCH_MODES[search_mode][1],
                  progress=progress, cancel_event=cancel_event)
    check_cancel(cancel_event)
    document = export_review(output/"scan"/"report.json", output/"review", progress, cancel_event)
    document.update(search_mode=search_mode, feature_cache=str(cache))
    from storage import write_json
    write_json(output/"review"/"review.json", document)
    return {"review_path": str(output/"review"/"review.json"), "report": result,
            "preparation": preparation, "groups": len(result["review_groups"]),
            "review_files": len(document["files"])}
