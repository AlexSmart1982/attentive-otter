"""Pair, group and neighbourhood measurements for labelled sample images."""

import numpy as np

from tune import metrics


def labels_for(rows):
    left, right = np.tril_indices(len(rows), -1)
    ids = np.array([row["source_id"] for row in rows])
    return ids[left] == ids[right]


def score_matrix(scores, size):
    matrix = np.zeros((size, size))
    lower = np.tril_indices(size, -1)
    matrix[lower] = scores
    matrix[(lower[1], lower[0])] = scores
    return matrix


def group_threshold(rows, scores):
    """Highest cutoff retaining connectivity of every true duplicate group.

Use only the training split. A maximum spanning tree supplies each source's
bottleneck. This evaluates grouping without requiring every pair to match.
"""
    matrix = score_matrix(scores, len(rows))
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(row["source_id"], []).append(i)
    bottlenecks = []
    for ids in groups.values():
        if len(ids) < 2:
            continue
        parents = {i: i for i in ids}

        def find(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i

        candidates = sorted((matrix[i, j], i, j) for x, i in enumerate(ids) for j in ids[:x])
        needed = len(ids) - 1
        for score, i, j in reversed(candidates):
            a, b = find(i), find(j)
            if a != b:
                parents[a] = b
                needed -= 1
                if not needed:
                    bottlenecks.append(float(score))
                    break
    return min(bottlenecks) if bottlenecks else 1.0


def evaluate(rows, scores, threshold):
    labels = labels_for(rows)
    result = metrics(scores, labels, threshold)
    matrix = score_matrix(scores, len(rows))
    linked = (matrix >= threshold) & (matrix > 0)
    np.fill_diagonal(linked, False)
    sources = np.array([row["source_id"] for row in rows])
    scenes = np.array([row.get("scene_id", row["source_id"]) for row in rows])
    same_source = sources[:, None] == sources[None, :]
    same_scene = scenes[:, None] == scenes[None, :]
    np.fill_diagonal(same_source, False)
    series = same_scene & ~same_source
    unrelated = ~same_scene & ~same_source
    np.fill_diagonal(series, False)
    top1, ties, strict_series, strict_other, series_margins, other_margins = [], [], [], [], [], []
    for i in range(len(rows)):
        copies = matrix[i, same_source[i]]
        if not len(copies):
            continue
        others = matrix[i, ~same_source[i] & (np.arange(len(rows)) != i)]
        if len(others):
            top1.append(float(copies.max()) > float(others.max()))
            ties.append(float(copies.max()) == float(others.max()))
        if np.any(series[i]):
            margin = float(copies.min() - matrix[i, series[i]].max())
            series_margins.append(margin)
            strict_series.append(margin > 0)
        if np.any(unrelated[i]):
            margin = float(copies.min() - matrix[i, unrelated[i]].max())
            other_margins.append(margin)
            strict_other.append(margin > 0)
    recovered, group_count, isolated = 0, 0, 0
    for source in set(sources):
        ids = np.flatnonzero(sources == source)
        if len(ids) < 2:
            continue
        group_count += 1
        seen, pending = set(), [int(ids[0])]
        while pending:
            i = pending.pop()
            if i in seen:
                continue
            seen.add(i)
            pending.extend(int(j) for j in ids if linked[i, j] and int(j) not in seen)
        recovered += len(seen) == len(ids)
        isolated += int(np.sum(~np.any(linked[np.ix_(ids, ids)], axis=1)))
    lower = np.tril(np.ones_like(linked), -1).astype(bool)
    result.update({
        "sources": group_count, "connected_sources": int(recovered),
        "group_recall": recovered / group_count if group_count else 0,
        "isolated_versions": isolated,
        "series_false_positives": int(np.sum(linked & series & lower)),
        "unrelated_false_positives": int(np.sum(linked & unrelated & lower)),
        "top1_correct": float(np.mean(top1)) if top1 else None,
        "top1_tie": float(np.mean(ties)) if ties else None,
        "all_copies_before_series": float(np.mean(strict_series)) if strict_series else None,
        "all_copies_before_unrelated": float(np.mean(strict_other)) if strict_other else None,
        "series_margin_quantiles": np.quantile(series_margins, [0, .5, 1]).tolist() if series_margins else [],
        "unrelated_margin_quantiles": np.quantile(other_margins, [0, .5, 1]).tolist() if other_margins else []})
    return result
