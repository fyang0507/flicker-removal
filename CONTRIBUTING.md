# Contributing

Flicker-removal corrects Bayer RAW samples with deterministic illumination gains. Changes should preserve source files, sensor geometry, and a reproducible record of every correction. The initial evidence comes from one Sony A7 IV session; broader camera and lighting support needs independent evaluation.

## Development setup

Use Python 3.12 or 3.13 and run commands from the repository root:

```sh
uv sync --locked --extra dev --extra scene
uv run flicker-removal --help
uv run pytest -q
```

The `scene` extra supplies OpenCV for optional scene-assisted correction. ExifTool is optional for ingestion, but supplies camera maker-note and shutter-mode diagnostics that LibRaw alone may not recover. On macOS, install it with `brew install exiftool`.

Most tests use generated arrays and need no photographs. RAW integration tests use an explicitly supplied local fixture:

```sh
FLICKER_TEST_RAW=/path/to/source.ARW uv run pytest -q tests/test_dng.py
```

A missing fixture skips those integration tests; passing the synthetic suite alone does not establish camera or editor compatibility. The fixture must be a supported Bayer RAW. Keep it outside Git.

## Changes and evidence

Use a new output directory for each run. Do not replace a previous delivery or silently reinterpret its metrics with a new implementation. For imaging changes, report the affected cases, clean-frame abstention, known-signal recovery, and visual review. A lower natural-image row score is a diagnostic, not proof of more accurate restoration.

RAW writer changes need sensor-value equality after reopening, metadata checks, and matched rendering checks. A successful LibRaw reopen does not establish matching appearance in every editor; record the application and version when testing native rendering.

Keep reusable code in `src/flicker_removal/` and automated checks in `tests/`. Keep private exploratory work and generated records outside the public source tree. Clearly label new results and preserve the implementation revision and evaluation procedure. The reserved `photos/test/` set is evaluation-only: never use it for calibration, tuning, or candidate selection; see the [evaluation protocol](docs/evaluation.md).

Before submitting a change:

```sh
uv run pytest -q
uv run ruff format --check src tests scripts .github/scripts
git diff --check
```

Include the problem, resulting behavior, relevant checks, and remaining limits in the change description. Add regression tests for meaningful behavior and failure modes. Do not add a test merely to duplicate an implementation detail.

## Privacy and third-party material

Do not commit photographs, previews, image-bearing scene archives, camera profiles, model weights, local environments, or generated deliveries. Reports can contain filenames, capture times, source hashes, paths, and EXIF; review them before sharing. Prefer synthetic fixtures or a separately documented dataset with explicit sharing rights.

See [NOTICE](NOTICE) before adding or redistributing external code, weights, profiles, or data. No external research model or checkpoint is bundled or downloaded by the core package.
