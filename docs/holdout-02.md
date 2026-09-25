# Frozen out-of-sample evaluation

This aggregate report records one evaluation of a second private set, completed on 2026-09-25. It is evidence about the frozen method, not a public benchmark or a claim of complete flicker removal. Photographs, filenames, capture times, per-photo records and original Git history remain private.

## Protocol

Before inference or visual inspection, the implementation, first-session lighting profile, options, input hashes and review procedure were recorded. The evaluated implementation is byte-identical to `src/flicker_removal/` in public commit `3eb20a3`. The base pipeline ran once; scene refinement was disabled. No test photograph supplied calibration, a scene reference, a threshold change, hyperparameter tuning or candidate selection. The already frozen per-frame phase, strength and mixture fit was normal inference.

All 45 RAWs and two sidecars were included in the source manifest; no RAW was an exact content duplicate of the 35 development photographs. Exact hashes do not establish scene independence or rule out near-duplicates. Lighting and dimmer continuity were not confirmed, so this tests transfer of the existing profile, not a certified replication of its lighting setup.

The second set used the same Sony ILCE-7M4 camera model and electronic shutter, with compatible sensor geometry. All photographs used ISO 2000 and f/3.5. Shutter durations were 35 at 1/160 s, seven at 1/125 s, two at 1/250 s and one at 1/100 s. Three exposures therefore lay outside the profile's calibrated shutter groups. These are additional tested settings, not evidence of support for other cameras or arbitrary lighting.

## Outcomes

| Measure | Frozen result |
|---|---|
| Frames processed | 45 |
| Corrected / unchanged low confidence | 44 / 1 |
| Exact Bayer, decode and metadata integrity | 45 / 45 passed |
| Original RAW and sidecar hash preservation | 47 / 47 passed |
| Uncalibrated shutter flags / gain-bound flags | 3 / 2 |
| Median luminance row diagnostic, before → after | 0.081965 → 0.003544 |
| Median chroma row diagnostic, before → after | 0.018568 → 0.006929 |
| Luminance diagnostic lower / equal / higher | 44 / 1 / 0 |
| Chroma diagnostic lower / equal / higher | 39 / 1 / 5 |
| Unsigned 16-bit storage clipping | None reported |
| Maximum newly-above-source-white fraction in a frame | 0.000857, approximately 0.086% |
| Batch processing time / reported peak memory | 137.67 seconds / 1.32 GiB on Apple Silicon |

The batch time includes decode, fitting, export and previews; it excludes the later independent audit and visual-review preparation. Values above the source white level can affect rendering even without 16-bit storage clipping. The fitted row diagnostics include real scene structure and are not percentages of flicker removed.

## Visual evidence and decision

Review covered all 45 matched contact-sheet pairs and native 512-pixel crops in nine deterministically evenly spaced photographs. Three crop centers per photograph were fixed at 20%, 50% and 80% of width, all at 50% of height. Original and corrected renders used identical white balance, exposure, crop and color encoding.

Visible repetitive bands were substantially reduced in the stronger examples. Residual tone/color variation remained in some views. The sampled crops showed no obvious geometric or synthesized-detail artifacts, but this limited review cannot certify every pixel or establish perfect restoration. Five chroma-diagnostic increases remain part of the result; they were not discarded to improve the reported outcome.

The outcome supports useful correction on this second set while retaining the existing experimental scope. There is no clean reference or blinded independent visual rating. The test prompted no algorithm change and no new claim that scene refinement is better. Further adaptation must use separate development data and fresh unseen evaluation evidence. See [limitations](limitations.md) and the [evaluation protocol](evaluation.md).
