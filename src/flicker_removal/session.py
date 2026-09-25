"""Scene-assisted residual illumination fitting; reference pixels never enter outputs.

The portable lighting profile remains sufficient for ordinary batch correction.
This optional archive also contains registered proxies of one repeated scene.
Known targets are excluded from their own reference; unrelated views fall back.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.ndimage import gaussian_filter

from .model import LightingProfile, robust_ls


def _cv2():
    try:
        import cv2
    except ImportError as error:
        raise RuntimeError("Scene refinement requires: uv sync --extra scene") from error
    cv2.setNumThreads(1)
    cv2.setRNGSeed(17)
    return cv2


def image_digest(image):
    return hashlib.sha256(np.ascontiguousarray(image, dtype=np.float32).tobytes()).hexdigest()


def profile_digest(profile):
    return hashlib.sha256(json.dumps(asdict(profile), sort_keys=True).encode()).hexdigest()


def gray(image):
    value = np.log(np.maximum(image[:, :, 1], 0.002))
    lo, hi = np.percentile(value, [1, 99])
    return np.uint8(np.clip((value - lo) / max(hi - lo, 1e-6), 0, 1) * 255)


def register(image, reference):
    """Fit a background-plane homography with evidence, or reject registration."""
    cv = _cv2()
    sift = cv.SIFT_create(nfeatures=5000)
    kp, desc = sift.detectAndCompute(gray(image), None)
    kp0, desc0 = sift.detectAndCompute(gray(reference), None)
    if desc is None or desc0 is None:
        raise ValueError("Insufficient registration features")
    pairs = cv.BFMatcher().knnMatch(desc, desc0, k=2)
    matches = [
        pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance
    ]
    if len(matches) < 40:
        raise ValueError("Insufficient registration matches")
    src = np.float32([kp[m.queryIdx].pt for m in matches])
    dst = np.float32([kp0[m.trainIdx].pt for m in matches])
    H, keep = cv.findHomography(src, dst, cv.RANSAC, 1.7, maxIters=10000, confidence=0.999)
    if H is None or not np.all(np.isfinite(H)) or int(keep.sum()) < 40:
        raise ValueError("Registration consensus failed")
    keep = keep[:, 0] > 0
    residual = cv.perspectiveTransform(src[:, None], H)[:, 0] - dst
    rms = float(np.sqrt(np.mean(residual[keep] ** 2) * 2))
    h, w = image.shape[:2]
    coverage = np.ptp(src[keep], axis=0) / [w, h]
    if rms > 1.0 or np.min(coverage) < 0.4:
        raise ValueError("Registration lacks reliable spatial coverage")
    return H, {
        "matches": len(matches),
        "inliers": int(keep.sum()),
        "rms_proxy_pixels": rms,
        "feature_extent_fraction": coverage.tolist(),
    }


def template_from_stack(stack, omit=()):
    """Median log-exposure template with target exclusion and motion rejection."""
    keep = np.ones(len(stack), bool)
    keep[list(omit)] = False
    if keep.sum() < 5:
        raise ValueError("At least five other registered views are required")
    data = stack[keep].copy()
    median = np.median(data, axis=0)
    for _ in range(4):
        residual = data - median[None]
        stable = (np.max(np.median(abs(residual), axis=0), axis=2) < 0.12) & (
            np.min(median, axis=2) > 0
        )
        if stable.sum() < 1000:
            raise ValueError("Insufficient repeated stable background")
        offsets = np.median(residual[:, stable, :], axis=1)
        data -= offsets[:, None, None, :]
        median = np.median(data, axis=0)
    mad = np.median(abs(data - median[None]), axis=0)
    smooth = gaussian_filter(median, (1, 1, 0))
    dx = np.max(abs(np.gradient(smooth, axis=1)), axis=2)
    dy = np.max(abs(np.gradient(smooth, axis=0)), axis=2)
    mask = (
        (np.max(mad, axis=2) < 0.065) & (dx < 0.045) & (dy < 0.045) & (np.min(median, axis=2) > 0)
    )
    return median, mask


def ratio_data(image, homography, template, static):
    cv = _cv2()
    h, w = image.shape[:2]
    yy, xx = np.mgrid[:h, :w]
    coordinates = np.stack([xx, yy], axis=-1).astype(np.float32)
    mapped = cv.perspectiveTransform(coordinates.reshape(-1, 1, 2), homography).reshape(h, w, 2)
    reference = cv.remap(template, mapped[:, :, 0], mapped[:, :, 1], cv.INTER_LINEAR)
    mask = (
        cv.remap(static.astype(np.float32), mapped[:, :, 0], mapped[:, :, 1], cv.INTER_LINEAR)
        > 0.99
    )
    log_image = np.log(np.maximum(image, 0.001))
    dx = np.max(abs(np.gradient(gaussian_filter(log_image, (1, 1, 0)), axis=1)), axis=2)
    mask &= (dx < 0.05) & (np.min(image, axis=2) > 0.008) & (np.max(image, axis=2) < 0.8)
    mask[:8] = False
    mask[-8:] = False
    mask[:, :8] = False
    mask[:, -8:] = False
    return coordinates, mask, log_image - reference


def _basis(x, y, q):
    low = np.column_stack(
        [np.ones_like(x), x - 0.5, y - 0.5, (x - 0.5) ** 2, (x - 0.5) * (y - 0.5), (y - 0.5) ** 2]
    )
    spatial = low[:, :3]
    wave = np.column_stack(
        [spatial * np.cos(2 * np.pi * q * y[:, None]), spatial * np.sin(2 * np.pi * q * y[:, None])]
    )
    return low, wave


def refine_gain(image, homography, template, static, q, *, max_log_gain=0.08):
    """Return an extra gain only when both independent spatial folds support it.

    Eighteen harmonic coefficients describe affine spatial amplitude/phase in
    camera RGB. Six nuisance shading coefficients per channel never enter gain.
    The small extra gain is not allowed to turn weak frames into corrections;
    that gate belongs to SceneReference.polish before this function is called.
    """
    ones = np.ones_like(image, dtype=np.float32)
    if not np.isfinite(q) or q <= 0 or not 0 < max_log_gain <= 0.08:
        raise ValueError("Invalid residual frequency or gain limit")
    coordinates, mask, ratio = ratio_data(image, homography, template, static)
    h, w = image.shape[:2]
    report = {
        "status": "unchanged",
        "q": float(q),
        "stable_fraction": float(mask.mean()),
        "samples": int(mask.sum()),
        "maximum_extra_log_gain": max_log_gain,
    }
    if mask.mean() < 0.05 or mask.sum() < 8000:
        return ones, {**report, "reason": "insufficient_static_support"}
    x, y = (coordinates[:, :, 0][mask] + 0.5) / w, (coordinates[:, :, 1][mask] + 0.5) / h
    # A short vertical patch cannot separate a sinusoid from ordinary shading.
    # Checkerboard validation alone would test mostly X and permit unsupported
    # extrapolation across the rest of the image. Require broad row/phase support.
    phase_counts = np.bincount(np.minimum((np.mod(q * y, 1) * 12).astype(int), 11), minlength=12)
    row_counts = np.bincount(np.minimum((y * 8).astype(int), 7), minlength=8)
    report["support_extent_xy"] = [float(np.ptp(x)), float(np.ptp(y))]
    report["supported_row_bins"] = int(np.sum(row_counts >= len(y) * 0.01))
    report["minimum_phase_fraction"] = float(phase_counts.min() / len(y))
    if (
        np.ptp(y) < 0.6
        or np.ptp(x) < 0.4
        or report["supported_row_bins"] < 5
        or report["minimum_phase_fraction"] < 0.01
    ):
        return ones, {**report, "reason": "insufficient_row_or_phase_coverage"}
    low, wave = _basis(x, y, q)
    A = np.column_stack([wave, low])[::4]
    low, wave = low[::4], wave[::4]
    sample = ratio[mask][::4]
    parity = ((np.floor(x * 8).astype(int) + np.floor(y * 6).astype(int)) % 2)[::4]
    folds, predictions = [], []
    for fold in (0, 1):
        train, test = parity == fold, parity != fold
        if min(train.sum(), test.sum()) < 1000:
            return ones, {**report, "reason": "insufficient_spatial_holdout"}
        nuisance = np.array(
            [robust_ls(low[train], sample[train, c], iterations=6) for c in range(3)]
        )
        coefficients = np.array(
            [robust_ls(A[train], sample[train, c], iterations=6) for c in range(3)]
        )
        e0 = sample[test] - low[test] @ nuisance.T
        e1 = sample[test] - A[test] @ coefficients.T
        valid = np.max(abs(e0), axis=1) < 0.15
        if valid.sum() < 1000:
            return ones, {**report, "reason": "insufficient_heldout_inliers"}
        baseline = float(np.sqrt(np.mean(e0[valid] ** 2)))
        refined = float(np.sqrt(np.mean(e1[valid] ** 2)))
        folds.append(
            {
                "heldout_samples": int(valid.sum()),
                "shading_only_log_rms": baseline,
                "plus_harmonic_log_rms": refined,
                "heldout_error_reduction": 1 - refined / max(baseline, 1e-12),
            }
        )
        predictions.append(wave @ coefficients[:, :6].T)
    report["folds"] = folds
    report["fold_gain_disagreement_log_rms"] = float(
        np.sqrt(np.mean((predictions[0] - predictions[1]) ** 2))
    )
    report["predicted_residual_log_rms"] = float(
        np.sqrt(np.mean(((predictions[0] + predictions[1]) / 2) ** 2))
    )
    improvements = [f["heldout_error_reduction"] for f in folds]
    if (
        min(improvements) <= 0
        or np.mean(improvements) < 0.005
        or report["predicted_residual_log_rms"] < 0.002
    ):
        return ones, {**report, "reason": "residual_not_confirmed_on_heldout_regions"}
    coef = np.array([robust_ls(A, sample[:, c], iterations=6) for c in range(3)])
    keep = np.max(abs(sample - A @ coef.T), axis=1) < 0.05
    if keep.sum() < 1000:
        return ones, {**report, "reason": "insufficient_final_inliers"}
    coef = np.array([robust_ls(A[keep], sample[keep, c], iterations=6) for c in range(3)])
    _, full_wave = _basis(
        (coordinates[:, :, 0].ravel() + 0.5) / w, (coordinates[:, :, 1].ravel() + 0.5) / h, q
    )
    log_gain = (-full_wave @ coef[:, :6].T).reshape(image.shape)
    if np.mean(abs(log_gain) >= max_log_gain) > 0.01:
        return ones, {
            **report,
            "reason": "residual_exceeds_safe_gain_bound",
            "bound_fraction": float(np.mean(abs(log_gain) >= max_log_gain)),
        }
    gain = np.exp(np.clip(log_gain, -max_log_gain, max_log_gain)).astype(np.float32)
    report.update(
        status="polished",
        reason="corroborated_residual",
        coefficients=coef.tolist(),
        inlier_samples=int(keep.sum() * 4),
        fit_log_rms=float(np.sqrt(np.mean((sample[keep] - A[keep] @ coef.T) ** 2))),
        gain_min=float(gain.min()),
        gain_max=float(gain.max()),
        bound_fraction=float(np.mean(abs(log_gain) >= max_log_gain)),
        extra_log_gain_rms=float(np.sqrt(np.mean(np.log(gain) ** 2))),
    )
    return gain, report


class SceneReference:
    def __init__(self, stack, anchor, homographies, metadata):
        self.stack = stack
        self.anchor = anchor
        self.homographies = homographies
        self.metadata = metadata
        self._common_template = None

    @classmethod
    def build(cls, images, metadata, gains, reports, profile):
        if len({len(images), len(metadata), len(gains), len(reports)}) != 1:
            raise ValueError("Scene images, metadata, gains and reports must have equal lengths")
        if len(images) < 6:
            raise ValueError("Scene calibration requires at least six photographs")
        eligible = [i for i, r in enumerate(reports) if r["status"] == "corrected"]
        anchor_index = eligible[0] if eligible else len(images) // 2
        anchor = images[anchor_index] * gains[anchor_index]
        reference_metadata = metadata[anchor_index]
        cv = _cv2()
        h, w = anchor.shape[:2]
        stack, transforms, records = [], [], []
        for i, (image, meta, gain) in enumerate(zip(images, metadata, gains)):
            if image.shape != anchor.shape:
                raise ValueError("All scene proxies must share geometry")
            corrected = image * gain
            try:
                H, registration = register(corrected, anchor)
            except ValueError as error:
                records.append({"file": meta["file"], "registered": False, "reason": str(error)})
                continue
            exposure = (
                meta["shutter_s"]
                * (meta["iso"] / reference_metadata["iso"])
                * (reference_metadata["aperture"] / meta["aperture"]) ** 2
            )
            if not np.isfinite(exposure) or exposure <= 0:
                raise ValueError("Valid shutter, aperture and ISO required for scene calibration")
            warped = cv.warpPerspective(corrected / exposure, H, (w, h), flags=cv.INTER_LINEAR)
            stack.append(np.log(np.maximum(warped, 0.1)).astype(np.float32))
            transforms.append(H)
            records.append(
                {
                    "file": meta["file"],
                    "registered": True,
                    "stack_index": len(stack) - 1,
                    "image_digest": image_digest(image),
                    "registration": registration,
                }
            )
        if len(stack) < 6:
            raise ValueError("Fewer than six views registered reliably")
        data = {
            "schema_version": 1,
            "profile_sha256": profile_digest(profile),
            "anchor": reference_metadata["file"],
            "records": records,
            "cycles_per_sensor_height": profile.cycles_per_sensor_height,
            "limitation": "Scene-specific background reference. Moving subjects are not clean-reference validated.",
        }
        return cls(np.stack(stack), anchor.astype(np.float32), np.stack(transforms), data)

    def save(self, path):
        with Path(path).open("xb") as stream:
            np.savez_compressed(
                stream,
                stack=self.stack,
                anchor=self.anchor,
                homographies=self.homographies,
                metadata=json.dumps(self.metadata),
            )

    @classmethod
    def load(cls, path, profile):
        with np.load(path, allow_pickle=False) as archive:
            metadata = json.loads(str(archive["metadata"]))
            if metadata["schema_version"] != 1 or metadata["profile_sha256"] != profile_digest(
                profile
            ):
                raise ValueError("Scene reference does not match this lighting profile")
            return cls(archive["stack"], archive["anchor"], archive["homographies"], metadata)

    def polish(self, image, gain, baseline_report, profile, *, source_name=None):
        start = time.perf_counter()
        residual_veto = baseline_report["status"] == "unchanged_residual_check" and baseline_report[
            "reasons"
        ] == ["independent_row_score_increased"]
        if baseline_report["status"] != "corrected" and not residual_veto:
            return gain, {
                "status": "unchanged",
                "reason": "baseline_confidence_gate",
                "seconds": time.perf_counter() - start,
            }
        if image.shape != self.anchor.shape:
            return gain, {
                "status": "unchanged",
                "reason": "scene_geometry_mismatch",
                "seconds": time.perf_counter() - start,
            }
        digest = image_digest(image)
        known = [r for r in self.metadata["records"] if r.get("image_digest") == digest]
        omitted = [
            r["stack_index"]
            for r in self.metadata["records"]
            if r.get("registered")
            and (
                r.get("image_digest") == digest
                or (source_name is not None and r["file"] == source_name)
            )
        ]
        try:
            if known:
                H = self.homographies[known[0]["stack_index"]]
                registration = known[0]["registration"]
            else:
                H, registration = register(image * gain, self.anchor)
            if omitted:
                template, mask = template_from_stack(self.stack, omitted)
            else:
                if self._common_template is None:
                    self._common_template = template_from_stack(self.stack)
                template, mask = self._common_template
            extra, report = refine_gain(
                image * gain, H, template, mask, profile.cycles_per_sensor_height
            )
        except ValueError as error:
            return gain, {
                "status": "unchanged",
                "reason": str(error),
                "seconds": time.perf_counter() - start,
            }
        report.update(
            registration=registration,
            target_excluded=bool(omitted),
            excluded_references=len(omitted),
            seconds=time.perf_counter() - start,
        )
        if report["status"] != "polished":
            return gain, report
        if residual_veto and min(f["heldout_error_reduction"] for f in report["folds"]) < 0.05:
            return gain, {
                **report,
                "status": "unchanged",
                "reason": "insufficient_evidence_to_override_row_score_veto",
            }
        report["baseline_residual_veto_override"] = residual_veto
        combined = np.exp(
            np.clip(
                np.log(gain.astype(np.float64)) + np.log(extra),
                -profile.max_log_gain,
                profile.max_log_gain,
            )
        ).astype(np.float32)
        return combined, report
