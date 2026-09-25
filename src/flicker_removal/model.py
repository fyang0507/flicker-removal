"""Shared sensor-row calibration and robust, low-resolution gain estimation.

No image content is generated, resampled, denoised or fused by this module.
The only image operation is multiplication by a smooth positive gain field.
Temporal frequency is deliberately not inferred from spatial periodicity alone.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import time
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from scipy.interpolate import RegularGridInterpolator
from scipy.optimize import minimize_scalar


@dataclass
class LightingProfile:
    schema_version: int
    camera: str
    sensor_height: int
    sensor_width: int
    shutter_mode: str
    cycles_per_sensor_height: float
    rgb_relative_modulation: list[float]
    exposure_amplitudes: dict[str, float]
    training_files: list[str]
    min_green_amplitude: float = 0.025
    max_log_gain: float = 0.30
    mixing_grid: tuple[int, int] = (9, 7)
    temporal_frequency_hz: float | None = None
    row_time_seconds: float | None = None
    frequency_status: str = "empirical spatial calibration; temporal frequency unidentified"

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported lighting profile schema")
        obj = cls(**data)
        if not 2 <= obj.cycles_per_sensor_height <= 40:
            raise ValueError("Invalid row frequency")
        if len(obj.rgb_relative_modulation) != 3 or min(obj.rgb_relative_modulation) <= 0:
            raise ValueError("Invalid RGB response")
        return obj


def robust_ls(A, b, weight=None, iterations=4):
    weight = np.ones_like(b) if weight is None else np.asarray(weight)
    w = weight.copy()
    for _ in range(iterations):
        root = np.sqrt(np.maximum(w, 0))
        coef = np.linalg.lstsq(A * root[:, None], b * root, rcond=None)[0]
        residual = b - A @ coef
        scale = max(np.median(np.abs(residual - np.median(residual))) * 1.4826, 0.001)
        w = weight * np.minimum(1, 1.5 * scale / (np.abs(residual) + 1e-10))
    return coef


def features(image):
    a = np.asarray(image, dtype=np.float64)
    if a.ndim != 3 or a.shape[2] != 3 or min(a.shape[:2]) < 64:
        raise ValueError("Expected H×W×3 sensor-linear proxy, at least64×64")
    if not np.all(np.isfinite(a)):
        raise ValueError("Image contains non-finite values")
    log = np.log(np.maximum(a, 0.001))
    sm = gaussian_filter(log, (1, 1, 0))
    d = (sm[4:] - sm[:-4]) / 4
    dx = np.abs(np.gradient(sm, axis=1))[2:-2]
    ddy = np.abs(np.gradient(np.gradient(sm, axis=0), axis=0))[2:-2]
    weight = (a[2:-2] > 0.008) * (a[2:-2] < 0.85) / (1 + (dx / 0.025) ** 4 + (ddy / 0.006) ** 4)
    return d, weight


def row_summary(d, weight, columns=None):
    if columns is not None:
        d = d[:, columns]
        weight = weight[:, columns]
    med = np.median(d, axis=1)
    rw = weight * np.minimum(1, 0.02 / (np.abs(d - med[:, None]) + 1e-10))
    summary = np.sum(d * rw, axis=1) / np.maximum(rw.sum(axis=1), 1e-10)
    confidence = np.mean(weight, axis=1)
    return summary, confidence


def wave_design(height, q):
    y = (np.arange(height) + 0.5) / height
    wave = np.column_stack([np.cos(2 * np.pi * q * y), np.sin(2 * np.pi * q * y)])
    dw = (wave[4:] - wave[:-4]) / 4
    yy = y[2:-2]
    design = np.column_stack([dw, np.ones(height - 4), yy - 0.5, (yy - 0.5) ** 2])
    return y, wave, design


def global_coeff(summary, confidence, q):
    _, _, A = wave_design(len(summary) + 4, q)
    return np.array([robust_ls(A, summary[:, c], confidence[:, c])[:2] for c in range(3)])


def _asymmetric_phase_rescue(d, weight, profile):
    """Corroborate one illuminated side when the other half has no useful phase.

    Called only after the ordinary gate rejects *solely* for half-phase
    disagreement. The pooled amplitude/coverage guards and final residual veto
    remain in force. Spectral ratios are empirical evidence, not calibrated
    probabilities or a guarantee that periodic scene detail is illumination.
    """
    width = d.shape[1]
    q = profile.cycles_per_sensor_height
    response = np.asarray(profile.rgb_relative_modulation)
    regions = []
    columns = [slice(0, width // 2), slice(width // 2, None)]
    columns += [slice(width * i // 7, width * (i + 1) // 7) for i in range(7)]
    for column in columns:
        summary, confidence = row_summary(d, weight, column)
        coefficients = global_coeff(summary, confidence, q)
        latent = np.sum(coefficients * response[:, None], axis=0) / np.sum(response**2)
        amplitude = float(np.linalg.norm(latent))
        controls = []
        for offset in (-2.0, -1.1, 1.1, 2.0):
            coef = global_coeff(summary, confidence, q + offset)
            control = np.sum(coef * response[:, None], axis=0) / np.sum(response**2)
            controls.append(float(np.linalg.norm(control)))
        noise = float(np.median(controls))
        rgb_coherence = np.sum(coefficients * latent[None], axis=1) / np.maximum(
            np.linalg.norm(coefficients, axis=1) * amplitude, 1e-10
        )
        reliable = (
            amplitude >= profile.min_green_amplitude
            and amplitude / max(noise, 0.001) >= 2.0
            and float(np.min(rgb_coherence)) >= 0.9
            and float(np.mean(weight[:, column])) >= 0.08
        )
        regions.append(
            {
                "reliable": bool(reliable),
                "amplitude": amplitude,
                "noise": noise,
                "phase": float(np.arctan2(latent[1], latent[0])),
            }
        )
    half_reliable = [region["reliable"] for region in regions[:2]]
    supported = [region for region in regions[2:] if region["reliable"]]
    result = {
        "rescued": False,
        "half_phase_reliable": half_reliable,
        "reliable_strip_count": len(supported),
        "phase_support_strips": 0,
        "supported_signal_weight": 0.0,
        "opposed_signal_weight": 0.0,
    }
    # Both strong halves have already failed the original green-phase check.
    # A dominant side must never erase a contradictory, well-supported side.
    if all(half_reliable) or len(supported) < 3:
        return result
    angles = np.array([region["phase"] for region in supported])
    weights = np.array(
        [max(region["amplitude"] ** 2 - region["noise"] ** 2, 0) for region in supported]
    )
    coherence = np.cos(angles[:, None] - angles[None, :])
    anchor = int(np.argmax(np.sum(weights[None, :] * np.clip(coherence, 0, 1) ** 4, axis=1)))
    aligned = coherence[anchor] >= 0.85
    total = max(float(weights.sum()), 1e-12)
    support = int(aligned.sum())
    fraction = float(weights[aligned].sum() / total)
    opposed = float(weights[coherence[anchor] < 0.7].sum() / total)
    phase = float(np.angle(np.sum(weights[aligned] * np.exp(1j * angles[aligned]))))
    result.update(
        phase_support_strips=support,
        supported_signal_weight=fraction,
        opposed_signal_weight=opposed,
        capture_phase_radians=phase,
        rescued=support >= 3 and fraction >= 0.70 and opposed <= 0.25,
    )
    return result


def calibrate(images, metadata, *, known_frequency_hz=None):
    """Pool unrelated, unaligned images; a common wavelength and RGB response.

    Exposure-conditioned amplitudes are empirical priors, not a claim to have
    uniquely recovered the lamp waveform. With a measured lamp frequency,
    row timing follows from spatial periodicity; otherwise both remain null.
    """
    if len(images) < 3:
        raise ValueError("Calibration requires at least3 images")
    shapes = {
        (m["camera"], m["sizes"]["height"], m["sizes"]["width"], m.get("shutter_mode", "unknown"))
        for m in metadata
    }
    if len(shapes) != 1:
        raise ValueError("Calibrate each camera, sensor geometry and shutter mode separately")
    summaries = []
    for a in images:
        d, w = features(a)
        summaries.append(row_summary(d, w))
    h = images[0].shape[0]
    spectra = []
    nfft = 8192
    freq = np.fft.rfftfreq(nfft) * h
    for s, w in summaries:
        v = s[:, 1] - gaussian_filter1d(s[:, 1], 30)
        spectra.append(np.abs(np.fft.rfft(v * np.hanning(len(v)), n=nfft)) ** 2)
    pooled = np.median(spectra, axis=0)
    mask = (freq >= 2.5) & (freq <= 30)
    coarse = freq[np.flatnonzero(mask)[np.argmax(pooled[mask])]]

    # The pool, rather than every frame's noisy maximum, chooses one frequency.
    def objective(q):
        _, _, A = wave_design(h, q)
        total = 0.0
        for s, w in summaries:
            c = robust_ls(A, s[:, 1], w[:, 1], iterations=2)
            residual = s[:, 1] - A @ c
            total += np.sum(w[:, 1] * np.minimum(residual**2, 0.008**2))
        return total

    opt = minimize_scalar(
        objective,
        bounds=(max(2.5, coarse - 0.35), coarse + 0.35),
        method="bounded",
        options={"xatol": 0.001},
    )
    q = float(opt.x)
    coeff = np.array([global_coeff(s, w, q) for s, w in summaries])
    amplitude = np.linalg.norm(coeff, axis=-1)
    good = amplitude[:, 1] > 0.025
    if good.sum() < 3:
        raise ValueError("Too little coherent flicker for reusable calibration")
    ratios = np.median(amplitude[good] / amplitude[good, 1, None], axis=0)
    # Cross-channel phase agreement is required by this single-light model.
    cross = np.sum(coeff[good] * coeff[good, 1, None, :], axis=-1) / np.maximum(
        amplitude[good] * amplitude[good, 1, None], 1e-10
    )
    if np.min(np.median(cross[:, [0, 2]], axis=0)) < 0.9:
        raise ValueError("RGB phases disagree: use separate lighting profiles / richer model")
    exposures = {}
    for m, amp in zip(metadata, amplitude):
        key = f"{m['shutter_s']:.7f}"
        exposures.setdefault(key, []).append(float(amp[1]))
    exposures = {k: float(np.median(v)) for k, v in exposures.items()}
    camera, height, width, mode = next(iter(shapes))
    profile = LightingProfile(
        1, camera, height, width, mode, q, ratios.tolist(), exposures, [m["file"] for m in metadata]
    )
    if known_frequency_hz is not None:
        if known_frequency_hz <= 0:
            raise ValueError("Measured frequency must be positive")
        profile.temporal_frequency_hz = float(known_frequency_hz)
        profile.row_time_seconds = q / (known_frequency_hz * height)
        profile.frequency_status = "lamp frequency supplied by user; effective row timing derived"
    diagnostics = {
        "pooled_coarse_cycles": float(coarse),
        "calibrated_cycles": q,
        "individual_rgb_amplitudes": amplitude.tolist(),
        "rgb_phase_agreement_median": float(np.median(cross)),
        "exposure_median_green_amplitudes": exposures,
        "temporal_identification": "Not identifiable from row periodicity with unconstrained per-frame mixing/strength. No frequency in Hz assumed.",
    }
    return profile, diagnostics


def estimate_gain(image, profile, shutter_s, *, spatial=True):
    start = time.perf_counter()
    a = np.asarray(image)
    h, w, _ = a.shape
    if shutter_s <= 0:
        raise ValueError("Exposure duration must be positive")
    d, weight = features(a)
    summary, confidence = row_summary(d, weight)
    y, wave, A = wave_design(h, profile.cycles_per_sensor_height)
    coefficients = global_coeff(summary, confidence, profile.cycles_per_sensor_height)
    response = np.asarray(profile.rgb_relative_modulation)
    # Common phase/strength and fixed RGB response learned across the session.
    latent = np.sum(coefficients * response[:, None], axis=0) / np.sum(response**2)
    amplitude = float(np.linalg.norm(latent))
    phase = float(np.arctan2(latent[1], latent[0]))
    left = global_coeff(
        *row_summary(d, weight, np.arange(w) < w // 2), profile.cycles_per_sensor_height
    )
    right = global_coeff(
        *row_summary(d, weight, np.arange(w) >= w // 2), profile.cycles_per_sensor_height
    )
    ph_coherence = float(
        np.dot(left[1], right[1]) / max(np.linalg.norm(left[1]) * np.linalg.norm(right[1]), 1e-10)
    )
    exposure_key = min(profile.exposure_amplitudes, key=lambda v: abs(float(v) - shutter_s))
    exposure_seen = abs(float(exposure_key) - shutter_s) < max(1e-7, shutter_s * 0.001)
    reasons = []
    if amplitude < profile.min_green_amplitude:
        reasons.append("weak_detected_banding")
    if ph_coherence < 0.70:
        reasons.append("left_right_phase_disagreement")
    if np.mean(weight) < 0.1:
        reasons.append("insufficient_smooth_unsaturated_signal")
    phase_rescue = None
    if reasons == ["left_right_phase_disagreement"]:
        phase_rescue = _asymmetric_phase_rescue(d, weight, profile)
        if phase_rescue["rescued"]:
            phase = phase_rescue["capture_phase_radians"]
            latent = amplitude * np.array([np.cos(phase), np.sin(phase)])
            reasons = []
    status = "corrected" if not reasons else "unchanged_low_confidence"
    signal = wave @ latent
    ny, nx = profile.mixing_grid
    mix = np.ones((h, w), dtype=np.float64)
    mix_coeff = np.ones((ny, nx))
    if status == "corrected" and spatial:
        cy = np.linspace(0, 1, ny)
        cx = np.linspace(0, 1, nx)
        yy = y[2:-2]
        ds = (signal[4:] - signal[:-4]) / 4
        local_A = np.column_stack([ds, np.ones(h - 4), yy - 0.5])
        med = np.median(d, axis=1)
        robust_weight = weight * np.minimum(1, 0.015 / (np.abs(d - med[:, None]) + 1e-10))
        # Pool all RGB after response normalization. One slowly varying mix map.
        for ix, xc in enumerate(cx):
            wx = np.exp(-0.5 * ((np.arange(w) / (w - 1) - xc) / 0.14) ** 2)
            ww = robust_weight * wx[None, :, None] * response[None, None, :] ** 2
            dd = np.sum(d / response[None, None, :] * ww, axis=(1, 2)) / np.maximum(
                np.sum(ww, axis=(1, 2)), 1e-10
            )
            evidence = np.mean(ww, axis=(1, 2))
            for iy, yc in enumerate(cy):
                wy = np.exp(-0.5 * ((yy - yc) / 0.16) ** 2)
                c = robust_ls(local_A, dd, wy * evidence)
                mix_coeff[iy, ix] = np.clip(c[0], 0, 2.2)
        mix_coeff = gaussian_filter(mix_coeff, 0.5)
        xx, yy_grid = np.meshgrid((np.arange(w) + 0.5) / w, y)
        mix = RegularGridInterpolator((cy, cx), mix_coeff)(np.stack([yy_grid, xx], axis=-1))
    log_gain = -signal[:, None, None] * mix[:, :, None] * response[None, None, :]
    bound_hit = float(np.mean(np.abs(log_gain) > profile.max_log_gain))
    log_gain = np.clip(log_gain, -profile.max_log_gain, profile.max_log_gain)
    if status != "corrected":
        log_gain = np.zeros((h, w, 3), dtype=np.float64)
    gains = np.exp(log_gain).astype(np.float32)
    before = banding_score(a, profile.cycles_per_sensor_height)
    after = banding_score(a * gains, profile.cycles_per_sensor_height)
    # A veto only: reducing this score alone is not proof of successful recovery.
    if (
        status == "corrected"
        and after["luminance"] > before["luminance"] * 1.05
        and after["luminance"] > 0.005
    ):
        status = "unchanged_residual_check"
        reasons.append("independent_row_score_increased")
        gains[:] = 1
        after = before.copy()
    report = {
        "status": status,
        "reasons": reasons,
        "capture_phase_radians": phase,
        "green_log_amplitude": amplitude,
        "left_right_phase_coherence": ph_coherence,
        "shutter_s": shutter_s,
        "calibrated_exposure": exposure_seen,
        "exposure_amplitude_prior": profile.exposure_amplitudes[exposure_key]
        if exposure_seen
        else None,
        "temporal_model_used": False,
        "mixing_coefficients": mix_coeff.tolist(),
        "frame_latent_count": 2 + ny * nx if spatial else 2,
        "gain_min": float(gains.min()),
        "gain_max": float(gains.max()),
        "log_gain_bound_fraction": bound_hit,
        "before": before,
        "after": after,
        "fit_seconds": time.perf_counter() - start,
    }
    if phase_rescue is not None:
        report["phase_rescue"] = phase_rescue
    if not exposure_seen:
        report["review_reason"] = (
            "Exposure outside empirical calibration; inspect before batch deployment"
        )
    return gains, report


def banding_score(image, q, *, columns=None):
    """Diagnostic expected-frequency amplitude, NOT a ground-truth quality score.

    Uses independent unweighted median derivatives and separate RGB/chroma.
    Evaluation should also include synthetic known-gain recovery and visual checks.
    """
    a = image if columns is None else image[:, columns]
    log = np.log(np.maximum(a, 0.002))
    row = np.median(np.diff(gaussian_filter1d(log, 1, axis=0), axis=0), axis=1)
    h = image.shape[0]
    y = (np.arange(h - 1) + 1) / h
    D = np.column_stack(
        [np.cos(2 * np.pi * q * y), np.sin(2 * np.pi * q * y), np.ones(h - 1), y - 0.5]
    )
    coef = np.array([robust_ls(D, row[:, c], iterations=3)[:2] for c in range(3)])
    scale = 2 * np.sin(np.pi * q / h)
    rgb = np.linalg.norm(coef, axis=1) / scale
    luminance = np.linalg.norm(np.array([0.2126, 0.7152, 0.0722]) @ coef) / scale
    chroma = np.sqrt(np.sum((coef[[0, 2]] - coef[1]) ** 2) / 2) / scale
    return {"luminance": float(luminance), "chroma": float(chroma), "rgb": rgb.tolist()}
