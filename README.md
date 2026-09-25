# flicker-removal

[![CI](https://github.com/fyang0507/flicker-removal/actions/workflows/ci.yml/badge.svg)](https://github.com/fyang0507/flicker-removal/actions/workflows/ci.yml)
[![Python 3.12–3.13](https://img.shields.io/badge/python-3.12%E2%80%933.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: experimental](https://img.shields.io/badge/status-experimental-orange)](docs/limitations.md)

Deterministic flicker-band correction for Bayer RAW photographs. Learn a lighting profile from one session, then apply a small, smooth correction to each photo's original sensor samples. Outputs are editable DNGs with matched before/after previews and processing records.

**Status: experimental.** Developed and initially validated on one private Sony Alpha 7 IV (ILCE-7M4) session on Apple Silicon. This is a session-calibrated method, with the following limits:

| Scope | Current boundary |
|---|---|
| Camera evidence | Sony Alpha 7 IV, electronic shutter, ISO 1600, f/5, 1/125–1/200 s. Other cameras and settings remain unvalidated. RGBG Bayer acceptance in code is not a compatibility guarantee. |
| Reusing a profile | Requires compatible camera geometry, shutter mode, lighting and dimmer settings. A profile from one room or session is not a universal lamp profile. |
| Band model | One dominant periodic pattern along sensor rows, a shared RGB phase and modulation response, and a smoothly varying light mixture. Multiple independent lamps or sharp lighting boundaries may violate it. |
| Restoration | Can leave residual bands, alter real shading, or abstain. Clipped detail cannot be recovered; brighter corrections can amplify noise. Complete removal is not guaranteed. |
| Optional scene refinement | Requires repeated background overlap. It gave mixed results in the first session and is disabled by default. |
| Color | Editable camera-color DNGs and neutral previews; Sony Creative Looks, proprietary lens corrections and identical rendering across editors are not reproduced. |

See [assumptions and limitations](docs/limitations.md) before processing another session. The correction preserves image geometry and does not synthesize detail or fuse pixels from other photographs.

## Install

Python 3.12 or 3.13 is required. From a local checkout:

```sh
python -m pip install .
flicker-removal --version
```

For reproducible development with the committed dependency lock:

```sh
uv sync --locked --extra dev
uv run flicker-removal --help
```

ExifTool is optional but recommended for identifying shutter mode from Sony maker notes. On macOS: `brew install exiftool`. The core runs on CPU and requires no CUDA or neural-model download. The optional `scene` extra adds OpenCV. macOS and Linux are CI targets; native RAW/editor compatibility has so far been checked on macOS.

## Process a session

Keep each camera, lighting setup, dimmer setting, and shutter mode in a separate session. Calibration requires at least three photographs with coherent flicker; more varied views help separate scene texture from lighting. Inputs are `.ARW` or Bayer `.DNG` files, including nested folders, with unique filename stems.

```sh
# Use paths outside the source-photo folder for all outputs.
flicker-removal calibrate data/session-02/raw \
  --profile data/session-02/lighting.json

flicker-removal batch data/session-02/raw \
  --profile data/session-02/lighting.json \
  --output outputs/session-02/base --full-jpeg

flicker-removal evaluate outputs/session-02/base \
  --sources data/session-02/raw \
  --report outputs/session-02/base-evaluation.json
```

Open `outputs/session-02/base/comparison.html` locally to review matched previews. Each run contains:

| Path | Contents |
|---|---|
| `corrected-raw/` | One verified editable Bayer DNG per input |
| `previews/` | Matched original and corrected neutral renders |
| `corrected-jpeg/` | Full-resolution renders when `--full-jpeg` is supplied |
| `gains/`, `reports/` | Per-photo correction maps and decisions |
| `batch-report.json` | Profile, runtime provenance, diagnostics and verification |

Original RAWs and XMPs are read-only inputs. Existing DNG outputs are refused unless `--overwrite` is explicitly requested. Corrected values remain at full sensor dimensions; the normal photograph crop is metadata. Uncompressed DNGs use about 66 MB for a 33-megapixel Sony A7 IV frame.

The default independent-frame correction is the conservative choice. Weak or unsupported detections retain the original sensor values. It includes the later eligibility fix for uneven lighting described in the [provenance journal](docs/journal.md). A low row score is not proof that every visible band is gone.

## Optional scene-assisted refinement

For multiple views of the same background, an additional scene reference can estimate small residual bands. Results were mixed in the first session, so this is opt-in:

```sh
python -m pip install '.[scene]'
flicker-removal calibrate-scene data/session-02/raw \
  --profile data/session-02/lighting.json \
  --scene-profile data/session-02/scene.npz
flicker-removal batch data/session-02/raw \
  --profile data/session-02/lighting.json \
  --scene-profile data/session-02/scene.npz \
  --output outputs/session-02/scene --full-jpeg
flicker-removal evaluate outputs/session-02/scene \
  --sources data/session-02/raw --compare outputs/session-02/base \
  --report outputs/session-02/scene-evaluation.json
```

Targets are excluded from their own scene references. Unsupported views fall back to base correction. The archive contains image proxies and must remain private. A held-out test must use a scene archive built only from separate calibration data; target exclusion alone does not establish an independent test.

## Out-of-sample evaluation

The second private set, `photos/test/`, is reserved strictly for evaluation. Freeze the code, saved profile, options and rendering settings before running it. Do not use these photos to calibrate a lighting profile, build a scene archive, tune thresholds or select algorithm changes. Fitting each photo's phase, strength and light mixture with the frozen estimator is normal inference.

If the held-out set does not match the saved profile's camera or lighting assumptions, record that limitation or failure. Develop any adaptation on separate data, with a fresh unseen set for its final evaluation. See the [evaluation protocol](docs/evaluation.md).

## Color and RAW compatibility

The DNG writer preserves camera white balance, color characterization, CFA geometry, black/white levels, crop and ordinary exposure metadata. EXIF uses the conventional directory layout required for correct ISO-dependent rendering in macOS Preview. Neutral comparison JPEGs do not reproduce Sony's Creative Look or an Adobe preset. Different RAW editors can still render colors differently.

See [RAW format and preservation boundaries](docs/raw-format.md) and the [Preview color diagnosis](docs/journal.md#rendering-compatibility). The writer currently supports RGBG Bayer; other sensor layouts are rejected. Proprietary camera metadata and lens tables are not fully preserved, so retain original ARWs as the archive.

## Documentation

- [Concise provenance journal](docs/journal.md) — how the project reached its current state, including rejected approaches.
- [Assumptions and limitations](docs/limitations.md).
- [Architecture and model limits](docs/architecture.md).
- [Python API](docs/api.md).
- [Evaluation and the next dataset](docs/evaluation.md).
- [Additional physical calibration](docs/calibration-next.md).
- [Contributing and tests](CONTRIBUTING.md).

Photos, sidecars, scene archives and generated deliveries are private and ignored. They are not included in the repository or package. Historical measurements describe the initial development session, not an independent public benchmark.

## Development

```sh
uv sync --locked --extra dev --extra scene
uv run pytest -q
uv run ruff format --check src tests scripts .github/scripts
uv build
```

Public tests use generated data. Optional RAW integration tests accept `FLICKER_TEST_RAW=/path/to/source.ARW`; they skip when no fixture is available. CI covers core and optional scene dependencies, packaging, and installed command entrypoints. Private development records and earlier experimental code are excluded from this public repository; the provenance journal summarizes their findings.

Project-owned code is [MIT licensed](LICENSE). Dependencies retain their own terms; see [NOTICE](NOTICE). No photographs, camera profiles, pretrained weights, or external model source are distributed with the package.
