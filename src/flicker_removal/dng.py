"""Non-destructive, verified Bayer DNG delivery for RGB Bayer cameras.

This product includes DNG technology under license by Adobe.

The original camera file is only read. This is a CFA DNG writer, not an RGB
TIFF writer with a renamed extension. See docs/raw-format.md for limitations.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

import exifread
import numpy as np
import rawpy
import tifffile


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rational(values: Any, denominator: int = 1_000_000) -> tuple[int, ...]:
    """Flatten rational pairs for tifffile's 2I/2i datatypes."""
    result = []
    for value in np.asarray(values).reshape(-1):
        result.extend((int(round(float(value) * denominator)), denominator))
    return tuple(result)


def _source_metadata(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        tags = exifread.process_file(stream, details=False)
    return tags


def _exif_tags(tags: dict[str, Any]) -> list[tuple]:
    """Build EXIF values; move these into ExifIFD before publishing the DNG."""
    result = []
    rational_tags = {
        "EXIF ExposureTime": (33434, "2I"),
        "EXIF FNumber": (33437, "2I"),
        "EXIF ShutterSpeedValue": (37377, "2i"),
        "EXIF ApertureValue": (37378, "2I"),
        "EXIF ExposureBiasValue": (37380, "2i"),
        "EXIF FocalLength": (37386, "2I"),
    }
    for key, (code, dtype) in rational_tags.items():
        if key in tags:
            ratio = tags[key].values[0]
            result.append((code, dtype, 1, (int(ratio.num), int(ratio.den)), False))
    for key, code in {
        "EXIF ISOSpeedRatings": 34855,
        "EXIF ExposureProgram": 34850,
        "EXIF MeteringMode": 37383,
        "EXIF WhiteBalance": 41987,
        "EXIF FocalLengthIn35mmFilm": 41989,
    }.items():
        if key in tags:
            value = int(tags[key].values[0])
            if 0 <= value <= 65535:
                result.append((code, "H", 1, value, False))
    for key, code in {
        "EXIF DateTimeOriginal": 36867,
        "EXIF DateTimeDigitized": 36868,
        "EXIF OffsetTimeOriginal": 36881,
        "EXIF OffsetTimeDigitized": 36882,
        "EXIF SubSecTimeOriginal": 37521,
        "EXIF SubSecTimeDigitized": 37522,
        "EXIF LensModel": 42036,
    }.items():
        if key in tags:
            result.append((code, "s", 0, str(tags[key]), False))
    return result


def _place_exif_ifd(path: Path, codes: set[int]) -> None:
    """Relocate writer-generated EXIF entries without rewriting their payloads.

    TIFF-EP permits these tags in IFD0, but Apple's RAW decoder ignores ISO
    there and chooses an incorrect exposure baseline. tifffile has no ExifIFD
    writer, so append a conventional EXIF directory and replacement IFD0.
    All existing sample/payload offsets remain valid. This helper is only for
    the temporary, little-endian classic TIFF produced by this writer.
    """
    if not codes:
        return
    with path.open("r+b") as stream:
        header = stream.read(8)
        if header[:4] != b"II*\0":
            raise ValueError("EXIF placement requires a little-endian classic TIFF")
        stream.seek(struct.unpack("<I", header[4:])[0])
        count = struct.unpack("<H", stream.read(2))[0]
        entries = [stream.read(12) for _ in range(count)]
        next_ifd = stream.read(4)
        if any(len(entry) != 12 for entry in entries) or len(next_ifd) != 4:
            raise ValueError("Truncated TIFF directory")
        code = lambda entry: struct.unpack("<H", entry[:2])[0]
        if any(code(entry) == 34665 for entry in entries):
            raise ValueError("Writer temporary unexpectedly already contains ExifIFD")
        exif = [entry for entry in entries if code(entry) in codes]
        if {code(entry) for entry in exif} != codes:
            raise ValueError("Missing writer EXIF entries")
        main = [entry for entry in entries if code(entry) not in codes]
        stream.seek(0, os.SEEK_END)
        stream.write(b"\0" * (-stream.tell() % 4))
        exif_offset = stream.tell()
        main_offset = exif_offset + 2 + 12 * len(exif) + 4
        if main_offset + 2 + 12 * (len(main) + 1) + 4 >= 2**32:
            raise ValueError("EXIF directories exceed classic TIFF offset range")
        stream.write(struct.pack("<H", len(exif)) + b"".join(exif) + b"\0" * 4)
        main.append(struct.pack("<HHII", 34665, 4, 1, exif_offset))
        main.sort(key=code)
        stream.write(struct.pack("<H", len(main)) + b"".join(main) + next_ifd)
        stream.seek(4)
        stream.write(struct.pack("<I", main_offset))


def _xmp_packet(source: Path) -> bytes:
    """Copy ratings/labels only; never claim the source Adobe edit was baked in."""
    ns = {
        "x": "adobe:ns:meta/",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "xmp": "http://ns.adobe.com/xap/1.0/",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    for prefix, uri in ns.items():
        ET.register_namespace(prefix, uri)
    root = ET.Element(f"{{{ns['x']}}}xmpmeta")
    rdf = ET.SubElement(root, f"{{{ns['rdf']}}}RDF")
    desc = ET.SubElement(rdf, f"{{{ns['rdf']}}}Description", {f"{{{ns['rdf']}}}about": ""})
    desc.set(f"{{{ns['xmp']}}}CreatorTool", "flicker-removal deterministic Bayer correction")
    desc.set(f"{{{ns['dc']}}}format", "image/dng")
    sidecar = source.with_suffix(".xmp")
    if sidecar.exists():
        try:
            sidecar_root = ET.parse(sidecar).getroot()
            for description in sidecar_root.iter(f"{{{ns['rdf']}}}Description"):
                for name in ("Rating", "Label", "CreateDate"):
                    key = f"{{{ns['xmp']}}}{name}"
                    if key in description.attrib:
                        desc.set(key, description.attrib[key])
        except ET.ParseError:
            # The source sidecar remains intact even when its XML is malformed.
            pass
    return ET.tostring(root, encoding="utf-8")


def _gain_array(gain_grid: Any) -> np.ndarray:
    gains = np.asarray(gain_grid, dtype=np.float32)
    if gains.ndim == 0:
        gains = np.full((1, 1, 3), float(gains), dtype=np.float32)
    elif gains.ndim == 1:
        # A vector is a per-row neutral gain, not an ambiguous RGB triplet.
        gains = np.repeat(gains[:, None, None], 3, axis=2)
    elif gains.ndim == 2 and gains.shape[1] == 3:
        gains = gains[:, None, :]
    elif gains.ndim != 3 or gains.shape[2] != 3:
        raise ValueError("gain_grid must be scalar, rows, rows×3, or rows×columns×3")
    if min(gains.shape) < 1 or not np.all(np.isfinite(gains)) or np.any(gains <= 0):
        raise ValueError("All correction gains must be finite and strictly positive")
    if np.max(gains) > 16:
        raise ValueError("Correction gain above 16 is outside this writer's safety bound")
    return gains


def _axis_coordinates(source_size: int, target_size: int, index: np.ndarray) -> tuple:
    # Grid samples are block centers; edge extension avoids artificial edge slopes.
    coordinate = np.clip((index + 0.5) * source_size / target_size - 0.5, 0, source_size - 1)
    lower = np.floor(coordinate).astype(np.intp)
    upper = np.minimum(lower + 1, source_size - 1)
    weight = coordinate - lower
    return lower, upper, weight


def _apply_gain(raw: Any, gains: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Apply on sensor samples in row blocks, retaining all original margins."""
    output = raw.raw_image.copy()
    visible = raw.raw_image_visible
    height, width = visible.shape
    top, left = int(raw.sizes.top_margin), int(raw.sizes.left_margin)
    pattern = np.asarray(raw.raw_pattern)
    color_desc = raw.color_desc.decode("ascii")
    blacks = np.asarray(raw.black_level_per_channel, dtype=np.float32)
    clipped_u16 = 0
    newly_above_white = 0
    above_white_before = 0
    for parity_y in range(2):
        for parity_x in range(2):
            channel = int(pattern[parity_y, parity_x])
            rgb_channel = "RGB".index(color_desc[channel])
            y_indices = np.arange(parity_y, height, 2)
            x_indices = np.arange(parity_x, width, 2)
            x0, x1, xw = _axis_coordinates(gains.shape[1], width, x_indices)
            plane = gains[:, :, rgb_channel]
            for start in range(0, len(y_indices), 128):
                ys = y_indices[start : start + 128]
                y0, y1, yw = _axis_coordinates(gains.shape[0], height, ys)
                upper = plane[y0[:, None], x0] * (1 - xw) + plane[y0[:, None], x1] * xw
                lower = plane[y1[:, None], x0] * (1 - xw) + plane[y1[:, None], x1] * xw
                multiplier = upper * (1 - yw[:, None]) + lower * yw[:, None]
                original = visible[np.ix_(ys, x_indices)].astype(np.float32)
                corrected = (original - blacks[channel]) * multiplier + blacks[channel]
                original_clipped = original >= raw.white_level
                above_white_before += int(np.count_nonzero(original_clipped))
                newly_above_white += int(
                    np.count_nonzero((corrected > raw.white_level) & ~original_clipped)
                )
                clipped_u16 += int(np.count_nonzero((corrected > 65535) | (corrected < 0)))
                output[np.ix_(ys + top, x_indices + left)] = np.clip(
                    np.rint(corrected), 0, 65535
                ).astype(np.uint16)
    pixels = height * width
    return output, {
        "gain_min": float(np.min(gains)),
        "gain_max": float(np.max(gains)),
        "gain_grid_shape": list(gains.shape),
        "gain_coordinate_system": "unrotated raw_image_visible; pixel/block centers; bilinear interpolation",
        "source_at_or_above_white_fraction": above_white_before / pixels,
        "newly_above_white_fraction": newly_above_white / pixels,
        "uint16_clipped_fraction": clipped_u16 / pixels,
    }


def validate_dng(
    path: str | Path,
    *,
    expected_bayer: np.ndarray | None = None,
    expected_shape: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Reopen independently with tifffile and LibRaw, checking actual RAW samples."""
    path = Path(path)
    required = [
        50706,
        50707,
        50708,
        33421,
        33422,
        50710,
        50713,
        50714,
        50717,
        50721,
        50728,
        50778,
        50829,
        50719,
        50720,
    ]
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        missing = [code for code in required if code not in page.tags]
        if missing:
            raise ValueError(f"Missing DNG tags: {missing}")
        if int(page.photometric) != 32803 or page.samplesperpixel != 1 or page.bitspersample != 16:
            raise ValueError("DNG must contain 16-bit, single-plane CFA data")
        if tuple(page.tags[50706].value) != (1, 4, 0, 0):
            raise ValueError("Unexpected DNG version")
        stored = page.asarray()
        if expected_bayer is not None and not np.array_equal(stored, expected_bayer):
            raise ValueError("TIFF RAW samples do not match written Bayer data")
        cfa_value = page.tags[33422].value
        expected_pattern = (
            np.frombuffer(cfa_value, dtype=np.uint8)
            if isinstance(cfa_value, bytes)
            else np.asarray(cfa_value)
        ).reshape(2, 2)
        expected_black = np.asarray(page.tags[50714].value, dtype=float).reshape(-1, 2)
        expected_black = (expected_black[:, 0] / expected_black[:, 1]).reshape(2, 2)
        expected_white = int(page.tags[50717].value)
    with rawpy.imread(str(path)) as decoded:
        if decoded.raw_pattern is None or tuple(decoded.raw_pattern.shape) != (2, 2):
            raise ValueError("LibRaw did not decode a Bayer CFA")
        if expected_shape is not None and tuple(decoded.raw_image.shape) != tuple(expected_shape):
            raise ValueError("LibRaw decoded unexpected RAW dimensions")
        if not np.array_equal(decoded.raw_image, stored):
            raise ValueError("LibRaw RAW samples differ from TIFF RAW samples")
        semantic_pattern = np.array(
            ["RGB".index(chr(decoded.color_desc[i])) for i in decoded.raw_pattern.flat]
        ).reshape(2, 2)
        if not np.array_equal(semantic_pattern, expected_pattern):
            raise ValueError("LibRaw decoded a different CFA pattern")
        black_pattern = np.asarray(decoded.black_level_per_channel)[decoded.raw_pattern]
        if not np.allclose(black_pattern, expected_black, atol=1):
            raise ValueError("LibRaw decoded different black levels")
        if decoded.white_level != expected_white:
            raise ValueError("LibRaw decoded a different white level")
        return {
            "tifffile_reopen": True,
            "libraw_reopen": True,
            "bayer_exact_match": True,
            "raw_shape": list(decoded.raw_image.shape),
            "visible_shape": list(decoded.raw_image_visible.shape),
            "black_level_per_channel": list(decoded.black_level_per_channel),
            "white_level": int(decoded.white_level),
            "camera_whitebalance": list(decoded.camera_whitebalance),
            "orientation_flip": int(decoded.sizes.flip),
            "adobe_camera_raw_verified": False,
        }


def write_corrected_dng(
    source: str | Path,
    destination: str | Path,
    gain_grid: Any = 1.0,
    *,
    metadata: dict[str, Any] | None = None,
    overwrite: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    """Write corrected sensor data while preserving the original ARW.

    Gains multiply the black-subtracted signal in *camera RGB*, with the green
    gain shared by both Bayer greens. A rows×columns×3 low-resolution gain grid
    spans raw_image_visible, before rotation or default crop. Sensor samples are
    neither spatially interpolated nor demosaiced; only the gain is interpolated.

    Values above the original white level are retained (up to uint16 capacity)
    and counted, so later exposure reduction can use them in supporting readers.
    Source saturation cannot be reconstructed. Existing source XMP files are
    never rewritten, and Adobe development settings are not silently baked in.
    """
    source, destination = Path(source), Path(destination)
    if destination.suffix.lower() != ".dng":
        raise ValueError("The corrected RAW output must use a .dng extension")
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must be different files")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    gains = _gain_array(gain_grid)
    tags = _source_metadata(source)
    make, model = str(tags.get("Image Make", "")), str(tags.get("Image Model", ""))
    if not make or not model:
        raise ValueError("Camera make/model metadata is required")
    orientation_tag = tags.get("Image Orientation")
    orientation = int(orientation_tag.values[0]) if orientation_tag is not None else 1
    if orientation not in range(1, 9):
        raise ValueError("Invalid TIFF orientation")
    source_digest = _sha256(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".dng", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with rawpy.imread(str(source)) as raw:
            if (
                raw.raw_pattern is None
                or raw.raw_pattern.shape != (2, 2)
                or raw.color_desc != b"RGBG"
            ):
                raise ValueError("This writer currently supports RGBG Bayer RAW only")
            corrected, stats = _apply_gain(raw, gains)
            sizes = raw.sizes
            top, left = int(sizes.top_margin), int(sizes.left_margin)
            height, width = raw.raw_image_visible.shape
            # DNG CFAPattern is relative to ActiveArea, as is BlackLevelRepeatDim.
            pattern = np.asarray(raw.raw_pattern)
            cfa_pattern = tuple(1 if int(i) == 3 else int(i) for i in pattern.flat)
            black_pattern = np.asarray(raw.black_level_per_channel)[pattern]
            matrix = np.asarray(raw.rgb_xyz_matrix, dtype=np.float64)[:3, :3]
            if not np.all(np.isfinite(matrix)) or abs(np.linalg.det(matrix)) < 1e-8:
                raise ValueError("Missing or singular camera XYZ-to-RGB color matrix")
            wb = np.asarray(raw.camera_whitebalance[:3], dtype=np.float64)
            if np.any(wb <= 0) or not np.all(np.isfinite(wb)):
                raise ValueError("Missing camera white balance")
            neutral = wb[1] / wb
            crop_x = int(getattr(sizes, "crop_left_margin", 0))
            crop_y = int(getattr(sizes, "crop_top_margin", 0))
            crop_w = int(getattr(sizes, "crop_width", 0))
            crop_h = int(getattr(sizes, "crop_height", 0))
            if not (
                crop_w > 0
                and crop_h > 0
                and crop_x >= 0
                and crop_y >= 0
                and crop_x + crop_w <= width
                and crop_y + crop_h <= height
            ):
                crop_x, crop_y, crop_w, crop_h = 0, 0, width, height
            provenance = {
                "pipeline": "flicker-removal",
                "source_file": source.name,
                "source_sha256": source_digest,
                "method": "multiplicative black-subtracted Bayer correction",
                "original_sensor_shape": list(raw.raw_image.shape),
                "active_area": [top, left, top + height, left + width],
                "default_crop_origin": [crop_x, crop_y],
                "default_crop_size": [crop_w, crop_h],
                **stats,
                "processing_metadata": metadata or {},
            }
            xmp = _xmp_packet(source)
            exif_tags = _exif_tags(tags)
            extra = [
                (271, "s", 0, make, False),
                (272, "s", 0, model, False),
                (274, "H", 1, orientation, False),
                (33421, "H", 2, (2, 2), False),
                (33422, "B", 4, cfa_pattern, False),
                (50706, "B", 4, (1, 4, 0, 0), False),
                (50707, "B", 4, (1, 1, 0, 0), False),
                (50708, "s", 0, f"{make} {model}", False),
                (50710, "B", 3, (0, 1, 2), False),
                (50711, "H", 1, 1, False),
                (50713, "H", 2, (2, 2), False),
                (50714, "2I", 4, _rational(black_pattern), False),
                (50717, "I", 1, int(raw.white_level), False),
                (50718, "2I", 2, (1, 1, 1, 1), False),
                (50719, "I", 2, (crop_x, crop_y), False),
                (50720, "I", 2, (crop_w, crop_h), False),
                (50721, "2i", 9, _rational(matrix), False),
                (50728, "2I", 3, _rational(neutral), False),
                (50730, "2i", 1, (0, 1), False),
                (50778, "H", 1, 21, False),  # D65: LibRaw's camera matrix calibration.
                (
                    50827,
                    "B",
                    len(source.name.encode("utf-8")) + 1,
                    source.name.encode("utf-8") + b"\0",
                    False,
                ),
                (50829, "I", 4, (top, left, top + height, left + width), False),
                (700, "B", len(xmp), xmp, False),
            ] + exif_tags
            tifffile.imwrite(
                temporary,
                corrected,
                photometric=32803,
                metadata=None,
                compression=None,
                byteorder="<",
                bigtiff=False,
                subfiletype=0,
                rowsperstrip=128,
                software="flicker-removal Bayer DNG writer",
                description=json.dumps(provenance, sort_keys=True),
                datetime=str(tags["Image DateTime"]) if "Image DateTime" in tags else None,
                extratags=extra,
            )
            _place_exif_ifd(temporary, {entry[0] for entry in exif_tags})
            verification = (
                validate_dng(temporary, expected_bayer=corrected, expected_shape=corrected.shape)
                if validate
                else {"skipped": True}
            )
            if validate:
                reopened_wb = np.asarray(verification["camera_whitebalance"][:3])
                if not np.allclose(reopened_wb / reopened_wb[1], wb / wb[1], atol=1e-5):
                    raise ValueError("DNG white balance differs from source")
            if overwrite:
                os.replace(temporary, destination)
            else:
                os.link(temporary, destination)
                temporary.unlink()
            return {
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "source_sha256": source_digest,
                **stats,
                "validation": verification,
            }
    finally:
        temporary.unlink(missing_ok=True)
