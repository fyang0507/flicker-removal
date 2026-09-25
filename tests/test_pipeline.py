"""Public end-to-end RAW coverage using generated sensor samples, no private media."""

from __future__ import annotations

import json

import numpy as np
import tifffile

from flicker_removal import cli, dng, io


def _synthetic_bayer_dng(path, phase):
    """Write a small physical-intensity flicker signal in a valid CFA container.

    The camera name enables LibRaw's established matrix interpretation; no camera
    image or private metadata is used. Smooth horizontal scene shading ensures
    this is an ingestion/export fixture, not a claim of natural-image quality.
    """
    height, width = 768, 1024
    yy, xx = np.mgrid[:height, :width]
    wave = np.cos(2 * np.pi * 8.1 * (yy + 0.5) / height + phase)
    shade = 0.9 + 0.15 * xx / (width - 1)
    mosaic = np.empty((height, width), dtype=np.uint16)
    for y, x, level, modulation in (
        (0, 0, 0.24, 0.12),
        (0, 1, 0.28, 0.10),
        (1, 0, 0.28, 0.10),
        (1, 1, 0.21, 0.075),
    ):
        signal = level * shade[y::2, x::2] * (1 + modulation * wave[y::2, x::2])
        mosaic[y::2, x::2] = np.rint(512 + (16383 - 512) * signal).astype(np.uint16)
    matrix = (
        7460,
        10000,
        -2365,
        10000,
        -588,
        10000,
        -5687,
        10000,
        13442,
        10000,
        2474,
        10000,
        -624,
        10000,
        1156,
        10000,
        6584,
        10000,
    )
    tags = [
        (271, "s", 0, "SONY", False),
        (272, "s", 0, "ILCE-7M4", False),
        (274, "H", 1, 1, False),
        (33421, "H", 2, (2, 2), False),
        (33422, "B", 4, (0, 1, 1, 2), False),
        (50706, "B", 4, (1, 4, 0, 0), False),
        (50707, "B", 4, (1, 1, 0, 0), False),
        (50708, "s", 0, "SONY ILCE-7M4", False),
        (50710, "B", 3, (0, 1, 2), False),
        (50711, "H", 1, 1, False),
        (50713, "H", 2, (2, 2), False),
        (50714, "2I", 4, (512, 1) * 4, False),
        (50717, "I", 1, 16383, False),
        (50718, "2I", 2, (1, 1, 1, 1), False),
        (50719, "I", 2, (0, 0), False),
        (50720, "I", 2, (width, height), False),
        (50721, "2i", 9, matrix, False),
        (50728, "2I", 3, (1, 2, 1, 1, 1, 2), False),
        (50778, "H", 1, 21, False),
        (50829, "I", 4, (0, 0, height, width), False),
        (33434, "2I", 1, (1, 200), False),
        (33437, "2I", 1, (5, 1), False),
        (34855, "H", 1, 1600, False),
        (37386, "2I", 1, (50, 1), False),
    ]
    tifffile.imwrite(
        path,
        mosaic,
        photometric=32803,
        metadata=None,
        byteorder="<",
        rowsperstrip=128,
        extratags=tags,
    )
    dng._place_exif_ifd(path, {33434, 33437, 34855, 37386})


def test_generated_raw_calibrate_batch_evaluate(tmp_path, monkeypatch):
    # Do not require ExifTool or infer a shutter mode from the fixture's name.
    monkeypatch.setattr(io.shutil, "which", lambda name: None)
    sources = tmp_path / "sources"
    sources.mkdir()
    for index, phase in enumerate((0.0, 0.7, 2.1, 4.2)):
        _synthetic_bayer_dng(sources / f"synthetic-{index}.dng", phase)
    originals = {path.name: io.source_hash(path) for path in sources.iterdir()}
    profile = tmp_path / "lighting.json"
    run = tmp_path / "run"
    report = tmp_path / "evaluation.json"

    assert cli.main(["calibrate", str(sources), "--profile", str(profile)]) == 0
    assert (
        cli.main(
            ["batch", str(sources), "--profile", str(profile), "--output", str(run), "--full-jpeg"]
        )
        == 0
    )
    assert cli.main(["evaluate", str(run), "--sources", str(sources), "--report", str(report)]) == 0

    evaluation = json.loads(report.read_text())
    assert evaluation["integrity_passed"] is True
    assert evaluation["file_count"] == len(originals)
    assert evaluation["status_counts"].get("corrected", 0) > 0
    for item in evaluation["files"]:
        assert item["integrity"] == "passed"
        assert item["validation"]["bayer_matches_source_and_saved_gains"] is True
        assert item["validation"]["preview_exif_iso_present"] is True
        output = run / "corrected-raw" / f"{item['file'][:-4]}_deflicker.dng"
        with tifffile.TiffFile(output) as image:
            page = image.pages[0]
            assert page.photometric == 32803
            assert 34855 not in page.tags
            assert page.tags[34665].value["ISOSpeedRatings"] == 1600
    assert {path.name: io.source_hash(path) for path in sources.iterdir()} == originals
