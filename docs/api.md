# Python API

The command-line interface is the primary workflow. The small module-level API below supports scripts and notebooks; version 0.1.x remains experimental, so pin a version and retain the saved profile and processing records.

## Calibrate and correct

```python
from flicker_removal.io import discover_inputs, read_metadata, sensor_proxy
from flicker_removal.model import LightingProfile, calibrate, estimate_gain
from flicker_removal.dng import write_corrected_dng

paths = discover_inputs("data/session-02/raw")
metadata = [read_metadata(path) for path in paths]
proxies = [sensor_proxy(path) for path in paths]
profile, diagnostics = calibrate(proxies, metadata)
profile.save("data/session-02/lighting.json")

profile = LightingProfile.load("data/session-02/lighting.json")
gains, fit = estimate_gain(proxies[0], profile, metadata[0]["shutter_s"])
result = write_corrected_dng(
    paths[0],
    "outputs/example.dng",
    gains,
    metadata={"fit": fit},
)
assert result["validation"]["bayer_exact_match"]
```

Create the profile's parent directory first. Calibration requires at least three coherent inputs sharing camera, geometry and shutter mode. `sensor_proxy` returns black-subtracted linear camera RGB in unrotated sensor coordinates; do not substitute a JPEG or rotated display RGB image. The gain grid uses that same coordinate system. Both green Bayer sites share a gain.

`estimate_gain` returns a gain map and diagnostic dictionary. Inspect `status`, `reasons`, exposure calibration and gain limits. An unchanged result is an abstention, not a clean-photo certification. The CLI performs camera/geometry/shutter compatibility checks for new inputs; callers using these low-level functions must do the same.

`write_corrected_dng` defaults to no overwrite and independent TIFF/LibRaw verification. It never changes the source, its sidecar, or its white-balance settings. See [RAW format](raw-format.md) for gain shapes, crop handling and metadata boundaries.

## Audit a completed batch

```python
from flicker_removal.evaluation import evaluate_run

report = evaluate_run(
    "outputs/session-02/scene",
    "data/session-02/raw",
    compare="outputs/session-02/base",  # optional
)
assert report["integrity_passed"]
```

This reads original RAWs, saved gains, DNGs and previews. It reconstructs intended corrected sensor values and checks the actual exported arrays, source hashes and relevant metadata. A comparison requires matching source hashes and preview settings and audits both runs. It does not infer a clean reference or rank photographic quality automatically. CLI `evaluate` writes a new report and exits nonzero if integrity fails.

## Optional reference archive

`flicker_removal.session.SceneReference` implements the experimental scene method. Install the `scene` extra and prefer the `calibrate-scene`/`batch --scene-profile` commands, which bind the archive to a lighting profile and manage target exclusion. Archives include private image proxies. See [architecture](architecture.md) and [evaluation](evaluation.md) before using a reference for held-out testing.
