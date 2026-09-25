from pathlib import Path
import pytest
from flicker_removal.cli import _preflight_destinations, _preflight_profile


def test_batch_cannot_overwrite_another_input_even_with_overwrite(tmp_path):
    folder = tmp_path / "corrected-raw"
    folder.mkdir()
    a = folder / "foo.dng"
    b = folder / "foo_deflicker.dng"
    a.write_bytes(b"original a")
    b.write_bytes(b"original b")
    with pytest.raises(ValueError, match="overwrite an input"):
        _preflight_destinations([a, b], tmp_path, True)
    assert a.read_bytes() == b"original a" and b.read_bytes() == b"original b"


def test_colliding_arw_dng_stems_rejected_before_writes(tmp_path):
    with pytest.raises(ValueError, match="stems collide"):
        _preflight_destinations(
            [tmp_path / "foo.ARW", tmp_path / "foo.DNG"], tmp_path / "out", True
        )


def test_existing_results_require_explicit_overwrite(tmp_path):
    out = tmp_path / "out"
    (out / "corrected-raw").mkdir(parents=True)
    (out / "corrected-raw" / "foo_deflicker.dng").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        _preflight_destinations([tmp_path / "foo.ARW"], out)
    _preflight_destinations([tmp_path / "foo.ARW"], out, True)


def test_profile_cannot_replace_raw_or_follow_symlink_to_source(tmp_path):
    raw = tmp_path / "foo.ARW"
    raw.write_bytes(b"original")
    with pytest.raises(ValueError, match=".json"):
        _preflight_profile([raw], raw, True)
    link = tmp_path / "profile.json"
    link.symlink_to(raw)
    with pytest.raises(ValueError, match="overwrite an input"):
        _preflight_profile([raw], link, True)
    assert raw.read_bytes() == b"original"


def test_profile_diagnostic_symlink_is_also_protected(tmp_path):
    raw = tmp_path / "foo.ARW"
    raw.write_bytes(b"original")
    (tmp_path / "profile.calibration.json").symlink_to(raw)
    with pytest.raises(ValueError, match="overwrite an input"):
        _preflight_profile([raw], tmp_path / "profile.json", True)
