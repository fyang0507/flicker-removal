"""Read-only RAW ingestion and consistent, color-tagged comparison rendering.

Sensor proxies use unrotated raw_image_visible coordinates. Rendered previews
apply the camera's default crop and orientation after linear demosaicing.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

import exifread
import numpy as np
import rawpy
from PIL import Image, ImageCms

RAW_SUFFIXES = frozenset({".arw", ".dng"})


def discover_inputs(inputdir: str | Path) -> list[Path]:
    """Return sorted ARW/DNG files beneath a directory, without duplicates."""
    root = Path(inputdir)
    if root.is_file():
        if root.suffix.lower() not in RAW_SUFFIXES:
            raise ValueError(f"Unsupported RAW extension: {root.suffix}")
        return [root]
    if not root.is_dir():
        raise FileNotFoundError(root)
    found = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in RAW_SUFFIXES:
            found.setdefault(path.resolve(), path)
    return sorted(found.values(), key=lambda p: str(p).casefold())


def source_hash(path: str | Path) -> str:
    """SHA256 with bounded memory, without reading the entire RAW at once."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hardware_info() -> dict[str, Any]:
    result: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "rawpy": rawpy.__version__,
        "libraw": list(rawpy.libraw_version),
    }
    if platform.system() == "Darwin":
        for key, name in [("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes")]:
            try:
                value = subprocess.check_output(["sysctl", "-n", key], text=True, timeout=5).strip()
                result[name] = int(value) if name == "memory_bytes" else value
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return result


def _shutter_metadata(path: Path) -> dict[str, Any]:
    executable = shutil.which("exiftool")
    result: dict[str, Any] = {"shutter_mode": "unknown"}
    if executable is None:
        result["shutter_mode_source"] = "ExifTool unavailable"
        return result
    try:
        data = json.loads(
            subprocess.check_output(
                [
                    executable,
                    "-j",
                    "-Shutter",
                    "-ShutterType",
                    "-ShutterMode",
                    "-ElectronicFrontCurtainShutter",
                    "-ReleaseMode",
                    "-SequenceNumber",
                    "-SonyExposureTime#",
                    "-SonyExposureTime2#",
                    str(path.resolve()),
                ],
                text=True,
                timeout=15,
            )
        )[0]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        result["shutter_mode_source"] = "ExifTool query failed"
        return result
    data.pop("SourceFile", None)
    result["shutter_metadata"] = data
    for tag, key in [
        ("SonyExposureTime", "sony_exposure_s"),
        ("SonyExposureTime2", "sony_exposure2_s"),
    ]:
        if tag in data:
            try:
                value = float(data[tag])
                if np.isfinite(value) and value > 0:
                    result[key] = value
                    result["sony_exposure_note"] = (
                        "Diagnostic approximate Sony maker-note timing; nominal shutter_s is unchanged"
                    )
            except (TypeError, ValueError):
                pass
    explicit = data.get("Shutter") or data.get("ShutterType") or data.get("ShutterMode")
    if explicit:
        label = str(explicit)
        if "electronic" in label.lower() or "silent" in label.lower():
            result["shutter_mode"] = "electronic"
        elif "mechanical" in label.lower():
            result["shutter_mode"] = "mechanical"
        else:
            result["shutter_mode"] = label
        result["shutter_mode_source"] = "ExifTool explicit shutter tag"
    elif str(data.get("ElectronicFrontCurtainShutter", "")).lower() in {"on", "yes", "1"}:
        result["shutter_mode"] = "electronic_first_curtain"
        result["shutter_mode_source"] = "ExifTool ElectronicFrontCurtainShutter=On"
    else:
        # EFCS=Off alone does not distinguish fully mechanical from electronic.
        result["shutter_mode_source"] = "No explicit shutter-mode tag recovered"
    return result


def read_metadata(path: str | Path) -> dict[str, Any]:
    """Return the inventory.json schema, plus optional ExifTool shutter evidence."""
    path = Path(path)
    start = time.perf_counter()
    with path.open("rb") as stream:
        tags = exifread.process_file(stream, details=False)
    with rawpy.imread(str(path)) as raw:
        result = {
            "file": path.name,
            "shutter_s": float(raw.other.shutter_speed),
            "iso": float(raw.other.iso_speed),
            "aperture": float(raw.other.aperture),
            "focal_length_mm": float(raw.other.focal_length),
            "timestamp": str(raw.other.timestamp),
            "sizes": raw.sizes._asdict(),
            "camera": str(tags.get("Image Model", "unknown")),
            "orientation": str(tags.get("Image Orientation", "unknown")),
            "white_balance": list(raw.camera_whitebalance),
            "raw_pattern": raw.raw_pattern.tolist() if raw.raw_pattern is not None else None,
            "black_levels": list(raw.black_level_per_channel),
            "white_level": int(raw.white_level),
            "exif": {
                k: str(v)
                for k, v in tags.items()
                if k.startswith("EXIF") or k in {"Image Make", "Image Model", "Image Orientation"}
            },
        }
    # DNG permits TIFF-EP exposure tags in IFD0. ExifRead labels those "Image",
    # and LibRaw does not populate every other field (notably ISO) from them.
    exif_names = [
        "ExposureTime",
        "FNumber",
        "ExposureProgram",
        "ISOSpeedRatings",
        "DateTimeOriginal",
        "DateTimeDigitized",
        "OffsetTimeOriginal",
        "SubSecTimeOriginal",
        "ExposureBiasValue",
        "FocalLength",
        "FocalLengthIn35mmFilm",
        "LensModel",
        "WhiteBalance",
    ]
    for name in exif_names:
        if f"Image {name}" in tags and f"EXIF {name}" not in result["exif"]:
            result["exif"][f"EXIF {name}"] = str(tags[f"Image {name}"])
    for field, name in [
        ("iso", "ISOSpeedRatings"),
        ("shutter_s", "ExposureTime"),
        ("aperture", "FNumber"),
        ("focal_length_mm", "FocalLength"),
    ]:
        if result[field] <= 0:
            tag = result["exif"].get(f"EXIF {name}")
            if tag:
                try:
                    result[field] = float(Fraction(tag))
                except (ValueError, ZeroDivisionError):
                    pass
    result.update(_shutter_metadata(path))
    result["seconds"] = time.perf_counter() - start
    return result


def sensor_proxy(path: str | Path, block: int = 8) -> np.ndarray:
    """Black-subtracted camera RGB, one sample per block×block sensor pixels.

    This is a CFA-cell average, not a demosaiced display image. Both Bayer green
    sites are combined. The common normalization matches inspect_batch.py and
    preserves channel ratios: (white_level - mean(black_levels)). Crop, white
    balance, transfer function, and rotation are deliberately not applied.
    """
    if isinstance(block, bool) or not isinstance(block, int) or block < 2 or block % 2:
        raise ValueError("block must be a positive even integer of at least 2")
    with rawpy.imread(str(path)) as raw:
        pattern = raw.raw_pattern
        if pattern is None or pattern.shape != (2, 2):
            raise ValueError("sensor_proxy currently requires a 2×2 Bayer CFA")
        colors = raw.color_desc.decode("ascii")
        channel_names = np.array([colors[int(c)] for c in pattern.flat]).reshape(2, 2)
        if set(channel_names.flat) != {"R", "G", "B"}:
            raise ValueError("sensor_proxy requires RGB Bayer color channels")
        mosaic = raw.raw_image_visible
        height, width = mosaic.shape
        height -= height % block
        width -= width % block
        if not height or not width:
            raise ValueError("block exceeds the visible sensor dimensions")
        blacks = np.asarray(raw.black_level_per_channel, dtype=np.float32)
        scale = float(raw.white_level - np.mean(blacks))
        if scale <= 0:
            raise ValueError("White level must be greater than black level")
        cell_block = block // 2
        planes = []
        for name in "RGB":
            combined = np.zeros((height // 2, width // 2), dtype=np.float32)
            positions = np.argwhere(channel_names == name)
            for y, x in positions:
                plane = mosaic[int(y) : height : 2, int(x) : width : 2].astype(np.float32)
                plane -= blacks[int(pattern[y, x])]
                np.maximum(plane, 0, out=plane)
                combined += plane
            combined /= len(positions)
            combined /= scale
            planes.append(
                combined.reshape(height // block, cell_block, width // block, cell_block).mean(
                    axis=(1, 3)
                )
            )
        return np.stack(planes, axis=-1).astype(np.float32, copy=False)


def linear_to_srgb(linear: np.ndarray) -> np.ndarray:
    """IEC sRGB transfer function, clipped to representable display RGB."""
    linear = np.clip(np.asarray(linear, dtype=np.float32), 0, 1)
    return np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.power(linear, 1 / 2.4) - 0.055)


def _crop_box(sizes: Any, rendered_width: int, rendered_height: int) -> tuple[int, int, int, int]:
    width, height = int(sizes.width), int(sizes.height)
    x, y = int(getattr(sizes, "crop_left_margin", 0)), int(getattr(sizes, "crop_top_margin", 0))
    crop_w, crop_h = int(getattr(sizes, "crop_width", 0)), int(getattr(sizes, "crop_height", 0))
    if not (
        crop_w > 0
        and crop_h > 0
        and x >= 0
        and y >= 0
        and x + crop_w <= width
        and y + crop_h <= height
    ):
        return (0, 0, rendered_width, rendered_height)
    sx, sy = rendered_width / width, rendered_height / height
    return tuple(int(round(v)) for v in (x * sx, y * sy, (x + crop_w) * sx, (y + crop_h) * sy))


def _orient(image: Image.Image, flip: int) -> Image.Image:
    operation = {
        1: Image.Transpose.FLIP_LEFT_RIGHT,
        2: Image.Transpose.FLIP_TOP_BOTTOM,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.TRANSPOSE,
        5: Image.Transpose.ROTATE_90,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
    }.get(flip)
    return image.transpose(operation) if operation is not None else image


def render_preview(
    path: str | Path,
    dest: str | Path,
    *,
    white_balance=None,
    exposure: float = 1.0,
    max_dimension: int = 1800,
    full_resolution: bool = False,
) -> dict[str, Any]:
    """Render RAW consistently through linear16 → exact sRGB → ICC JPEG/PNG.

    exposure is a fixed linear multiplier, not EV. Supply a common four-element
    camera white balance to compare several files. With None, each file's camera
    WB is used. Full-resolution output bypasses max_dimension. No auto exposure,
    auto WB, saturation recovery, denoising, sharpening, or camera look is applied.
    The default crop is applied in unrotated coordinates, then orientation.
    """
    path, dest = Path(path), Path(dest)
    if path.resolve() == dest.resolve():
        raise ValueError("Rendering must never overwrite the source RAW")
    if dest.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("render_preview output must be JPEG or PNG")
    if not np.isfinite(exposure) or exposure <= 0:
        raise ValueError("exposure must be a finite positive multiplier")
    if max_dimension < 1:
        raise ValueError("max_dimension must be positive")
    start = time.perf_counter()
    with rawpy.imread(str(path)) as raw:
        wb = np.asarray(
            raw.camera_whitebalance if white_balance is None else white_balance, dtype=float
        )
        if wb.shape == (3,):
            wb = np.append(wb, wb[1])
        if wb.shape == (4,) and wb[3] == 0:
            # DNG AsShotNeutral has three channels; LibRaw reports a zero fourth
            # WB slot even for a Bayer mosaic with two green sites.
            wb[3] = wb[1]
        if wb.shape != (4,) or np.any(wb <= 0) or not np.all(np.isfinite(wb)):
            raise ValueError("white_balance must contain three or four finite positive values")
        sizes, flip = raw.sizes, int(raw.sizes.flip)
        linear16 = raw.postprocess(
            half_size=not full_resolution,
            user_flip=0,
            use_camera_wb=False,
            use_auto_wb=False,
            user_wb=wb.tolist(),
            output_color=rawpy.ColorSpace.sRGB,
            output_bps=16,
            gamma=(1, 1),
            no_auto_bright=True,
            adjust_maximum_thr=0,
            bright=1.0,
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    crop = _crop_box(sizes, linear16.shape[1], linear16.shape[0])
    # A 65536-entry lookup applies the exact transfer to each linear16 code with
    # bounded memory, avoiding several full-resolution float RGB intermediates.
    lut = np.rint(
        linear_to_srgb(np.arange(65536, dtype=np.float32) * (exposure / 65535.0)) * 255
    ).astype(np.uint8)
    image = Image.fromarray(lut[linear16[crop[1] : crop[3], crop[0] : crop[2]]])
    del linear16
    image = _orient(image, flip)
    if not full_resolution:
        image.thumbnail((int(max_dimension), int(max_dimension)), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    save_options = {"icc_profile": icc}
    if dest.suffix.lower() in {".jpg", ".jpeg"}:
        save_options.update(quality=95, subsampling=0)
    image.save(dest, **save_options)
    return {
        "path": str(dest),
        "source": str(path),
        "width": image.width,
        "height": image.height,
        "white_balance": wb.tolist(),
        "exposure_multiplier": float(exposure),
        "full_resolution": full_resolution,
        "half_size_demosaic": not full_resolution,
        "unrotated_render_crop": list(crop),
        "orientation_flip": flip,
        "transfer": "exact sRGB from linear 16-bit RGB",
        "icc_profile": "sRGB",
        "no_auto_bright": True,
        "seconds": time.perf_counter() - start,
    }
