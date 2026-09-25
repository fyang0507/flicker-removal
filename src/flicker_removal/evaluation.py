"""Reproducible delivery integrity checks, separate from image-quality claims."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile

import exifread
import numpy as np
import rawpy
import tifffile
from PIL import Image

from . import __version__
from .dng import _apply_gain, _gain_array, validate_dng
from .io import discover_inputs, source_hash

LIMITATION = (
    "Row scores are estimator diagnostics, not clean-reference quality scores or "
    "percentages of flicker removed. RAW integrity does not establish correct "
    "illumination estimates; review the matched previews and held-out photos."
)

PRESERVED_EXIF = (
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
)


def _finite_number(value, description: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Expected a finite number for {description}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"Expected a finite number for {description}")


def _check_finite_json(value) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Batch report contains nonfinite values")
    if isinstance(value, dict):
        for child in value.values():
            _check_finite_json(child)
    elif isinstance(value, list):
        for child in value:
            _check_finite_json(child)


def _load_run(root: Path) -> tuple[dict, dict[str, dict]]:
    summary = json.loads((root / "batch-report.json").read_text())
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != 1
        or not isinstance(summary.get("reports"), list)
        or not summary["reports"]
    ):
        raise ValueError("Expected a nonempty schema-1 batch report")
    _check_finite_json(summary)
    wb = summary.get("preview_white_balance")
    if not isinstance(wb, list) or len(wb) not in (3, 4):
        raise ValueError("Expected three or four preview white-balance values")
    for index, value in enumerate(wb):
        _finite_number(value, "preview white balance")
        # LibRaw uses zero for an absent second green slot in some Bayer DNGs.
        # render_preview substitutes the first green value for that slot.
        if value < 0 or (index < 3 and value == 0):
            raise ValueError("Preview RGB white balance must be positive")
    exposure = summary.get("preview_exposure_multiplier")
    _finite_number(exposure, "preview exposure multiplier")
    if exposure <= 0:
        raise ValueError("Preview exposure multiplier must be positive")
    rows = {}
    for row in summary["reports"]:
        if not isinstance(row, dict):
            raise ValueError("Each batch report entry must be an object")
        name = row.get("file")
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or Path(name).name != name
            or "\\" in name
        ):
            raise ValueError("Batch reports must contain plain source filenames")
        if name.casefold() in {key.casefold() for key in rows}:
            raise ValueError(f"Duplicate source filename: {name}")
        if not isinstance(row.get("status"), str) or not row["status"]:
            raise ValueError(f"Missing processing status: {name}")
        dng = row.get("dng")
        digest = dng.get("source_sha256") if isinstance(dng, dict) else None
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError(f"Invalid source SHA256: {name}")
        for stage in ("before", "after"):
            scores = row.get(stage)
            if not isinstance(scores, dict):
                raise ValueError(f"Missing {stage} row scores: {name}")
            for channel in ("luminance", "chroma"):
                value = scores.get(channel)
                _finite_number(value, f"{name} {stage} {channel}")
                if value < 0:
                    raise ValueError(f"Negative row score: {name} {stage} {channel}")
        if not isinstance(row.get("review_reasons", []), list) or any(
            not isinstance(reason, str) for reason in row.get("review_reasons", [])
        ):
            raise ValueError(f"Review reasons must be a list of strings: {name}")
        rows[name] = row
    stems = [Path(name).stem.casefold() for name in rows]
    if len(set(stems)) != len(stems):
        raise ValueError("Source stems collide in batch outputs")
    return summary, rows


def _exif_values(path: Path) -> dict:
    with path.open("rb") as stream:
        tags = exifread.process_file(stream, details=False)
    values = {}
    for name in PRESERVED_EXIF:
        tag = tags.get(f"EXIF {name}") or tags.get(f"Image {name}")
        if tag is not None:
            values[name] = (
                tag.values
                if isinstance(tag.values, str)
                else [
                    (value.num, value.den) if hasattr(value, "num") else value
                    for value in tag.values
                ]
            )
    return values


def _verify_file(source: Path, root: Path, row: dict) -> dict:
    stem = source.stem
    expected_source_hash = row["dng"]["source_sha256"]
    if source_hash(source) != expected_source_hash:
        raise ValueError("Source hash differs from the processing record")
    gain_path = root / "gains" / f"{stem}.npy"
    gains = _gain_array(np.load(gain_path, allow_pickle=False))
    destination = root / "corrected-raw" / f"{stem}_deflicker.dng"
    with rawpy.imread(str(source)) as original:
        expected, _ = _apply_gain(original, gains)
        shape = original.raw_image.shape
        wb = np.asarray(original.camera_whitebalance[:3])
        black = original.black_level_per_channel
        white = original.white_level
        sizes = original.sizes
        matrix = original.rgb_xyz_matrix[:3, :3].copy()
    verification = validate_dng(destination, expected_bayer=expected, expected_shape=shape)
    with rawpy.imread(str(destination)) as corrected:
        if corrected.sizes != sizes:
            raise ValueError("RAW dimensions, crop or orientation differ from source")
        if corrected.black_level_per_channel != black or corrected.white_level != white:
            raise ValueError("Black or white levels differ from source")
        restored_wb = np.asarray(corrected.camera_whitebalance[:3])
        if not np.allclose(restored_wb / restored_wb[1], wb / wb[1], atol=1e-5):
            raise ValueError("As-shot white balance differs from source")
        if not np.allclose(corrected.rgb_xyz_matrix[:3, :3], matrix, rtol=1e-5, atol=1e-6):
            raise ValueError("Camera color matrix differs from source")
    source_exif, output_exif = _exif_values(source), _exif_values(destination)
    for name, value in source_exif.items():
        if name not in output_exif or output_exif[name] != value:
            raise ValueError(f"EXIF {name} is missing or differs from source")
    with tifffile.TiffFile(destination) as image:
        page = image.pages[0]
        provenance = json.loads(page.description)
        if provenance["source_sha256"] != expected_source_hash:
            raise ValueError("DNG provenance disagrees with the source hash")
        exif = page.tags.get(34665)
        has_exif_iso = exif is not None and "ISOSpeedRatings" in exif.value
    previews = {}
    for kind in ("neutral-original", "physics"):
        path = root / "previews" / kind / f"{stem}.jpg"
        with Image.open(path) as image:
            image.load()
            if not image.info.get("icc_profile"):
                raise ValueError(f"Missing color profile in {kind} preview")
            previews[kind] = list(image.size)
    if previews["neutral-original"] != previews["physics"]:
        raise ValueError("Original and corrected preview dimensions differ")
    full = root / "corrected-jpeg" / f"{stem}_deflicker.jpg"
    if full.exists():
        with Image.open(full) as image:
            image.load()
            if not image.info.get("icc_profile"):
                raise ValueError("Missing color profile in full-resolution JPEG")
            previews["full_jpeg"] = list(image.size)
    return {
        "source_sha256": expected_source_hash,
        "dng_sha256": source_hash(destination),
        "gain_sha256": source_hash(gain_path),
        "bayer_matches_source_and_saved_gains": verification["bayer_exact_match"],
        "raw_decode_metadata_preserved": True,
        "exposure_metadata_preserved": True,
        "preview_exif_iso_present": has_exif_iso,
        "compatibility_warnings": (
            ["legacy_iso_location"] if "ISOSpeedRatings" in source_exif and not has_exif_iso else []
        ),
        "preview_dimensions": previews,
    }


def _comparison(candidate: dict, baseline: dict, rows: dict, previous: dict) -> dict:
    if rows.keys() != previous.keys():
        raise ValueError("Comparison batches must contain the same source filenames")
    for key in ("preview_white_balance", "preview_exposure_multiplier"):
        if (
            key not in candidate
            or key not in baseline
            or np.shape(candidate[key]) != np.shape(baseline[key])
            or not np.allclose(candidate[key], baseline[key], rtol=1e-9, atol=1e-9)
        ):
            raise ValueError(f"Comparison preview settings differ: {key}")
    differences = []
    for name, row in rows.items():
        other = previous[name]
        if row["dng"]["source_sha256"] != other["dng"]["source_sha256"]:
            raise ValueError(f"Comparison source hashes differ: {name}")
        differences.append(
            {
                "file": name,
                "baseline_status": other["status"],
                "candidate_status": row["status"],
                "luminance_row_score_delta": row["after"]["luminance"]
                - other["after"]["luminance"],
                "chroma_row_score_delta": row["after"]["chroma"] - other["after"]["chroma"],
            }
        )
    return {
        "matched_source_hashes_and_render_settings": True,
        "interpretation": "Negative deltas mean lower reported row scores, not proof of better photographs.",
        "files": differences,
    }


def evaluate_run(
    run: str | Path, sources: str | Path, *, compare: str | Path | None = None
) -> dict:
    """Read and audit a batch; never modify inputs or existing outputs.

    Reconstruct expected corrected sensor values from each original and its saved
    gain map, then independently reopen the DNG. Missing or changed assets fail
    the audit. Source folders may be relocated; report-embedded host paths are
    never used to find source files.
    """
    root = Path(run)
    summary, rows = _load_run(root)
    inputs = discover_inputs(sources)
    by_name = {path.name: path for path in inputs}
    if len(by_name) != len(inputs):
        raise ValueError("Source filenames must be unique across the input folder")
    if set(rows) != set(by_name):
        raise ValueError("Source folder must match the batch's photo set exactly")
    baseline = previous = None
    if compare is not None:
        baseline, previous = _load_run(Path(compare))
        comparison = _comparison(summary, baseline, rows, previous)
    files = []
    for name, row in rows.items():
        result = {
            "file": name,
            "status": row["status"],
            "reported_row_scores": {"before": row["before"], "after": row["after"]},
            "review_reasons": row.get("review_reasons", []),
        }
        try:
            result["validation"] = _verify_file(by_name[name], root, row)
            if previous is not None:
                result["baseline_validation"] = _verify_file(
                    by_name[name], Path(compare), previous[name]
                )
            result["integrity"] = "passed"
        except (ValueError, OSError, KeyError, rawpy.LibRawError) as exc:
            result["integrity"] = "failed"
            result["error"] = str(exc)
        files.append(result)
    output = {
        "schema_version": 1,
        "software_version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "integrity_passed": all(row["integrity"] == "passed" for row in files),
        "file_count": len(files),
        "status_counts": dict(Counter(row["status"] for row in files)),
        "profile_sha256": summary.get("profile_sha256"),
        "scene_profile_sha256": summary.get("scene_profile_sha256"),
        "limitation": LIMITATION,
        "files": files,
    }
    if compare is not None:
        output["comparison"] = comparison
    return output


def evaluate_command(args) -> int:
    destination = Path(args.report)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    report = evaluate_run(args.run, args.sources, compare=args.compare)
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
        # Linking publishes a complete report atomically and still refuses a
        # destination created after the initial existence check.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    print(
        f"Integrity {'passed' if report['integrity_passed'] else 'FAILED'}: {report['file_count']} files → {destination}"
    )
    print(LIMITATION)
    return 0 if report["integrity_passed"] else 1
