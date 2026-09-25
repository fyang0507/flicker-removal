# Assumptions and limitations

The current method was developed on one private, 35-photo Sony Alpha 7 IV (ILCE-7M4) session: electronic shutter, ISO 1600, f/5, and shutter durations of 1/125, 1/160 and 1/200 s. The illumination was shared across that session. Successful correction there establishes a useful starting point, not general camera or lighting compatibility.

## Camera and file support

The CLI accepts ARW and Bayer DNG inputs. The writer requires an RGBG Bayer sensor, usable black/white levels, camera white balance and a nonsingular color matrix. It does not hard-code a Sony-only camera whitelist, but another camera passing those checks is still unvalidated. Sensor layout, crop, black levels, shutter behavior and native-editor rendering all need independent verification.

The exported DNG preserves sensor dimensions and records crop and orientation as metadata. It multiplies black-subtracted sensor samples by positive gains; it does not resample, warp, denoise, sharpen or synthesize image detail. Clipped source highlights cannot be reconstructed. Brightening increases noise, and an incorrect gain can change genuine shading or color.

## What a lighting profile assumes

| Assumption | Consequence when it fails |
|---|---|
| Compatible camera geometry, shutter mode, lighting and dimmer settings | A saved profile may fit the wrong pattern. The CLI rejects camera/geometry and known shutter-mode mismatches, but cannot establish that lighting is unchanged. Unknown shutter mode remains unverified. |
| A dominant periodic pattern along unrotated sensor rows | Diagonal, irregular, changing-frequency or several independent patterns are outside the base model. Calibration's coarse search spans 2.5–30 cycles per sensor height, followed by local frequency refinement. |
| One shared phase across RGB and a session-wide RGB modulation ratio | Independently modulated colored lamps can violate the model. Calibration rejects sufficiently inconsistent RGB phases; passing that check is not proof of a single physical light source. |
| Per-frame phase and strength, with a smooth spatial light mixture | A 9 × 7 mixing grid can represent broad changes in illumination, but sharp boundaries, specular reflections and complex mixed light can leave residuals or bias the estimate. |
| Enough smooth, exposed, unsaturated image regions | Heavy texture, horizontal scene structures, dark areas or clipping can leave insufficient evidence or resemble flicker. The estimator can abstain or mistake scene variation for illumination. |

Calibration needs at least three photographs with coherent modulation. It estimates spatial periodicity and empirical shutter-duration groups; it does not recover a validated lamp waveform. Lamp frequency and sensor readout time cannot be separated from spatial bands alone. A supplied measured lamp frequency permits a derived effective row time, not validation of arbitrary shutter-speed prediction. Unseen shutter durations are flagged for review rather than automatically rejected.

The base path processes frames independently, so camera or subject movement between frames does not require registration. Its gain is bounded to ±0.30 in log space by default. Confidence gates and a row-score veto reduce risk; an unchanged result does not mean that the photograph is flicker-free, and a lower diagnostic score is not a measured percentage of flicker removed.

## Optional scene reference

Scene refinement needs repeated, registerable static background regions and at least five eligible reference views after target exclusion. It registers analysis proxies only; reference pixels never enter the exported RAW. The extra log gain is bounded to ±0.08 and the combined gain retains the base bound.

Results were mixed in the development session. Extra channel gains may strengthen or partially reverse the base correction, so this option is not guaranteed to improve every image. Moving subjects and backgrounds with little shared coverage remain difficult. The scene archive contains image proxies and must remain private.

## Color and editor behavior

DNG sensor values retain camera color information, white balance and color characterization. The package does not recreate Sony Creative Looks, Adobe camera-matching profiles, proprietary lens tables or original embedded JPEGs. Neutral comparison previews therefore need not match the camera's JPEG appearance.

An earlier macOS Preview exposure defect was repaired by writing ISO and other ordinary exposure fields in conventional ExifIFD. That repair changes metadata placement, not RAW pixels or saturation. It does not promise identical color across Preview, Camera Raw or other editors. See [RAW preservation boundaries](raw-format.md) and the [Preview diagnosis](journal.md#rendering-compatibility).

## Strict held-out testing

The second private set in `photos/test/` is evaluation-only. Freeze the code revision, profile, options, rendering settings and evaluation procedure before inspecting its results. Do not calibrate from it, build a scene reference from it, tune against it or choose a candidate using its outcomes. Per-frame parameter estimation by the already frozen algorithm is permitted inference.

A mismatch with the frozen profile is an evaluation result, not permission to recalibrate on the test photos. Any later adaptation requires separate development/calibration data and fresh held-out evidence. Record failures, abstentions and diagnostic disagreements as well as improvements. The [evaluation protocol](evaluation.md) separates RAW integrity, rendering compatibility and restoration quality.
