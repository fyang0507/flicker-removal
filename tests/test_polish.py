"""Known-illumination and fallback tests for optional scene-assisted polishing.

These use synthetic sensor-linear images, never the user's photographs. The
illumination injection is physical intensity modulation, not the fitted log
waveform, so recovery checks also include a small model-family mismatch.
"""

from dataclasses import replace
import numpy as np
import pytest

pytest.importorskip("cv2")

from flicker_removal.model import LightingProfile
from flicker_removal.session import SceneReference, image_digest, profile_digest, refine_gain


def profile():
    return LightingProfile(
        1, "Synthetic", 2400, 3200, "synthetic", 8.14, [1.17, 1.0, 0.66], {"0.0050000": 0.08}, []
    )


def scene(height=240, width=320):
    h, w = height, width
    y = (np.arange(h) + 0.5)[:, None] / h
    x = (np.arange(w) + 0.5)[None, :] / w
    base = 0.16 * np.exp(
        0.18 * x + 0.12 * y - 0.25 * np.exp(-((x - 0.6) ** 2 + (y - 0.55) ** 2) / 0.08)
    )
    image = base[:, :, None] * np.array([0.8, 1.0, 0.7])
    obj = (x > 0.31 + 0.08 * y) & (x < 0.65) & (y > 0.28) & (y < 0.73)
    image *= np.where(obj[:, :, None], 0.62, 1.0)
    texture = 0.03 * np.sin(2 * np.pi * 43 * x) * np.sin(2 * np.pi * 31 * y)
    image *= (1 + texture * ((x > 0.65) & (y > 0.55)))[:, :, None]
    # Genuine scene stripes are present in the template and must survive.
    image *= (1 + 0.025 * np.cos(2 * np.pi * 8.14 * y) * ((x > 0.12) & (x < 0.27)))[:, :, None]
    return image


def light(image, phase=0.73, amplitude=0.045):
    h, w = image.shape[:2]
    y = (np.arange(h) + 0.5)[:, None] / h
    x = (np.arange(w) + 0.5)[None, :] / w
    modulation = amplitude * (0.55 + 0.35 * x + 0.10 * y) * np.cos(2 * np.pi * 8.14 * y + phase)
    return 1 + modulation[:, :, None] * np.array([1.17, 1.0, 0.66])


def template(image):
    return np.log(image / 0.005)


def known_reference(clean, observed, p):
    # The observed target is deliberately in the archive. polish must remove
    # it before estimating the clean scene template from six other views.
    stack = np.stack([template(clean)] * 6 + [template(observed)]).astype(np.float32)
    records = [
        {
            "file": f"synthetic-{i}",
            "registered": True,
            "stack_index": i,
            "image_digest": f"other-{i}",
            "registration": {"synthetic": True},
        }
        for i in range(6)
    ]
    records.append(
        {
            "file": "target",
            "registered": True,
            "stack_index": 6,
            "image_digest": image_digest(observed),
            "registration": {"synthetic": True},
        }
    )
    metadata = {"schema_version": 1, "profile_sha256": profile_digest(p), "records": records}
    return SceneReference(stack, clean.astype(np.float32), np.stack([np.eye(3)] * 7), metadata)


@pytest.mark.parametrize("phase", [0.31, 2.0, 4.7])
def test_physical_residual_recovery_preserves_edges_and_texture(phase):
    clean = scene()
    illumination = light(clean, phase)
    observed = clean * illumination
    gain, report = refine_gain(
        observed, np.eye(3), template(clean), np.ones(clean.shape[:2], bool), 8.14
    )
    error = np.log(illumination * gain)
    original = np.log(illumination)
    assert report["status"] == "polished", report
    assert np.sqrt(np.mean(error**2)) < np.sqrt(np.mean(original**2)) * 0.10
    assert np.quantile(abs(error), 0.99) < 0.003
    assert np.max(abs(np.log(gain.astype(float)))) <= 0.0800001
    # The error at actual object/texture boundaries must remain small too.
    edge = np.hypot(*np.gradient(np.log(clean[:, :, 1])))
    edge_pixels = edge > np.quantile(edge, 0.9)
    assert np.sqrt(np.mean(error[edge_pixels] ** 2)) < 0.0015
    assert min(f["heldout_error_reduction"] for f in report["folds"]) > 0.8


@pytest.mark.parametrize("nuisance_shading", [False, True])
def test_clean_scene_real_stripes_and_shading_are_not_removed(nuisance_shading):
    clean = scene()
    observed = clean.copy()
    if nuisance_shading:
        h, w = clean.shape[:2]
        y = (np.arange(h) + 0.5)[:, None] / h
        x = (np.arange(w) + 0.5)[None, :] / w
        shading = np.exp(0.08 * (x - 0.5) + 0.04 * (y - 0.5) + 0.03 * (x - 0.5) * (y - 0.5))
        observed *= shading[:, :, None] * np.array([1.05, 0.97, 1.02])
    gain, report = refine_gain(
        observed, np.eye(3), template(clean), np.ones(clean.shape[:2], bool), 8.14
    )
    assert report["status"] == "unchanged", report
    assert np.array_equal(gain, np.ones_like(gain))


def test_residual_requiring_large_clipped_gain_is_rejected():
    clean = scene()
    observed = clean * light(clean, amplitude=0.19)
    gain, report = refine_gain(
        observed, np.eye(3), template(clean), np.ones(clean.shape[:2], bool), 8.14
    )
    assert report["status"] == "unchanged", report
    assert report["reason"] == "residual_exceeds_safe_gain_bound"
    assert report["bound_fraction"] > 0.01
    assert np.array_equal(gain, np.ones_like(gain))


@pytest.mark.parametrize("shift", [False, True])
def test_insufficient_static_support_abstains(shift):
    clean = scene()
    mask = np.zeros(clean.shape[:2], bool)
    mask[20:45, 20:100] = True
    H = np.eye(3)
    if shift:
        mask[:] = True
        H[0, 2] = clean.shape[1] * 2
    gain, report = refine_gain(clean * light(clean), H, template(clean), mask, 8.14)
    assert report["reason"] == "insufficient_static_support"
    assert np.array_equal(gain, np.ones_like(gain))


def test_short_vertical_patch_cannot_turn_cubic_shading_into_full_height_bands():
    clean = scene(586, 878)
    h, w = clean.shape[:2]
    y = (np.arange(h) + 0.5)[:, None] / h
    # Nonharmonic shading has enough local curvature to resemble a fraction
    # of a wave within a short support patch. Extrapolating it would invent
    # bands throughout the rest of the image despite passing an X-only split.
    shading = np.exp(0.18 * ((y - 0.06) / 0.06) ** 3)
    shading = np.where(y < 0.12, shading, 1.0)
    observed = clean * shading[:, :, None]
    mask = np.broadcast_to(y < 0.12, (h, w)).copy()
    gain, report = refine_gain(observed, np.eye(3), template(clean), mask, 8.14)
    assert report["status"] == "unchanged", report
    assert report["samples"] > 8000
    assert report["stable_fraction"] > 0.05
    assert report["reason"] == "insufficient_row_or_phase_coverage"
    assert np.array_equal(gain, np.ones_like(gain))


def test_weak_baseline_is_frozen_before_scene_registration(monkeypatch):
    import flicker_removal.session as session

    clean = scene()
    p = profile()
    ref = known_reference(clean, clean, p)

    def unexpected(*args, **kwargs):
        raise AssertionError("A weak frame should not start scene fitting")

    monkeypatch.setattr(session, "register", unexpected)
    monkeypatch.setattr(session, "template_from_stack", unexpected)
    original_gain = np.ones_like(clean, dtype=np.float32)
    gain, report = ref.polish(
        clean,
        original_gain,
        {"status": "unchanged_low_confidence", "reasons": ["weak_detected_banding"]},
        p,
    )
    assert report["reason"] == "baseline_confidence_gate"
    assert np.array_equal(gain, original_gain)


def test_unrelated_featureless_frame_falls_back_to_baseline():
    clean = scene()
    p = profile()
    ref = known_reference(clean, clean, p)
    unrelated = np.full_like(clean, 0.13)
    baseline_gain = np.full_like(clean, 1.01, dtype=np.float32)
    gain, report = ref.polish(unrelated, baseline_gain, {"status": "corrected", "reasons": []}, p)
    assert report["status"] == "unchanged"
    assert "registration" in report["reason"].lower()
    assert np.array_equal(gain, baseline_gain)


def test_target_is_excluded_and_strong_evidence_can_override_row_score_veto():
    clean = scene()
    observed = clean * light(clean)
    p = profile()
    ref = known_reference(clean, observed, p)
    gain, report = ref.polish(
        observed,
        np.ones_like(observed, dtype=np.float32),
        {"status": "unchanged_residual_check", "reasons": ["independent_row_score_increased"]},
        p,
    )
    assert report["status"] == "polished", report
    assert report["baseline_residual_veto_override"] is True
    assert report["target_excluded"] is True and report["excluded_references"] == 1
    error = np.log(observed * gain / clean)
    assert np.sqrt(np.mean(error**2)) < 0.0015


def test_reference_archive_rejects_different_lighting_profile(tmp_path):
    clean = scene()
    p = profile()
    ref = known_reference(clean, clean, p)
    path = tmp_path / "reference.npz"
    ref.save(path)
    restored = SceneReference.load(path, p)
    assert np.array_equal(restored.stack, ref.stack)
    with pytest.raises(ValueError, match="does not match"):
        SceneReference.load(path, replace(p, cycles_per_sensor_height=8.10))
