# Architecture

The maintained pipeline estimates a low-dimensional illumination gain and applies it to original Bayer sensor samples. It operates locally on CPU. Scene refinement is an optional extension with an OpenCV dependency.

## Data flow

```text
original ARW / Bayer DNG + metadata
              │
              ├── sensor-linear proxy ── shared lighting calibration
              │                                  │
              │                          per-frame gain fit
              │                                  │
              │                     optional scene residual fit
              │                                  │
              └── original Bayer samples × bounded gain
                                                 │
                                     verified CFA DNG + report
                                                 │
                                     matched neutral previews
```

Only analysis proxies are averaged or registered. The full-resolution RAW samples are not spatially interpolated, warped, denoised, sharpened, or fused. Gain interpolation does not imply interpolation of image detail.

## Modules and responsibilities

| Module | Responsibility |
|---|---|
| `io.py` | Read-only metadata/RAW ingestion, camera-RGB proxy construction, consistent preview rendering |
| `model.py` | Lighting-profile schema, shared calibration, robust per-frame estimation, acceptance gates and row diagnostics |
| `session.py` | Optional scene archive, background registration, target exclusion, residual fitting and holdout checks |
| `dng.py` | Black-subtracted CFA gain application, metadata encoding, DNG verification and safe publication |
| `evaluation.py` | Reconstruct expected samples from originals and saved gains, verify delivery integrity, compare compatible run reports |
| `cli.py` | Command orchestration, source/profile compatibility checks, destination protection and batch reports |
| `report.py` | Local comparison gallery and links to generated evidence |

## Shared profile and per-photo fit

A JSON lighting profile stores camera geometry, shutter mode, cycles per sensor height, shared RGB modulation ratios, observed exposure groups, and correction thresholds. It belongs to a compatible camera/shutter/lighting setup. A changed dimmer setting or lighting setup needs new calibration even if the camera model is unchanged.

Estimation uses a camera-RGB proxy averaged from black-subtracted CFA cells. It is in sensor coordinates, before display rotation, cropping, white balance, or gamma. Smooth, sufficiently exposed and unsaturated regions carry more weight than edges or unreliable signal.

The base model uses a first-harmonic approximation in log illumination. Each frame fits phase and strength plus a smooth 9 × 7 light-mixture grid: 65 fitted variables. One RGB response is shared across the session. The resulting positive gain is bounded to ±0.30 in log space by default. Weak signals, contradictory supported phases, insufficient evidence, or a worsening safety diagnostic can produce an unchanged conversion.

The current base estimator includes the later eligibility fix for asymmetric lighting. “V1” refers to the frozen historical milestone, not every future invocation of the base path. Scene refinement is disabled unless explicitly requested.

Exposure duration is recorded and checked against the empirical calibration. The model does not recover a validated PWM waveform or predict arbitrary shutter speeds from a temporal integration model. Spatial cycles constrain the product of lamp frequency and sensor readout timing; they do not independently determine either quantity.

## Optional scene refinement

The scene archive contains private, exposure-normalized camera-RGB proxies and registration information. It is tied to a lighting-profile digest and repeated scene geometry; it is distinct from the reusable lighting profile.

SIFT/RANSAC registers analysis proxies. A robust temporal template and stability/gradient masks supply evidence from repeated background regions. Known targets, exact duplicate proxies, and matching source names are excluded from the target's reference. At least five other registered views are required after exclusion.

The target/template ratio fits 18 periodic RGB coefficients with affine spatial amplitude/phase. Separate quadratic nuisance terms absorb ordinary shading and do not enter the correction gain. Two spatial folds must corroborate the periodic term, and row/phase coverage must support extrapolation. The additional log gain is limited to ±0.08; the combined gain retains the base ±0.30 bound.

Unsupported registration or residual evidence falls back to the base estimator. Weak base detections remain unchanged. A base row-score veto can be reconsidered only under stronger held-out evidence. These gates support conservative behavior; they do not certify a clean moving face or uniformly better color.

## RAW writing and color

For each original sensor value, the writer computes:

```text
corrected = round(black + (original − black) × gain)
```

The two green Bayer sites share the green gain. Gains span the unrotated visible sensor array; active area, default crop, and orientation remain metadata. Source saturation cannot be recovered. Brightening can amplify noise, and a wrong illumination estimate can alter real shading despite preserving geometry.

The output is an uncompressed 16-bit CFA DNG. It carries black/white levels, CFA geometry, camera color matrix, as-shot white balance, crop, orientation, available ordinary exposure metadata, selected XMP fields, source hash, and correction provenance. Ordinary EXIF fields use ExifIFD for compatibility with Apple's native decoder. Proprietary MakerNotes, lens tables, embedded JPEGs, and camera-look recipes are not recreated.

The writer creates a temporary file, verifies the complete Bayer array through TIFF and LibRaw readers, checks white balance, then publishes the destination. Existing destinations require an explicit overwrite request; source/output collisions are rejected. Original ARW and XMP files are read-only inputs.

Preview exposure and common white balance affect only the comparison renders. They do not change DNG gains or replace the source's as-shot white balance. A decoder's tone/color defaults can still differ from the camera's JPEG look. The [RAW format](raw-format.md) and [Preview diagnosis](journal.md#rendering-compatibility) give compatibility details.

## Evidence and boundaries

Per-file reports record decisions, gains, clipping, source hashes, timing and verification; the batch report records the profile and rendering settings. Saved gain arrays allow inspection of the correction separately from display rendering.

The initial validation covers one Sony A7 IV session. General Bayer acceptance in code is not a compatibility certification for another camera: black levels, CFA, crop, matrices, shutter behavior, and editor rendering need validation. See the [evaluation protocol](evaluation.md) before interpreting results on another set.
