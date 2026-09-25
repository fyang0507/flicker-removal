# Development journal

This is a de-identified account of the decisions behind the first reusable release. Development began with one private Sony Alpha 7 IV session. The original photographs, per-photo measurements, source identifiers, experimental artifacts and private Git history are excluded from the public repository. The account below is provenance, not a public benchmark or an independently reproducible quality claim.

## Initial RAW correction

The first task was to reduce row-aligned brightness and color bands while preserving editable RAW data. The photographs shared a lighting setup and electronic shutter mode, but phase, strength, shutter duration and the spatial mixture of light varied. Ordinary shading and white balance could not safely be treated as flicker.

The chosen method estimates illumination from black-subtracted Bayer proxies, learns a shared spatial period and RGB response, then fits phase, strength and a smooth 9 × 7 light-mixture field per frame. A bounded positive gain modifies the original sensor samples. Weak-frame abstention, conflicting-phase rejection and a separate row-score veto limit unsupported corrections. The method does not synthesize detail or fuse reference pixels into an image.

The first delivery, called **V1**, substantially reduced visible bands but left residuals. Synthetic physical-intensity injections, independent strip diagnostics, visual inspection and exact TIFF/LibRaw Bayer reopening supplied complementary evidence. A lower fitted band score was never treated as the percentage of real flicker removed. Lamp frequency and sensor row timing remained unidentifiable separately from the photographs alone.

## Residual investigation

Additional harmonics, denser maps, spline fits and optical-flow correspondences sometimes improved one natural-image statistic while worsening known-injection recovery or other diagnostics. A separate neural RGB baseline also gave mixed results and did not supply corrected Bayer RAW. These approaches did not become the default.

The next delivery, **V2**, added an eligibility fix for asymmetric lighting and optional scene-assisted residual fitting. Repeated static background regions can support a small periodic correction, with target exclusion, held-out spatial checks, coverage requirements and gain bounds. Reference pixels never enter the exported RAW.

V2 was **mixed, not strictly better**. Independent luminance and chroma diagnostics disagreed, and known-injection tests did not establish a uniform further improvement. V1 remained the conservative choice for that original batch. The maintained base path includes the later eligibility fix; optional scene refinement stays disabled by default. Its extra gains can strengthen or partially reverse an earlier local adjustment.

## Rendering compatibility

macOS Preview initially displayed the DNGs darker and less colorful than their source ARWs. An identity DNG with unchanged Bayer values reproduced the difference, separating rendering compatibility from correction quality.

The writer had placed exposure metadata in IFD0. Apple's decoder ignored ISO there and selected a darker rendering baseline. Moving the same ISO and ordinary exposure fields to conventional ExifIFD restored the expected brightness without changing sensor values. Adjusting BaselineExposure alone did not repair this path. An embedded camera-look profile also failed to resolve it; a saturation boost would have hidden the actual cause.

Exact Bayer-equality checks, native ImageIO comparisons and direct Preview inspection verified the metadata repair. Remaining differences from the camera's JPEG look are a rendering boundary, not evidence that the DNG has lost RAW color information. See [RAW preservation](raw-format.md).

## Reusable release and public boundary

The accepted correction and metadata repair became an MIT-licensed Python package with a CLI, generated-data tests, optional local RAW integration tests, packaging checks, CI and reusable delivery evaluation. Consolidation preserved the estimator and exporter behavior. Experimental artifacts stayed private; the public repository begins from a sanitized maintained-source snapshot.

The method was developed on one camera and session; a later frozen evaluation is recorded below. The second private set in `photos/test/` is reserved strictly for out-of-sample evaluation using frozen code, a previously saved profile and fixed options. It must never supply calibration data, a scene reference, development feedback or candidate selection. New adaptations require separate development data and a fresh holdout. See [limitations](limitations.md) and the [evaluation protocol](evaluation.md).

## 2026-09-25 — Freeze the second set and publish maintained source

The second set was evaluated once using unchanged implementation bytes and the first-session profile, with scene refinement disabled. It supplied no calibration, tuning or candidate selection. Of 45 photographs, 44 received correction and one abstained; all 45 exported RAWs passed exact Bayer and metadata checks, and all 47 input RAW/sidecar files stayed unchanged. Matched views showed substantial reduction with residual variation, and five chroma diagnostics increased. These mixed diagnostics remain recorded in the [aggregate holdout report](holdout-02.md). Lighting continuity remains unconfirmed.

Public publication used a fresh source snapshot with a GitHub noreply commit identity. Private photographs, detailed research records, original filenames and old Git history were excluded. The public source includes badges, explicit assumptions, MIT licensing and this de-identified provenance summary. Local package tests passed without private image fixtures; publication CI is tracked on GitHub.
