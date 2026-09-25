# Evaluation protocol

Use a new photo set to measure generalization, not to retroactively tune the first session's score. The first private session established useful correction and RAW integrity; it did not establish complete flicker removal, a universal camera profile, or a public benchmark ranking.

## Keep the questions separate

| Question | Evidence |
|---|---|
| Were original files preserved and intended samples exported? | Source hashes, full-array Bayer verification, metadata/crop/WB checks |
| Did the known injected illumination decrease? | Synthetic or physical-injection recovery against a known target |
| Does an ordinary photograph look better? | Matched renders, fixed-region diagnostics, native-resolution visual review |
| Does the editor interpret the DNG correctly? | Identity conversion, same-renderer comparison, native editor inspection |
| Does an algorithm change generalize? | Predeclared calibration/evaluation split and retained regression cases |

A production acceptance gate is part of the algorithm. Passing it is not an independent quality measurement. A natural-image Fourier or row score includes genuine scene content; a percentage decrease must not be called the percentage of flicker removed.

## Second set: strict frozen holdout

The supplied `photos/test/` folder is reserved entirely for out-of-sample evaluation. It is not a development or calibration set. The test measures the existing pipeline with the frozen first-session lighting profile, whose estimated spatial frequency is approximately 8.14 cycles per sensor height.

1. Freeze the existing code revision, profile digest, options, rendering settings and evaluation procedure before inspecting test outcomes. Preserve a manifest and use a separate output directory.
2. Keep every test RAW and sidecar private and read-only. Record hashes and available camera geometry, shutter mode, shutter duration, aperture, ISO and white balance. Unknown settings stay unknown. Do not silently override incompatibility checks.
3. Run the frozen estimator. Fitting each image's phase, strength and spatial light mixture is normal inference: the algorithm and shared profile remain fixed. Do not recalibrate the profile, split off test photos for training, build a scene archive from them, tune thresholds or select algorithm changes using their results.
4. Report all cases, including failures and abstentions. Do not choose a favorable subset after seeing the outputs. Verify source hashes and exported RAW integrity, then review matched renders under the frozen settings.

The first-session profile assumes compatible camera, shutter mode and illumination. Metadata cannot establish that the lamps, dimmer setting or lighting mixture stayed the same. Unknown or changed lighting limits interpretation: this is a test of transfer under those conditions, not proof that a session-specific profile should generalize to arbitrary illumination. A mismatch or poor result is retained as an evaluation outcome; it does not permit calibration on `photos/test/`.

Optional scene refinement may be evaluated only if its configuration and archive were fixed in advance using separate development data. Target exclusion is not a substitute for this separation. Do not use test images as synthetic-injection backgrounds for development, either. Any later adaptation needs separate development/calibration data and fresh unseen photos for a final evaluation; the observed second set cannot become fresh evidence again.

## Independent future sessions

The following protocol applies to additional data collected for future development, not to the reserved second set above.

1. Keep source RAWs and sidecars private and read-only; record hashes and capture-setting evidence. Separate cameras, sensor geometry, shutter modes and lighting/dimmer settings.
2. Designate development/calibration photos and held-out evaluation photos before tuning. Include strong bands, weak controls, difficult mixed lighting, real horizontal texture, highlights and moving subjects where available. Record near-duplicates and neighboring burst frames that could leak scene evidence across the split.
3. Calibrate and develop only on the development/calibration photos. A changed lighting setup normally needs its own profile rather than silent reuse of the first session's profile. Keep the future evaluation photos untouched.
4. Freeze the code revision, profile, options, rendering settings and evaluation cases before the final run. Keep the base path as the reference and predeclare any separate evaluation of optional scene refinement.

For a held-out scene test, build the scene archive only from calibration photos. Do not place a held-out target or duplicate capture in that archive. Automatic target exclusion protects ordinary calibration-member processing, but it is not a substitute for an independently chosen evaluation split. Reports and scene archives can contain filenames, metadata and image proxies; review them before sharing.

Calibrate once on the future calibration folder, then apply the saved profile to its evaluation folder. These generic commands are **not** instructions to recalibrate from `photos/test/`:

```sh
flicker-removal calibrate data/session-b/calibration \
  --profile runs/session-b/lighting.json
flicker-removal batch data/session-b/evaluation \
  --profile runs/session-b/lighting.json \
  --output runs/session-b/base --full-jpeg
flicker-removal evaluate runs/session-b/base \
  --sources data/session-b/evaluation \
  --report runs/session-b/base-evaluation.json
```

The `evaluate` command verifies exported Bayer values against the originals and saved gains, checks source hashes and relevant metadata, and summarizes the reported estimator diagnostics. It does not supply a clean reference or a percentage of real flicker removed. To compare a predeclared candidate delivery, add `--compare runs/session-b/base`; comparison requires matching sources and preview settings. Keep the JSON report with the run's provenance.

## Review without tuning

Use identical preview exposure, white balance, crop, scale, and color encoding within each before/after comparison. Avoid judging flicker changes from mismatched camera JPEGs and neutral RAW renders. Review both whole frames and native-resolution crops of walls, faces, hair, fabric, texture, shadows and highlights. Keep diagnostic disagreements and changed-versus-unchanged counts in the report.

For a predeclared optional scene comparison, use the already frozen archive and run a separate batch with `--scene-profile`. Record registration failures and abstentions as outcomes. An unchanged conversion preserves sensor samples; it does not certify the absence of flicker.

Calibrating and processing all photos from one session is a valid operational workflow, but describe it as within-session processing rather than a held-out evaluation. The CLI does not select a random split; that choice belongs to the evaluation design.

## Known-signal tests

Run the automated synthetic suite before evaluating real photos. Changes to the estimator should retain clean/weak no-op controls, contradictory-phase rejection, positive finite bounds, and recovery tests that include real edges or shading.

Known physical intensity modulation is stronger evidence than fitting and scoring the same model. Use independently selected frequencies, phases, RGB mixtures, and spatial mixtures; include abrupt/oblique boundaries and a higher-harmonic case. A simple injection of the estimator's exact log-sinusoid family is a useful unit test, not a universal restoration benchmark.

Weak natural photographs used as injection backgrounds are not certified clean. Report these two measurements distinctly:

- **Absolute error:** compare the corrected injected image with the unmodified background; any pre-existing background flicker remains part of the target's uncertainty.
- **Incremental response:** compare correction of the injected and uninjected background; this isolates response to the known perturbation but may cancel a shared error.

Never construct a reference from a held-out injection target or select a favorable frequency using its known answer. Preserve all cases, including failed gates and regressions, and state how many pass optional refinement.

## RAW and rendering acceptance

For each exported DNG, require successful TIFF/LibRaw reopening and exact equality to the intended corrected Bayer values. Review white balance, color matrix, black/white levels, crop/orientation, EXIF exposure/ISO placement, gained values above source white, and actual storage clipping. Verify original hashes afterward.

For a new camera or decoder, also export an identity-gain DNG and compare it with its source. A sensor-identical pair isolates conversion/rendering differences from the correction algorithm. Check the user's intended editor, not only the library used to write the file. Do not attempt to repair an exposure-metadata defect by baking saturation or a tone curve into Bayer samples.

## Record a decision

Each evaluation should state the revision and profile digest, data split, camera/session scope, method, acceptance and abstention counts, independent diagnostics, known-signal results, visual disagreements, RAW-integrity status, and end-to-end runtime/memory. Distinguish fitting-only timings from decode/export/render costs.

Select candidates on development/validation data, then measure the frozen choice on the final holdout. Never use the reserved test set to select a candidate. Mixed final results narrow the claim and must be reported; further tuning requires separate development data and fresh final-test evidence. Retain earlier outputs and document the tradeoff in the [development journal](journal.md).

The [development journal](journal.md) explains why RAW integrity, appearance and independent quality evidence remain separate. Development-session results are not promised performance on the second set. The [controlled calibration protocol](calibration-next.md) describes additional development captures that could identify a more complete physical lighting model.
