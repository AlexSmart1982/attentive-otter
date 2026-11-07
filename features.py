"""Compact LAB masks and a non-neural appearance check for aligned copies.

The legacy implementation remains in comparsion.py. This engine includes all
valid windows, normalizes each orientation with its own counts, and compares
both images' orientations. The resulting Dice score is symmetric and in [0, 1].
"""

from dataclasses import dataclass
import time

import cv2
import numpy as np

from image_io import read_image, correct_stride


@dataclass
class Descriptor:
    bits: np.ndarray
    counts: np.ndarray
    appearance: np.ndarray


def local_ranges(lab, kernel, stride):
    """Top-left anchored max-min, sampled only where the full window fits."""
    element = np.ones((kernel, kernel), dtype=np.uint8)
    maximum = cv2.dilate(lab, element, anchor=(0, 0))
    minimum = cv2.erode(lab, element, anchor=(0, 0))
    valid_h, valid_w = lab.shape[0] - kernel + 1, lab.shape[1] - kernel + 1
    return (maximum - minimum)[:valid_h:stride, :valid_w:stride]


def pack_masks(ranges, l_threshold, ab_threshold):
    threshold = np.array([l_threshold, ab_threshold, ab_threshold])
    masks = ranges > threshold
    # A, H, W, C -> A, C, H*W; padding bits are always zero.
    masks = np.moveaxis(masks, -1, 1).reshape(len(masks), 3, -1)
    counts = masks.sum(axis=-1, dtype=np.int64)
    packed = np.packbits(masks, axis=-1)
    padding = (-packed.shape[-1]) % 8
    packed = np.pad(packed, ((0, 0), (0, 0), (0, padding)))
    return packed.copy().view(np.uint64), counts


def appearance_vectors(bgr):
    vectors = []
    for image in bgr:
        small = cv2.resize(image, (72, 96), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 0.8)
        vectors.append(small.astype(np.float32).reshape(-1) / 255)
    return np.stack(vectors)


def read_orientations(path):
    original = read_image(path)
    if original.shape[0] < original.shape[1]:
        original = cv2.rotate(original, cv2.ROTATE_90_CLOCKWISE)
    rotations = (0, 1, 2, 3) if original.shape[0] == original.shape[1] else (0, 2)
    return [np.ascontiguousarray(np.rot90(original, turns)) for turns in rotations]


def build_descriptors(rows, configurations):
    """Process a file once, reuse preprocessing/ranges across threshold sweeps."""
    all_descriptors = [[] for _ in configurations]
    started = time.perf_counter()
    for index, row in enumerate(rows):
        bgr = read_orientations(row["path"])
        appearance = appearance_vectors(bgr)
        prepared, ranges_cache, masks_cache = {}, {}, {}
        for config, destination in zip(configurations, all_descriptors):
            height, width = config.get("height", 400), config.get("width", 300)
            sigma = config.get("blur_sigma", 0.0)
            before_lab = config.get("resize_before_lab", True)
            kernel, desired_stride = config.get("kernel", 6), config.get("stride", 2)
            if (not isinstance(kernel, int) or kernel < 1 or desired_stride < 1
                    or min(height, width) <= kernel or sigma < 0):
                raise ValueError("Invalid resolution, kernel, stride or blur")
            prep_key = (height, width, sigma, before_lab)
            if prep_key not in prepared:
                layers = []
                for image in bgr:
                    if before_lab:
                        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
                        if sigma:
                            image = cv2.GaussianBlur(image, (0, 0), sigma)
                        image = cv2.cvtColor(image, cv2.COLOR_BGR2Lab)
                    else:
                        image = cv2.cvtColor(image, cv2.COLOR_BGR2Lab)
                        image = cv2.resize(image, (width, height))
                        if sigma:
                            image = cv2.GaussianBlur(image, (0, 0), sigma)
                    layers.append(image)
                prepared[prep_key] = np.stack(layers)
            stride = correct_stride(kernel_size=kernel, desired_stride=desired_stride,
                                       h=height, w=width)
            range_key = (prep_key, kernel, stride)
            if range_key not in ranges_cache:
                ranges_cache[range_key] = np.stack([
                    local_ranges(image, kernel, stride) for image in prepared[prep_key]])
            ab_threshold = config.get("threshold", 9)
            l_threshold = config.get("l_threshold", 27)
            if not 0 <= min(ab_threshold, l_threshold) <= max(ab_threshold, l_threshold) <= 255:
                raise ValueError("Thresholds must be in [0, 255]")
            mask_key = (range_key, ab_threshold, l_threshold)
            if mask_key not in masks_cache:
                bits, counts = pack_masks(ranges_cache[range_key], l_threshold, ab_threshold)
                masks_cache[mask_key] = Descriptor(bits, counts, appearance)
            destination.append(masks_cache[mask_key])
        if (index + 1) % 50 == 0 or index + 1 == len(rows):
            print(f"Features: {index + 1}/{len(rows)}, {time.perf_counter()-started:.1f}s", flush=True)
    return all_descriptors


@dataclass
class PairEvidence:
    intersections: np.ndarray
    counts: np.ndarray
    appearance: np.ndarray

    def scores(self, weights=(1, 1, 1)):
        weights = np.asarray(weights, dtype=np.float64)
        if weights.shape != (3,) or np.any(weights < 0) or not np.any(weights > 0):
            raise ValueError("Use three nonnegative LAB weights, at least one positive")
        left, right = np.tril_indices(len(self.counts), -1)
        counts = self.counts @ weights
        scores = np.zeros(len(left))
        for start in range(0, len(left), 4096):
            end = start + 4096
            denominator = counts[left[start:end], :, None] + counts[right[start:end], None, :]
            numerator = 2 * (self.intersections[start:end] @ weights)
            dice = np.divide(numerator, denominator, out=np.zeros_like(numerator), where=denominator > 0)
            scores[start:end] = dice.max(axis=(1, 2))
        return np.clip(scores, 0, 1)


def pair_evidence(descriptors, batch_size=32, appearance=None):
    """Cache intersections so channel weights/cutoffs need no new image work.

Appearance is 1 - RGB RMSE on blurred thumbnails. Orientations are maximized
independently for the appearance and edge scores. No crop/mirror alignment.
"""
    n = len(descriptors)
    intersections = np.zeros((n * (n - 1) // 2, 4, 4, 3), dtype=np.uint32)
    counts_table = np.zeros((n, 4, 3), dtype=np.int64)
    calculate_appearance = appearance is None
    if calculate_appearance:
        appearance = np.zeros(len(intersections))
    groups = {}
    for i, descriptor in enumerate(descriptors):
        groups.setdefault(len(descriptor.bits), []).append(i)
        counts_table[i, :len(descriptor.counts)] = descriptor.counts
    stacked = {}
    for count, ids in groups.items():
        ids = np.asarray(ids)
        stacked[count] = (ids, np.stack([descriptors[i].bits for i in ids]),
                          np.stack([descriptors[i].appearance for i in ids]))
    for i, query in enumerate(descriptors):
        offset = i * (i - 1) // 2
        for count, (ids, bits, vectors) in stacked.items():
            previous = np.flatnonzero(ids < i)
            for start in range(0, len(previous), batch_size):
                positions = previous[start:start + batch_size]
                js = ids[positions]
                best_appearance = np.zeros(len(js))
                for a in range(len(query.bits)):
                    for b in range(count):
                        intersection = np.bitwise_count(
                            np.bitwise_and(bits[positions, b], query.bits[a])).sum(axis=-1)
                        intersections[offset + js, a, b] = intersection
                        if calculate_appearance:
                            diff = vectors[positions, b] - query.appearance[a]
                            score = 1 - np.sqrt(np.mean(diff * diff, axis=-1))
                            best_appearance = np.maximum(best_appearance, score)
                if calculate_appearance:
                    appearance[offset + js] = best_appearance
    return PairEvidence(intersections, counts_table, np.clip(appearance, 0, 1))


def pair_scores(descriptors, weights=(1, 1, 1)):
    evidence = pair_evidence(descriptors)
    return evidence.scores(weights), evidence.appearance
