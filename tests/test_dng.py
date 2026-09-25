"""RAW delivery checks; integration tests use a local source if it is available."""

import hashlib
import os
from pathlib import Path

import exifread
import numpy as np
import pytest
import rawpy
import tifffile

from flicker_removal import dng


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _exif(path):
    with Path(path).open("rb") as stream:
        return exifread.process_file(stream, details=False)


def _exif_values(tag):
    """Compare rational values exactly, without display rounding or alias names."""
    if isinstance(tag.values, str):
        return tag.values
    return [(value.num, value.den) if hasattr(value, "num") else value for value in tag.values]


@pytest.fixture(scope="module")
def source():
    supplied = os.environ.get("FLICKER_TEST_RAW")
    if not supplied:
        pytest.skip("Set FLICKER_TEST_RAW to opt in to local RAW integration tests")
    path = Path(supplied)
    if not path.is_file():
        pytest.skip("Set FLICKER_TEST_RAW to a local Sony A7 IV ARW for DNG integration tests")
    return path


@pytest.fixture(scope="module")
def identity(source, tmp_path_factory):
    destination = tmp_path_factory.mktemp("dng_identity") / "identity.dng"
    before = _digest(source)
    result = dng.write_corrected_dng(source, destination)
    assert _digest(source) == before
    return destination, result


def test_identity_preserves_actual_mosaic_and_raw_metadata(source, identity):
    destination, result = identity
    with rawpy.imread(str(source)) as original, rawpy.imread(str(destination)) as restored:
        np.testing.assert_array_equal(restored.raw_image, original.raw_image)
        assert restored.sizes == original.sizes
        assert restored.black_level_per_channel == original.black_level_per_channel
        assert restored.white_level == original.white_level
        original_wb = np.array(original.camera_whitebalance[:3])
        restored_wb = np.array(restored.camera_whitebalance[:3])
        np.testing.assert_allclose(
            restored_wb / restored_wb[1], original_wb / original_wb[1], atol=1e-5
        )
        crop_size = (
            original.sizes.crop_width or original.sizes.width,
            original.sizes.crop_height or original.sizes.height,
        )
    source_exif, output_exif = _exif(source), _exif(destination)
    with tifffile.TiffFile(destination) as tif:
        assert tif.pages[0].photometric == 32803  # Actual CFA, not rendered RGB.
        assert tif.pages[0].samplesperpixel == 1
        assert tif.pages[0].tags[50720].value == crop_size
        assert "source_sha256" in tif.pages[0].description
        # Apple ignores TIFF-EP ISO in IFD0 and then renders these files ~0.8EV
        # darker. Require conventional EXIF placement independently of LibRaw.
        assert 34855 not in tif.pages[0].tags
        if "EXIF ISOSpeedRatings" in source_exif:
            exif = tif.pages[0].tags[34665].value
            assert exif["ISOSpeedRatings"] == source_exif["EXIF ISOSpeedRatings"].values[0]
    # These are the documented preserved EXIF fields. Compare the supplied
    # fixture's values, so another shutter speed, ISO or lens is a valid test.
    for name in (
        "ExposureTime",
        "FNumber",
        "ShutterSpeedValue",
        "ApertureValue",
        "ExposureBiasValue",
        "FocalLength",
        "ISOSpeedRatings",
        "ExposureProgram",
        "MeteringMode",
        "WhiteBalance",
        "FocalLengthIn35mmFilm",
        "DateTimeOriginal",
        "DateTimeDigitized",
        "OffsetTimeOriginal",
        "OffsetTimeDigitized",
        "SubSecTimeOriginal",
        "SubSecTimeDigitized",
        "LensModel",
    ):
        key = f"EXIF {name}"
        if key in source_exif:
            assert key in output_exif, key
            assert _exif_values(output_exif[key]) == _exif_values(source_exif[key]), key
    assert result["source_sha256"] == _digest(source)
    assert result["validation"]["bayer_exact_match"] is True
    assert result["uint16_clipped_fraction"] == 0


def test_identity_develops_with_same_color_and_exposure(source, identity):
    destination, _ = identity
    parameters = dict(
        half_size=True,
        use_camera_wb=True,
        no_auto_bright=True,
        output_bps=16,
        gamma=(1, 1),
        user_flip=0,
        output_color=rawpy.ColorSpace.sRGB,
    )
    with rawpy.imread(str(source)) as original:
        original_rgb = original.postprocess(**parameters)
    with rawpy.imread(str(destination)) as restored:
        restored_rgb = restored.postprocess(**parameters)
    assert restored_rgb.shape == original_rgb.shape
    difference = np.abs(restored_rgb.astype(np.int32) - original_rgb.astype(np.int32))
    # Catch wrong matrix direction, WB inversion, black subtraction, or CFA phase.
    assert np.mean(difference) < 0.1
    assert np.max(difference) <= 8


def test_camera_rgb_gains_preserve_black_offsets_and_margin_samples(source, tmp_path):
    destination = tmp_path / "rgb.dng"
    gains = np.array([[[1.1, 0.9, 1.05]]], dtype=np.float32)
    before = _digest(source)
    dng.write_corrected_dng(source, destination, gains)
    with rawpy.imread(str(source)) as original, rawpy.imread(str(destination)) as restored:
        for y in range(2):
            for x in range(2):
                raw_channel = int(original.raw_pattern[y, x])
                rgb_channel = "RGB".index(chr(original.color_desc[raw_channel]))
                black = original.black_level_per_channel[raw_channel]
                plane = original.raw_image_visible[y::2, x::2].astype(np.float64)
                expected = np.clip(
                    np.rint(black + (plane - black) * float(gains[0, 0, rgb_channel])), 0, 65535
                )
                np.testing.assert_array_equal(restored.raw_image_visible[y::2, x::2], expected)
        right = original.sizes.left_margin + original.sizes.width
        np.testing.assert_array_equal(restored.raw_image[:, right:], original.raw_image[:, right:])
    assert _digest(source) == before


@pytest.mark.parametrize("bad_gain", [0, -1, np.nan, np.inf, np.ones((3, 2))])
def test_invalid_gains_do_not_create_output(tmp_path, bad_gain):
    destination = tmp_path / "invalid.dng"
    with pytest.raises(ValueError):
        dng.write_corrected_dng(tmp_path / "need-not-exist.ARW", destination, bad_gain)
    assert not destination.exists()


def test_existing_output_is_never_silently_replaced(tmp_path):
    destination = tmp_path / "existing.dng"
    destination.write_bytes(b"existing user data")
    with pytest.raises(FileExistsError):
        dng.write_corrected_dng(tmp_path / "need-not-exist.ARW", destination)
    assert destination.read_bytes() == b"existing user data"


def test_failed_reopen_is_not_published(source, tmp_path, monkeypatch):
    destination = tmp_path / "rejected.dng"

    def reject(*args, **kwargs):
        raise ValueError("Independent RAW decoder rejected output")

    monkeypatch.setattr(dng, "validate_dng", reject)
    with pytest.raises(ValueError, match="decoder rejected"):
        dng.write_corrected_dng(source, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected-*.dng"))


def test_disguised_rgb_tiff_is_rejected(tmp_path):
    destination = tmp_path / "fake.dng"
    tifffile.imwrite(destination, np.zeros((8, 8, 3), np.uint16), photometric="rgb")
    with pytest.raises(ValueError, match="Missing DNG tags"):
        dng.validate_dng(destination)
