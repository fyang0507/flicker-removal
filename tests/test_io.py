from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from flicker_removal import io


class FakeRaw:
    def __init__(self, mosaic=None):
        self.raw_pattern = np.array([[0, 1], [3, 2]])
        self.color_desc = b"RGBG"
        self.black_level_per_channel = [10, 20, 30, 40]
        self.white_level = 125
        self.raw_image_visible = mosaic
        self.camera_whitebalance = [2, 1, 3, 1]
        self.sizes = SimpleNamespace(
            width=16,
            height=8,
            crop_left_margin=4,
            crop_top_margin=2,
            crop_width=8,
            crop_height=4,
            flip=6,
        )
        self.kwargs = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def postprocess(self, **kwargs):
        self.kwargs = kwargs
        height, width = (8, 16) if not kwargs["half_size"] else (4, 8)
        return np.full((height, width, 3), 16384, dtype=np.uint16)


def test_sensor_proxy_black_subtraction_and_two_green_sites(monkeypatch):
    mosaic = np.tile(np.array([[60, 70], [90, 80]], dtype=np.uint16), (8, 12))
    fake = FakeRaw(mosaic)
    monkeypatch.setattr(io.rawpy, "imread", lambda _: fake)
    proxy = io.sensor_proxy("synthetic.ARW", block=8)
    assert proxy.shape == (2, 3, 3)
    assert proxy.dtype == np.float32
    np.testing.assert_allclose(proxy, 0.5)
    # Keep below-black values from becoming negative illumination estimates.
    fake.raw_image_visible[0::2, 0::2] = 0
    np.testing.assert_array_equal(io.sensor_proxy("synthetic.ARW")[:, :, 0], 0)


@pytest.mark.parametrize("block", [0, 1, 3, -2, 2.5, True])
def test_sensor_proxy_rejects_invalid_blocks(block):
    with pytest.raises(ValueError):
        io.sensor_proxy("unused", block=block)


def test_exact_srgb_transfer():
    values = np.array([-1, 0, 0.0031308, 0.18, 1, 2], dtype=np.float32)
    expected = np.array([0, 0, 0.040449936, 0.46135613, 1, 1])
    np.testing.assert_allclose(io.linear_to_srgb(values), expected, atol=1e-7)


def test_render_honors_crop_rotation_fixed_wb_and_icc(tmp_path, monkeypatch):
    fake = FakeRaw()
    monkeypatch.setattr(io.rawpy, "imread", lambda _: fake)
    destination = tmp_path / "comparison.jpg"
    report = io.render_preview(
        tmp_path / "source.ARW", destination, white_balance=[4, 2, 6, 2], max_dimension=100
    )
    # Half-size crop is 4×2; orientation 6 rotates it to 2×4.
    assert (report["width"], report["height"]) == (2, 4)
    assert fake.kwargs["no_auto_bright"] is True
    assert fake.kwargs["use_auto_wb"] is False
    assert fake.kwargs["gamma"] == (1, 1)
    assert fake.kwargs["user_wb"] == [4, 2, 6, 2]
    assert fake.kwargs["user_flip"] == 0
    with Image.open(destination) as rendered:
        assert rendered.info["icc_profile"]
        assert rendered.size == (2, 4)


def test_full_resolution_bypasses_preview_size(tmp_path, monkeypatch):
    fake = FakeRaw()
    monkeypatch.setattr(io.rawpy, "imread", lambda _: fake)
    report = io.render_preview(
        "source.dng", tmp_path / "full.png", full_resolution=True, max_dimension=2
    )
    assert (report["width"], report["height"]) == (4, 8)
    assert fake.kwargs["half_size"] is False


def test_dng_three_channel_white_balance_zero_fourth_slot(tmp_path, monkeypatch):
    fake = FakeRaw()
    fake.camera_whitebalance = [2, 1, 3, 0]
    monkeypatch.setattr(io.rawpy, "imread", lambda _: fake)
    report = io.render_preview("source.dng", tmp_path / "dng.jpg")
    assert report["white_balance"] == [2, 1, 3, 1]


def test_discovery_and_source_digest(tmp_path):
    (tmp_path / "a.ARW").write_bytes(b"abc")
    (tmp_path / "b.dng").write_bytes(b"def")
    (tmp_path / "ignored.jpg").write_bytes(b"ghi")
    assert [p.name for p in io.discover_inputs(tmp_path)] == ["a.ARW", "b.dng"]
    assert (
        io.source_hash(tmp_path / "a.ARW")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_efcs_off_is_not_invented_as_mechanical(tmp_path, monkeypatch):
    monkeypatch.setattr(io.shutil, "which", lambda _: "/usr/local/bin/exiftool")
    monkeypatch.setattr(
        io.subprocess,
        "check_output",
        lambda *a, **k: '[{"SourceFile":"sample", "ElectronicFrontCurtainShutter":"Off"}]',
    )
    metadata = io._shutter_metadata(tmp_path / "sample.ARW")
    assert metadata["shutter_mode"] == "unknown"


def test_sony_shutter_tag_and_diagnostic_exposure(tmp_path, monkeypatch):
    monkeypatch.setattr(io.shutil, "which", lambda _: "/usr/local/bin/exiftool")
    monkeypatch.setattr(
        io.subprocess,
        "check_output",
        lambda *a, **k: (
            '[{"Shutter":"Silent / Electronic (0 0 0)", "SonyExposureTime":"0.00540589797642122"}]'
        ),
    )
    metadata = io._shutter_metadata(tmp_path / "sample.ARW")
    assert metadata["shutter_mode"] == "electronic"
    assert metadata["sony_exposure_s"] == pytest.approx(0.00540589797642122)
    assert "shutter_s" not in metadata


def test_dng_ifd0_iso_fallback_preserves_nominal_exposure(tmp_path, monkeypatch):
    source = tmp_path / "source.dng"
    source.write_bytes(b"test")
    fake = FakeRaw()
    fake.other = SimpleNamespace(
        iso_speed=0,
        shutter_speed=0.005,
        aperture=5,
        focal_length=64.1,
        timestamp="2000-01-01 00:00:00",
    )
    fake.sizes._asdict = lambda: {"width": 16, "height": 8}
    monkeypatch.setattr(io.rawpy, "imread", lambda _: fake)
    monkeypatch.setattr(
        io.exifread,
        "process_file",
        lambda *a, **k: {
            "Image Model": "Camera",
            "Image ISOSpeedRatings": "1600",
            "Image ExposureTime": "1/200",
        },
    )
    monkeypatch.setattr(io, "_shutter_metadata", lambda _: {"sony_exposure_s": 0.0054})
    metadata = io.read_metadata(source)
    assert metadata["iso"] == 1600
    assert metadata["shutter_s"] == 0.005
    assert metadata["exif"]["EXIF ExposureTime"] == "1/200"
