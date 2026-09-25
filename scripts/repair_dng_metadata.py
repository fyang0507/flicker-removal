"""Repair EXIF placement in existing pipeline DNGs without changing RAW pixels.

Apple's RAW decoder ignores ISO stored directly in the primary TIFF directory.
This script moves the original EXIF entries into ExifIFD. It preserves their
values, all color settings, correction provenance, and the corrected Bayer array.
Files are verified before atomic publication to a new output directory.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

import numpy as np
import rawpy
import tifffile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from flicker_removal.dng import _place_exif_ifd

# The standard EXIF fields emitted by this pipeline's original DNG writer.
EXIF_CODES = {
    33434,
    33437,
    34850,
    34855,
    36867,
    36868,
    36881,
    36882,
    37377,
    37378,
    37380,
    37383,
    37386,
    37521,
    37522,
    41987,
    41989,
    42036,
}
EXIF_POINTER = 34665


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_directories(path: Path) -> tuple[dict[int, bytes], dict[int, bytes]]:
    """Read classic little-endian TIFF entries as exact bytes, including offsets."""
    with path.open("rb") as stream:
        header = stream.read(8)
        if header[:4] != b"II*\0":
            raise ValueError(f"Expected a pipeline little-endian classic TIFF: {path}")

        def directory(offset: int) -> dict[int, bytes]:
            stream.seek(offset)
            count = struct.unpack("<H", stream.read(2))[0]
            entries = [stream.read(12) for _ in range(count)]
            if any(len(entry) != 12 for entry in entries):
                raise ValueError(f"Truncated TIFF directory: {path}")
            return {struct.unpack("<H", entry[:2])[0]: entry for entry in entries}

        main = directory(struct.unpack("<I", header[4:])[0])
        exif = (
            directory(struct.unpack("<I", main[EXIF_POINTER][8:])[0])
            if EXIF_POINTER in main
            else {}
        )
    return main, exif


def verify(source: Path, target: Path, codes: set[int]) -> dict:
    source_main, _ = read_directories(source)
    target_main, target_exif = read_directories(target)
    if target_exif != {code: source_main[code] for code in codes}:
        raise ValueError("EXIF entries changed during relocation")
    if {code: value for code, value in source_main.items() if code not in codes} != {
        code: value for code, value in target_main.items() if code != EXIF_POINTER
    }:
        raise ValueError("Non-EXIF TIFF entries changed")
    # Relocation only appends directories and replaces bytes 4..7 of the TIFF
    # header. Compare the entire original payload to guarantee every original
    # sample, metadata value, color setting, and provenance byte is preserved.
    with source.open("rb") as before, target.open("rb") as after:
        if before.read(4) != after.read(4):
            raise ValueError("TIFF format changed")
        before.seek(8)
        after.seek(8)
        while block := before.read(1024 * 1024):
            if block != after.read(len(block)):
                raise ValueError("An original payload byte changed")
    with rawpy.imread(str(source)) as original, rawpy.imread(str(target)) as copied:
        if not np.array_equal(original.raw_image, copied.raw_image):
            raise ValueError("Corrected Bayer samples changed")
        if original.sizes != copied.sizes:
            raise ValueError("Sensor dimensions, orientation, or crop changed")
        if original.black_level_per_channel != copied.black_level_per_channel:
            raise ValueError("Black levels changed")
        if original.white_level != copied.white_level:
            raise ValueError("White level changed")
        if not np.array_equal(original.camera_whitebalance, copied.camera_whitebalance):
            raise ValueError("White balance changed")
        if not np.array_equal(original.raw_pattern, copied.raw_pattern):
            raise ValueError("Bayer pattern changed")
        bayer_hash = hashlib.sha256(copied.raw_image.tobytes()).hexdigest()
    with tifffile.TiffFile(target) as image:
        exif_values = image.pages[0].tags[EXIF_POINTER].value
        iso = exif_values.get("ISOSpeedRatings")
    return {
        "moved_exif_tags": sorted(codes),
        "exif_entries_byte_exact": True,
        "original_payload_byte_exact": True,
        "bayer_exact_match": True,
        "bayer_sha256": bayer_hash,
        "white_balance_black_white_crop_preserved": True,
        "color_settings_and_provenance_preserved": True,
        "exif_iso": iso,
    }


def publish_directory(staging: Path, destination: Path) -> None:
    """Atomic rename with OS-enforced refusal to replace any existing path."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(staging), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
    else:
        raise RuntimeError("Atomic publication without replacement requires macOS or Linux")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def repair(input_path: Path, output_path: Path) -> dict:
    source = input_path.expanduser().resolve(strict=True)
    output = output_path.expanduser().resolve()
    if output.exists() or output_path.is_symlink():
        raise FileExistsError(f"Output directory already exists: {output}")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Input and output paths must not overlap")
    if source.is_dir():
        sources = sorted(
            path.resolve()
            for path in source.iterdir()
            if path.is_file() and path.suffix.lower() == ".dng"
        )
    elif source.is_file() and source.suffix.lower() == ".dng":
        sources = [source]
    else:
        raise ValueError("Input must be one DNG or a directory containing DNG files")
    if not sources:
        raise ValueError("No DNG files found")
    if len({path.name.casefold() for path in sources}) != len(sources):
        raise ValueError("Input filenames are not unique ignoring case")
    records = []
    for path in sources:
        main, exif = read_directories(path)
        if EXIF_POINTER in main or exif:
            raise ValueError(f"DNG already has an ExifIFD; no repair attempted: {path}")
        codes = EXIF_CODES & main.keys()
        if 34855 not in codes:
            raise ValueError(f"Expected the original writer's misplaced ISO tag: {path}")
        records.append((path, codes))
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "operation": "relocate original EXIF entries to ExifIFD",
        "color_profile_changed": False,
        "baseline_exposure_changed": False,
        "output_directory": str(output),
        "files": [],
    }
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as directory:
        staging = Path(directory)
        for path, codes in records:
            source_hash = digest(path)
            target = staging / path.name
            shutil.copy2(path, target)
            _place_exif_ifd(target, codes)
            validation = verify(path, target, codes)
            if digest(path) != source_hash:
                raise ValueError(f"Source DNG changed during processing: {path}")
            manifest["files"].append(
                {
                    "source": str(path),
                    "source_sha256": source_hash,
                    "output": str(output / path.name),
                    "output_sha256": digest(target),
                    "bytes": target.stat().st_size,
                    "validation": validation,
                }
            )
            print(f"Verified {path.name}", flush=True)
        (staging / "repair-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        publish_directory(staging, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="One pipeline DNG or a directory of DNGs")
    parser.add_argument("output", type=Path, help="New output directory; must not exist")
    args = parser.parse_args()
    result = repair(args.input, args.output)
    print(f"Published {len(result['files'])} verified DNG(s) to {args.output}")


if __name__ == "__main__":
    main()
