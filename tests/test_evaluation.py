"""Public evaluation behavior, with an optional real RAW integrity regression."""

from copy import deepcopy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageCms
import pytest
import tifffile

from flicker_removal import cli, dng, evaluation
from flicker_removal.io import source_hash


def _write_run(root, sources, *, score=0.02):
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "preview_white_balance": [2, 1, 2, 1],
        "preview_exposure_multiplier": 1,
        "reports": [
            {
                "file": path.name,
                "status": "corrected",
                "before": {"luminance": 0.2, "chroma": 0.08},
                "after": {"luminance": score, "chroma": score / 2},
                "dng": {"source_sha256": source_hash(path)},
                # Relocated source folders must work without following this path.
                "source_metadata": {"file": "/unavailable/old-host/source.ARW"},
            }
            for path in sources
        ],
    }
    (root / "batch-report.json").write_text(json.dumps(summary))
    return summary


def _edit_run(root, edit):
    path = root / "batch-report.json"
    summary = json.loads(path.read_text())
    edit(summary)
    path.write_text(json.dumps(summary))


@pytest.fixture
def runs(tmp_path):
    sources = tmp_path / "relocated-sources"
    sources.mkdir()
    source = sources / "frame.ARW"
    source.write_bytes(b"fixture source bytes")
    candidate, baseline = tmp_path / "candidate", tmp_path / "baseline"
    _write_run(candidate, [source], score=0.02)
    _write_run(baseline, [source], score=0.03)
    return SimpleNamespace(sources=sources, source=source, candidate=candidate, baseline=baseline)


def test_comparison_verifies_both_deliveries_and_uses_relocated_sources(runs, monkeypatch):
    visited = []

    def verify(source, root, row):
        visited.append((source, root))
        return {"bayer_matches_source_and_saved_gains": True}

    monkeypatch.setattr(evaluation, "_verify_file", verify)
    report = evaluation.evaluate_run(runs.candidate, runs.sources, compare=runs.baseline)
    assert visited == [(runs.source, runs.candidate), (runs.source, runs.baseline)]
    assert report["integrity_passed"] is True
    assert report["files"][0]["baseline_validation"]["bayer_matches_source_and_saved_gains"]
    comparison = report["comparison"]
    assert comparison["matched_source_hashes_and_render_settings"] is True
    assert comparison["files"][0]["luminance_row_score_delta"] == pytest.approx(-0.01)
    assert "not proof" in comparison["interpretation"]


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: s["reports"][0].update(file="other.ARW"), "same source filenames"),
        (lambda s: s["reports"][0]["dng"].update(source_sha256="0" * 64), "source hashes differ"),
        (lambda s: s.update(preview_white_balance=[2.1, 1, 2, 1]), "preview settings differ"),
        (lambda s: s.update(preview_white_balance=[2, 1, 2]), "preview settings differ"),
        (lambda s: s.update(preview_exposure_multiplier=2), "preview settings differ"),
    ],
)
def test_comparison_requires_matching_photos_hashes_and_render_settings(runs, change, message):
    _edit_run(runs.baseline, change)
    with pytest.raises(ValueError, match=message):
        evaluation.evaluate_run(runs.candidate, runs.sources, compare=runs.baseline)


@pytest.mark.parametrize("extra", [False, True])
def test_source_folder_must_match_complete_batch(runs, extra):
    if extra:
        (runs.sources / "extra.ARW").write_bytes(b"extra")
    else:
        runs.source.rename(runs.sources / "different.ARW")
    with pytest.raises(ValueError, match="photo set exactly"):
        evaluation.evaluate_run(runs.candidate, runs.sources)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda s: s.update(reports=[]), "nonempty schema-1"),
        (lambda s: s.update(schema_version=2), "nonempty schema-1"),
        (lambda s: s["reports"][0].update(file="../outside.ARW"), "plain source filenames"),
        (lambda s: s["reports"].append(deepcopy(s["reports"][0])), "Duplicate source"),
        (lambda s: s["reports"][0].pop("status"), "processing status"),
        (lambda s: s["reports"][0]["after"].pop("chroma"), "finite number"),
        (lambda s: s["reports"][0]["after"].update(luminance=float("nan")), "nonfinite"),
        (lambda s: s["reports"][0]["after"].update(rgb=[1, float("inf"), 1]), "nonfinite"),
        (lambda s: s["reports"][0]["dng"].update(source_sha256="unknown"), "Invalid source SHA256"),
    ],
)
def test_malformed_reports_fail_before_audit(runs, change, message):
    _edit_run(runs.candidate, change)
    with pytest.raises(ValueError, match=message):
        evaluation.evaluate_run(runs.candidate, runs.sources)


def test_changed_source_is_reported_as_integrity_failure(runs):
    runs.source.write_bytes(b"changed since processing")
    report = evaluation.evaluate_run(runs.candidate, runs.sources)
    assert report["integrity_passed"] is False
    assert report["files"][0]["integrity"] == "failed"
    assert "Source hash differs" in report["files"][0]["error"]


def test_baseline_failure_is_not_hidden_by_passing_candidate(runs, monkeypatch):
    def verify(source, root, row):
        if root == runs.baseline:
            raise ValueError("Baseline sensor samples changed")
        return {"bayer_matches_source_and_saved_gains": True}

    monkeypatch.setattr(evaluation, "_verify_file", verify)
    report = evaluation.evaluate_run(runs.candidate, runs.sources, compare=runs.baseline)
    assert report["integrity_passed"] is False
    assert "Baseline sensor samples changed" in report["files"][0]["error"]


def test_cli_success_writes_report_and_returns_zero(runs, monkeypatch, tmp_path):
    monkeypatch.setattr(evaluation, "_verify_file", lambda *a: {"verified": True})
    report = tmp_path / "evaluation.json"
    result = cli.main(
        ["evaluate", str(runs.candidate), "--sources", str(runs.sources), "--report", str(report)]
    )
    assert result == 0
    assert json.loads(report.read_text())["integrity_passed"] is True


def test_cli_failed_asset_returns_nonzero_and_keeps_failure_evidence(runs, tmp_path):
    # These source bytes have the right recorded hash, but no gain map exists.
    # Exercise the actual audit and exception path without needing a RAW decoder.
    report = tmp_path / "failed-evaluation.json"
    result = cli.main(
        ["evaluate", str(runs.candidate), "--sources", str(runs.sources), "--report", str(report)]
    )
    assert result == 1
    saved = json.loads(report.read_text())
    assert saved["integrity_passed"] is False
    assert "frame.npy" in saved["files"][0]["error"]


def test_python_module_propagates_failed_integrity_exit_code(runs, tmp_path):
    report = tmp_path / "module-evaluation.json"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "flicker_removal",
            "evaluate",
            str(runs.candidate),
            "--sources",
            str(runs.sources),
            "--report",
            str(report),
        ],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr
    assert json.loads(report.read_text())["integrity_passed"] is False


def test_cli_invalid_manifest_creates_no_partial_report(runs, tmp_path):
    _edit_run(runs.candidate, lambda s: s["reports"][0]["after"].update(luminance=float("nan")))
    destination = tmp_path / "not-published.json"
    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                "evaluate",
                str(runs.candidate),
                "--sources",
                str(runs.sources),
                "--report",
                str(destination),
            ]
        )
    assert error.value.code == 2
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["existing", "dangling-symlink"])
def test_report_never_overwrites_existing_path(runs, tmp_path, kind):
    destination = tmp_path / "evaluation.json"
    if kind == "existing":
        destination.write_text("keep this report")
    else:
        destination.symlink_to(tmp_path / "missing-target")
    args = SimpleNamespace(
        run=runs.candidate, sources=runs.sources, compare=None, report=destination
    )
    with pytest.raises(FileExistsError):
        evaluation.evaluate_command(args)
    if kind == "existing":
        assert destination.read_text() == "keep this report"
    else:
        assert destination.is_symlink()
        assert not (tmp_path / "missing-target").exists()


def test_report_publication_race_preserves_other_writer(runs, tmp_path, monkeypatch):
    destination = tmp_path / "evaluation.json"
    monkeypatch.setattr(evaluation, "_verify_file", lambda *a: {"verified": True})

    def another_writer_wins(source, target):
        target.write_text("other process report")
        raise FileExistsError(target)

    monkeypatch.setattr(evaluation.os, "link", another_writer_wins)
    args = SimpleNamespace(
        run=runs.candidate, sources=runs.sources, compare=None, report=destination
    )
    with pytest.raises(FileExistsError):
        evaluation.evaluate_command(args)
    assert destination.read_text() == "other process report"
    assert not list(tmp_path.glob(".evaluation.json-*.tmp"))


def test_real_raw_audit_detects_gain_sample_and_exif_tampering(tmp_path):
    supplied = os.environ.get("FLICKER_TEST_RAW")
    if not supplied:
        pytest.skip("Set FLICKER_TEST_RAW to opt in to the real RAW integrity regression")
    original = Path(supplied).resolve()
    if not original.is_file():
        pytest.skip("Set FLICKER_TEST_RAW for the real RAW integrity regression")
    original_hash = source_hash(original)
    sources = tmp_path / "sources"
    sources.mkdir()
    source = sources / original.name
    source.symlink_to(original)
    root = tmp_path / "run"
    _write_run(root, [source])
    (root / "gains").mkdir()
    gain_path = root / "gains" / f"{source.stem}.npy"
    np.save(gain_path, np.ones((1, 1, 3), np.float32))
    destination = root / "corrected-raw" / f"{source.stem}_deflicker.dng"
    dng.write_corrected_dng(source, destination)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    for kind in ("neutral-original", "physics"):
        folder = root / "previews" / kind
        folder.mkdir(parents=True)
        Image.new("RGB", (8, 8), (100, 100, 100)).save(
            folder / f"{source.stem}.jpg", icc_profile=icc
        )

    initial = evaluation.evaluate_run(root, sources)
    assert initial["integrity_passed"] is True
    assert initial["files"][0]["validation"]["compatibility_warnings"] == []

    np.save(gain_path, np.full((1, 1, 3), 1.01, np.float32))
    assert evaluation.evaluate_run(root, sources)["integrity_passed"] is False
    np.save(gain_path, np.ones((1, 1, 3), np.float32))

    mosaic = tifffile.memmap(destination, mode="r+")
    previous = int(mosaic[12, 12])
    mosaic[12, 12] = previous ^ 1
    mosaic.flush()
    assert evaluation.evaluate_run(root, sources)["integrity_passed"] is False
    mosaic[12, 12] = previous
    mosaic.flush()
    del mosaic

    # Change ISO in ExifIFD without touching any Bayer sample. A pixel-only
    # verifier would pass this, although the file's intended appearance changed.
    with tifffile.TiffFile(destination) as image:
        exif_offset = int(image.pages[0].tags[34665].valueoffset)
    with destination.open("r+b") as stream:
        stream.seek(exif_offset)
        count = struct.unpack("<H", stream.read(2))[0]
        for index in range(count):
            entry_offset = exif_offset + 2 + 12 * index
            stream.seek(entry_offset)
            entry = stream.read(12)
            if struct.unpack("<H", entry[:2])[0] == 34855:
                iso = struct.unpack("<H", entry[8:10])[0]
                stream.seek(entry_offset + 8)
                stream.write(struct.pack("<H", iso ^ 1))
                break
        else:
            pytest.fail("Fixture does not contain the ISO tag required for this regression")
    final = evaluation.evaluate_run(root, sources)
    assert final["integrity_passed"] is False
    assert "ISOSpeedRatings" in final["files"][0]["error"]
    assert source_hash(original) == original_hash
