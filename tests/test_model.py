"""Offline inverse-correction checks; no personal photographs are needed."""

from dataclasses import replace
import numpy as np
import pytest
from flicker_removal.model import LightingProfile, calibrate, estimate_gain


def profile(q=8.14):
    return LightingProfile(
        1,
        "Synthetic",
        2400,
        3200,
        "synthetic",
        q,
        [1.17, 1.0, 0.66],
        {"0.0050000": 0.08, "0.0062500": 0.05},
        [],
        min_green_amplitude=0.025,
    )


def scene(height=240, width=320, edges=False):
    y = (np.arange(height) + 0.5)[:, None] / height
    x = (np.arange(width) + 0.5)[None, :] / width
    base = 0.12 * np.exp(
        0.18 * x + 0.11 * y - 0.22 * np.exp(-((x - 0.55) ** 2 + (y - 0.6) ** 2) / 0.09)
    )
    rgb = base[:, :, None] * np.array([0.8, 1.0, 0.7])[None, None, :]
    if edges:
        shape = (x > 0.35) & (x < 0.64) & (y > 0.28) & (y < 0.71)
        rgb = rgb * np.where(shape[:, :, None], 0.6, 1.0)
        rgb[:, width // 5 : width // 5 + 2] *= 1.25
    return rgb


def physical_signal(a, p, phase=0.8, amplitude=0.11, mixed=True):
    h, w, _ = a.shape
    y = (np.arange(h) + 0.5)[:, None] / h
    x = (np.arange(w) + 0.5)[None, :] / w
    mixture = (0.25 + 0.75 * x) * (0.8 + 0.2 * y) if mixed else np.ones((h, w))
    return (
        1.0
        + amplitude
        * np.cos(2 * np.pi * p.cycles_per_sensor_height * y + phase)[:, :, None]
        * mixture[:, :, None]
        * np.array(p.rgb_relative_modulation)[None, None, :]
    )


@pytest.mark.parametrize("phase", [0.2, 1.7, 4.1])
def test_physical_mixed_illumination_recovery(phase):
    p = profile()
    clean = scene(edges=True)
    illumination = physical_signal(clean, p, phase=phase)
    observed = clean * illumination
    gain, report = estimate_gain(observed, p, 0.005)
    before = np.sqrt(np.mean(np.log(observed / clean) ** 2))
    after = np.sqrt(np.mean(np.log(observed * gain / clean) ** 2))
    assert report["status"] == "corrected", report
    assert after < before * 0.4, (before, after)
    assert np.isfinite(gain).all() and (gain > 0).all()
    assert gain.min() >= np.exp(-p.max_log_gain) - 1e-6
    assert gain.max() <= np.exp(p.max_log_gain) + 1e-6
    # A gain field must stay smooth across edges, preserving their positions.
    assert np.max(np.abs(np.diff(np.log(gain), axis=1))) < 0.008


def test_unmodulated_scene_and_real_edges_remain_exactly_unchanged():
    clean = scene(edges=True)
    gain, report = estimate_gain(clean, profile(), 0.005)
    assert report["status"] == "unchanged_low_confidence"
    assert "weak_detected_banding" in report["reasons"]
    assert np.array_equal(gain, np.ones_like(gain))
    assert np.array_equal(clean * gain, clean)


def test_weak_modulation_is_conservatively_unchanged():
    p = profile()
    clean = scene(edges=True)
    gain, report = estimate_gain(clean * physical_signal(clean, p, amplitude=0.012), p, 0.005)
    assert report["status"] == "unchanged_low_confidence"
    assert np.array_equal(gain, np.ones_like(gain))


def test_phase_disagreement_abstains_instead_of_applying_one_wrong_waveform():
    p = profile()
    clean = scene()
    signal = physical_signal(clean, p, amplitude=0.12, mixed=False)
    signal[:, signal.shape[1] // 2 :] = 2 - signal[:, signal.shape[1] // 2 :]
    gain, report = estimate_gain(clean * signal, p, 0.005)
    assert "left_right_phase_disagreement" in report["reasons"]
    assert np.array_equal(gain, np.ones_like(gain))


def test_weak_half_phase_does_not_veto_three_coherent_illuminated_strips():
    p = profile()
    clean = scene(edges=True)
    h, w, _ = clean.shape
    y = (np.arange(h) + 0.5)[:, None, None] / h
    x = (np.arange(w) + 0.5)[None, :, None] / w
    theta = 2 * np.pi * p.cycles_per_sensor_height * y
    # Weak genuine scene shading on the unilluminated side biases its fitted
    # phase. It is part of the clean reference and must not veto the bright side.
    clean = clean * (1 + 0.019 * np.cos(theta + 2.0) * (x < 0.5))
    light = (
        1
        + 0.12
        * np.cos(theta + 0.3)
        * np.where(x < 0.5, 0.1, 1.0)
        * np.array(p.rgb_relative_modulation)[None, None, :]
    )
    gain, report = estimate_gain(clean * light, p, 0.005)
    assert report["left_right_phase_coherence"] < 0.7
    assert report["phase_rescue"]["half_phase_reliable"] == [False, True]
    assert report["phase_rescue"]["phase_support_strips"] >= 3
    assert report["status"] == "corrected"
    before = np.sqrt(np.mean(np.log(light) ** 2))
    after = np.sqrt(np.mean(np.log(light * gain) ** 2))
    assert after < before * 0.4


def test_two_lit_halves_with_opposite_phases_remain_unchanged_despite_strong_mean():
    p = profile()
    clean = scene()
    h, w, _ = clean.shape
    y = (np.arange(h) + 0.5)[:, None, None] / h
    x = (np.arange(w) + 0.5)[None, :, None] / w
    light = (
        1
        + np.cos(2 * np.pi * p.cycles_per_sensor_height * y + 0.49)
        * np.where(x < 0.5, -0.05, 0.16)
        * np.array(p.rgb_relative_modulation)[None, None, :]
    )
    gain, report = estimate_gain(clean * light, p, 0.005)
    assert report["green_log_amplitude"] > p.min_green_amplitude
    assert report["phase_rescue"]["half_phase_reliable"] == [True, True]
    assert report["phase_rescue"]["rescued"] is False
    assert "left_right_phase_disagreement" in report["reasons"]
    assert np.array_equal(gain, np.ones_like(gain))


def test_localized_periodic_scene_detail_does_not_trigger_strip_rescue():
    p = profile()
    clean = scene(edges=True)
    h, w, _ = clean.shape
    y = (np.arange(h) + 0.5)[:, None, None] / h
    x = (np.arange(w) + 0.5)[None, :, None] / w
    # A real patterned surface happens to share the calibrated wavelength.
    clean = clean * (
        1
        + 0.07
        * np.cos(2 * np.pi * p.cycles_per_sensor_height * y + 0.37)
        * ((x > 0.40) & (x < 0.68))
    )
    gain, report = estimate_gain(clean, p, 0.005)
    assert "weak_detected_banding" in report["reasons"]
    assert "phase_rescue" not in report
    assert np.array_equal(gain, np.ones_like(gain))
    assert np.array_equal(clean * gain, clean)


def test_shared_calibration_recovers_spatial_frequency_and_keeps_hz_unknown():
    p = profile(q=8.21)
    a = scene()
    phases = [0.2, 1.4, 2.9, 4.5]
    images = [a * physical_signal(a, p, phase=phase, mixed=False) for phase in phases]
    metadata = [
        dict(
            camera="Synthetic",
            sizes={"height": 2400, "width": 3200},
            shutter_mode="synthetic",
            shutter_s=0.005,
            file=f"synthetic-{i}",
        )
        for i in range(len(images))
    ]
    learned, diagnostic = calibrate(images, metadata)
    assert abs(learned.cycles_per_sensor_height - p.cycles_per_sensor_height) < 0.08
    assert np.allclose(learned.rgb_relative_modulation, p.rgb_relative_modulation, atol=0.04)
    assert learned.temporal_frequency_hz is None and learned.row_time_seconds is None
    assert diagnostic["rgb_phase_agreement_median"] > 0.98


def test_novel_exposure_is_explicitly_flagged():
    p = profile()
    a = scene()
    gain, report = estimate_gain(a * physical_signal(a, p), p, 0.004)
    assert report["calibrated_exposure"] is False
    assert report["exposure_amplitude_prior"] is None
    assert "review_reason" in report


def test_shared_calibration_rejects_one_opposite_rgb_channel():
    p = profile()
    a = scene()
    images = []
    for phase in [0.2, 1.4, 2.9, 4.5]:
        light = physical_signal(a, p, phase=phase, mixed=False)
        light[:, :, 2] = 2 - light[:, :, 2]
        images.append(a * light)
    metadata = [
        dict(
            camera="Synthetic",
            sizes={"height": 2400, "width": 3200},
            shutter_mode="synthetic",
            shutter_s=0.005,
            file=f"synthetic-{i}",
        )
        for i in range(4)
    ]
    with pytest.raises(ValueError, match="RGB phases disagree"):
        calibrate(images, metadata)


@pytest.mark.parametrize("value", [np.nan, np.inf])
def test_nonfinite_input_is_rejected(value):
    a = scene()
    a[0, 0, 0] = value
    with pytest.raises(ValueError, match="non-finite"):
        estimate_gain(a, profile(), 0.005)
