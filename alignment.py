"""Bounded global translation and residual measurements for experiments.

These routines do not change the production similarity profile or index.
Coordinates are in the common comparison grid, not native source pixels.
"""

import cv2
import numpy as np


def residual_statistics(a, b, valid):
    energy = np.mean((a-b)**2, axis=-1)[valid].astype(np.float64)
    rmse = float(np.sqrt(energy.mean()))
    top_count = max(1, int(np.ceil(len(energy)*.01)))
    total = energy.sum()
    return {"rmse": rmse, "similarity": 1-rmse,
            "p95_pixel_error": float(np.sqrt(np.quantile(energy, .95))),
            "p99_pixel_error": float(np.sqrt(np.quantile(energy, .99))),
            "top_1_percent_energy_share": float(np.partition(energy, -top_count)[-top_count:].sum()/total)
            if total else 0.0}


def align_translation(a, b, max_shift=4.0):
    """Align B to A on equal-sized RGB/BGR float or uint8 arrays.

Phase correlation initializes ECC with translation only. Invalid/large
estimates are rejected. Compare before/after on the same interior pixels.
"""
    if a.shape != b.shape or a.ndim != 3 or a.shape[2] != 3 or min(a.shape[:2]) < 32:
        raise ValueError("Use equal RGB/BGR arrays with dimensions >=32")
    if not 0 <= max_shift < min(a.shape[:2])/4:
        raise ValueError("Invalid maximum displacement")
    a = a.astype(np.float32) / (255 if a.dtype == np.uint8 else 1)
    b = b.astype(np.float32) / (255 if b.dtype == np.uint8 else 1)
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise ValueError("Images must be finite")
    gray_a = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (0, 0), .8)
    gray_b = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (0, 0), .8)
    height, width = a.shape[:2]
    hann = cv2.createHanningWindow((width, height), cv2.CV_32F)
    phase, response = cv2.phaseCorrelate(gray_a.copy(), gray_b.copy(), hann)
    warp = np.eye(2, 3, dtype=np.float32)
    if np.all(np.isfinite(phase)) and max(abs(v) for v in phase) <= max_shift:
        warp[:, 2] = phase
    status, correlation = "ecc", None
    try:
        correlation, fitted = cv2.findTransformECC(
            gray_a, gray_b, warp.copy(), cv2.MOTION_TRANSLATION,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6), None, 5)
        if np.all(np.isfinite(fitted)) and np.max(np.abs(fitted[:, 2])) <= max_shift:
            warp = fitted
        else:
            status = "ecc_outside_bound_phase_fallback"
    except cv2.error:
        status = "phase_fallback"
    aligned = cv2.warpAffine(b, warp, (width, height),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REFLECT_101)
    margin = int(np.ceil(max_shift))+2
    valid = np.zeros((height, width), dtype=bool)
    valid[margin:-margin, margin:-margin] = True
    before, after = residual_statistics(a, b, valid), residual_statistics(a, aligned, valid)
    report = {"dx": float(warp[0, 2]), "dy": float(warp[1, 2]), "phase_response": response,
              "ecc_correlation": correlation, "status": status,
              "valid_pixel_fraction": float(valid.mean()), "before": before, "after": after,
              "rmse_reduction": 1-after["rmse"]/before["rmse"] if before["rmse"] else 0.0}
    return aligned, report
