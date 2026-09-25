"""Local, reproducible batch commands. Originals are always read-only inputs."""

from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import resource
import time
import numpy as np
from . import __version__
from .model import LightingProfile, calibrate, estimate_gain, banding_score
from .dng import write_corrected_dng


def _json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _sources(folder):
    from .io import discover_inputs

    paths = discover_inputs(folder)
    if not paths:
        raise ValueError(f"No ARW or DNG inputs in {folder}")
    return paths


def _preflight_destinations(paths, root, overwrite=False):
    """Never overwrite any source, including another file in the same batch."""
    sources = {p.resolve() for p in paths}
    destinations = [(root / "corrected-raw" / f"{p.stem}_deflicker.dng").resolve() for p in paths]
    # Case-insensitive names must also remain unique on ordinary macOS volumes.
    keys = [str(p).casefold() for p in destinations]
    if len(set(keys)) != len(keys):
        raise ValueError("Input stems collide; ARW/DNG files must have distinct names")
    source_keys = {str(p).casefold() for p in sources}
    if source_keys.intersection(keys):
        raise ValueError("An output would overwrite an input RAW; choose a separate output folder")
    for dest in destinations:
        if dest.exists() and not overwrite:
            raise FileExistsError(f"{dest}; choose new output or explicitly pass --overwrite")


def _preflight_profile(paths, dest, overwrite=False):
    if dest.suffix.lower() != ".json":
        raise ValueError("Lighting profile destination must have a .json extension")
    source_keys = {str(p.resolve()).casefold() for p in paths}
    outputs = [dest, dest.with_suffix(".calibration.json")]
    for output in outputs:
        if str(output.resolve()).casefold() in source_keys:
            raise ValueError("Profile output would overwrite an input RAW")
        if output.exists() and not overwrite:
            raise FileExistsError(output)


def make_profile(args):
    from .io import read_metadata, sensor_proxy

    paths = _sources(args.input)
    _preflight_profile(paths, Path(args.profile), args.overwrite)
    started = time.perf_counter()
    metadata = []
    images = []
    for p in paths:
        metadata.append(read_metadata(p))
        images.append(sensor_proxy(p))
    if args.shutter_mode:
        for m in metadata:
            m["shutter_mode"] = args.shutter_mode
    profile, diagnostics = calibrate(images, metadata, known_frequency_hz=args.measured_frequency)
    dest = Path(args.profile)
    dest.parent.mkdir(parents=True, exist_ok=True)
    profile.save(dest)
    diagnostics["calibration_total_seconds"] = time.perf_counter() - started
    diagnostics["source_metadata"] = metadata
    _json(dest.with_suffix(".calibration.json"), diagnostics)
    print(
        f"Calibrated {len(paths)} frames: {profile.cycles_per_sensor_height:.4f} cycles per sensor height → {dest}"
    )


def _check_metadata(paths, metadata, profile):
    for p, m in zip(paths, metadata):
        if (m["camera"], m["sizes"]["height"], m["sizes"]["width"]) != (
            profile.camera,
            profile.sensor_height,
            profile.sensor_width,
        ):
            raise ValueError(f"{p.name}: incompatible camera or sensor geometry")
        mode = m.get("shutter_mode", "unknown")
        if mode != "unknown" and profile.shutter_mode != "unknown" and mode != profile.shutter_mode:
            raise ValueError(f"{p.name}: shutter mode differs from profile")


def make_scene(args):
    from .io import read_metadata, sensor_proxy
    from .session import SceneReference

    paths = _sources(args.input)
    dest = Path(args.scene_profile)
    if dest.suffix.lower() != ".npz":
        raise ValueError("Scene profile must have an .npz extension")
    if dest.exists():
        raise FileExistsError(dest)
    if dest.with_suffix(".json").exists():
        raise FileExistsError(dest.with_suffix(".json"))
    profile = LightingProfile.load(args.profile)
    started = time.perf_counter()
    metadata = [read_metadata(p) for p in paths]
    _check_metadata(paths, metadata, profile)
    images = []
    gains = []
    reports = []
    for p, m in zip(paths, metadata):
        image = sensor_proxy(p)
        gain, report = estimate_gain(image, profile, m["shutter_s"])
        images.append(image)
        gains.append(gain)
        reports.append(report)
        print(f"Scene proxy: {p.name}", flush=True)
    scene = SceneReference.build(images, metadata, gains, reports, profile)
    dest.parent.mkdir(parents=True, exist_ok=True)
    scene.save(dest)
    _json(
        dest.with_suffix(".json"),
        {**scene.metadata, "calibration_seconds": time.perf_counter() - started},
    )
    print(
        f"Saved scene reference with {len(scene.stack)} registered photographs → {dest}", flush=True
    )


def batch(args):
    from .io import read_metadata, sensor_proxy, render_preview

    profile = LightingProfile.load(args.profile)
    paths = _sources(args.input)
    root = Path(args.output)
    source_root = Path(args.input).resolve()
    if root.resolve() == source_root or (
        source_root.is_dir() and source_root in root.resolve().parents
    ):
        raise ValueError("Output directory must be outside the source directory")
    if not np.isfinite(args.exposure) or args.exposure <= 0:
        raise ValueError("Preview exposure multiplier must be positive and finite")
    _preflight_destinations(paths, root, args.overwrite)
    # Preflight all metadata and destinations before producing partial outputs.
    metadata = [read_metadata(p) for p in paths]
    _check_metadata(paths, metadata, profile)
    scene = None
    scene_hash = None
    if getattr(args, "scene_profile", None):
        from .session import SceneReference
        from .io import source_hash

        scene = SceneReference.load(args.scene_profile, profile)
        scene_hash = source_hash(args.scene_profile)
    for folder in [
        "corrected-raw",
        "previews/neutral-original",
        "previews/physics",
        "gains",
        "reports",
    ]:
        (root / folder).mkdir(parents=True, exist_ok=True)
    wb = np.median([m["white_balance"] for m in metadata], axis=0).tolist()
    profile_hash = hashlib.sha256(Path(args.profile).read_bytes()).hexdigest()
    start = time.perf_counter()
    reports = []
    for i, (p, m) in enumerate(zip(paths, metadata)):
        one_start = time.perf_counter()
        image = sensor_proxy(p)
        gains, report = estimate_gain(image, profile, m["shutter_s"])
        if scene is not None:
            baseline_status = report["status"]
            baseline_after = report["after"]
            gains, refinement = scene.polish(image, gains, report, profile, source_name=p.name)
            report["scene_refinement"] = {
                **refinement,
                "baseline_status": baseline_status,
                "baseline_after": baseline_after,
                "scene_profile_sha256": scene_hash,
            }
            if refinement["status"] == "polished":
                report["status"] = "corrected"
                report["reasons"] = []
                report["after"] = banding_score(image * gains, profile.cycles_per_sensor_height)
                report["gain_min"] = float(gains.min())
                report["gain_max"] = float(gains.max())
                report["log_gain_bound_fraction"] = float(
                    np.mean(abs(np.log(gains)) >= profile.max_log_gain - 1e-6)
                )
        report["file"] = p.name
        report["source_metadata"] = m
        np.save(root / "gains" / f"{p.stem}.npy", gains)
        dest = root / "corrected-raw" / f"{p.stem}_deflicker.dng"
        t = time.perf_counter()
        dng = write_corrected_dng(
            p,
            dest,
            gains,
            metadata={"profile_sha256": profile_hash, "fit": report},
            overwrite=args.overwrite,
        )
        report["dng_write_verify_seconds"] = time.perf_counter() - t
        report["dng"] = dng
        t = time.perf_counter()
        render_preview(
            p,
            root / "previews" / "neutral-original" / f"{p.stem}.jpg",
            white_balance=wb,
            exposure=args.exposure,
            max_dimension=1800,
        )
        render_preview(
            dest,
            root / "previews" / "physics" / f"{p.stem}.jpg",
            white_balance=wb,
            exposure=args.exposure,
            max_dimension=1800,
        )
        if args.full_jpeg:
            (root / "corrected-jpeg").mkdir(exist_ok=True)
            render_preview(
                dest,
                root / "corrected-jpeg" / f"{p.stem}_deflicker.jpg",
                white_balance=wb,
                exposure=args.exposure,
                full_resolution=True,
            )
        report["render_seconds"] = time.perf_counter() - t
        report["total_seconds"] = time.perf_counter() - one_start
        report["review_reasons"] = []
        if m.get("shutter_mode", "unknown") == "unknown" or profile.shutter_mode == "unknown":
            report["review_reasons"].append("shutter_mode_unverified")
        if not report["calibrated_exposure"]:
            report["review_reasons"].append("uncalibrated_exposure")
        if report["after"]["luminance"] > 0.025:
            report["review_reasons"].append("elevated_residual_row_score")
        if report["log_gain_bound_fraction"] > 0.001:
            report["review_reasons"].append("gain_bound_reached")
        if dng.get("newly_above_white_fraction", 0) > 0.001:
            report["review_reasons"].append("check_highlights")
        reports.append(report)
        _json(root / "reports" / f"{p.stem}.json", report)
        print(
            f"[{i + 1}/{len(paths)}] {p.name}: {report['status']}; row score {report['before']['luminance']:.4f} → {report['after']['luminance']:.4f}; {report['total_seconds']:.2f}s",
            flush=True,
        )
    summary = {
        "schema_version": 1,
        "software_version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "implementation_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob("*.py"))
        },
        "profile": asdict(profile),
        "profile_sha256": profile_hash,
        "preview_white_balance": wb,
        "preview_exposure_multiplier": args.exposure,
        "total_seconds": time.perf_counter() - start,
        "platform": platform.platform(),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "scene_profile_sha256": scene_hash,
        "reports": reports,
    }
    _json(root / "batch-report.json", summary)
    from .report import write_gallery

    write_gallery(root, summary)
    print(f"Finished. Comparison gallery: {root / 'comparison.html'}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="flicker-removal",
        description="Deterministic session-calibrated RAW flicker correction",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "calibrate", help="Learn shared row wavelength and RGB modulation from one lighting session"
    )
    p.add_argument("input")
    p.add_argument("--profile", required=True)
    p.add_argument("--shutter-mode")
    p.add_argument(
        "--measured-frequency",
        type=float,
        help="Only supply a separately measured lamp frequency in Hz",
    )
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=make_profile)
    p = sub.add_parser(
        "calibrate-scene",
        help="Learn an optional repeated-scene reference for residual illumination polishing",
    )
    p.add_argument("input")
    p.add_argument("--profile", required=True)
    p.add_argument("--scene-profile", required=True)
    p.set_defaults(func=make_scene)
    p = sub.add_parser("batch", help="Apply a saved profile, write verified DNGs and previews")
    p.add_argument("input")
    p.add_argument("--profile", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--full-jpeg", action="store_true")
    p.add_argument(
        "--scene-profile",
        help="Optional scene archive from calibrate-scene; unrelated views fall back to base correction",
    )
    p.add_argument(
        "--exposure",
        type=float,
        default=1.0,
        help="Fixed preview exposure multiplier; does not alter DNG gains",
    )
    p.set_defaults(func=batch)
    p = sub.add_parser(
        "evaluate",
        help="Verify a delivery against source RAWs and saved gains; summarize diagnostics",
    )
    p.add_argument("run", type=Path)
    p.add_argument("--sources", type=Path, required=True)
    p.add_argument(
        "--report",
        type=Path,
        required=True,
        help="New JSON report; existing files are never overwritten",
    )
    p.add_argument(
        "--compare",
        type=Path,
        help="Optional baseline batch with the same source files and rendering settings",
    )
    from .evaluation import evaluate_command

    p.set_defaults(func=evaluate_command)
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
