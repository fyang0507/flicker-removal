# What would close the remaining gap systematically?

The current photographs support some additional refinement, but they cannot establish a zero-flicker guarantee. The remaining problem is partly missing calibration evidence: faint real shading and residual flicker can produce the same measurements, especially on moving faces under mixed lighting. Stronger correction alone cannot resolve that ambiguity safely.

## What the software work addresses

The residual work fits richer illumination gains and tests evidence from repeated scene content. The scene-assisted path excludes a target from its own reference, checks held-out spatial regions and row/phase coverage, retains weak-frame abstention, and bounds correction strength. The operation remains multiplication of original sensor samples; it does not generate facial detail or merge another photograph's pixels into a face.

We also tested classical optical flow as a source of photometric correspondences. It recovered compatible controlled signals well, but did not consistently improve the natural-photo pilots, so it remains experimental. These comparisons help identify model limitations rather than treating every more complicated estimator as an upgrade.

Software can continue improving inference and rejection. To make the reusable lighting model substantially more identifiable, the next useful input is a controlled calibration capture under the original lighting.

## Why the existing session does not fully identify the system

The approximately 8.14 cycles per sensor height in the saved profile is an **empirical spatial measurement**. It is not a measured lamp frequency or complete PWM waveform. In the simplified model,

```
spatial cycles = lamp frequency × row time × sensor height
observed amplitude = lamp modulation × exposure integration × local light mixture
```

Unknown row time and lamp frequency can compensate for each other. Free per-frame strength and spatial mixing can also absorb uncertainty in exposure integration. Three nominal shutter durations and ordinary scene content leave competing explanations.

Mixed lighting adds another unknown: the fraction of modulated light varies across surfaces. A wall, a cheek, and a forehead may receive different proportions of lamp light and ambient light. That fraction changes with position and orientation. Background calibration helps establish the waveform, but does not directly measure every moving face's illumination mixture.

## Practical capture protocol

This is a concrete plan for a later calibration session; it is not required to review or use the current outputs.

1. **Reproduce the setup.** Use the same lamps, dimmer settings, camera, electronic shutter mode, RAW settings, and image dimensions as the affected session. Keep other lights and daylight as stable as practical. Record the settings and any changes. A calibration from a different lighting state should be treated as a separate profile.
2. **Use a matte, uniform neutral target.** Fill the frame with a gray or neutral white surface under the problem lighting. Avoid texture, glossy reflections, and clipped highlights. Keep useful signal above the sensor's dark-noise region. Inspect black-subtracted RAW values when setting exposure.
3. **Lock the other camera variables.** Use manual exposure for the series: fixed ISO, aperture, white balance, focus, framing, and target position. Change only shutter duration. Keep the camera stable for this calibration, even though the eventual correction must continue supporting handheld photographs.
4. **Sample a wider shutter range.** Include 1/100, 1/125, 1/160, 1/200, 1/250, and 1/320 second. The existing batch contains 1/125, 1/160, and 1/200; the neighboring durations provide new integration constraints. Capture roughly **20–30 RAW frames per duration**, with varied release intervals to sample different flicker phases. Check the recovered phase distribution; repeated captures are useful only if they provide phase diversity.
5. **Probe the light mixture.** Repeat shorter target series at the wall, chair, and approximate subject positions, and with several target orientations toward the lights. Keep the target uniform in each image. These measurements help distinguish the common waveform from spatial or directional light mixing. They do not make a flat target an exact model of curved skin.
6. **Capture a verified averaging reference.** Use a longer exposure whose integrated modulation has been independently shown to be negligible under this lighting. Do not assume that 1/60 second, or any other nominal duration, averages every lamp/PWM component. Capture multiple phases and inspect the uniform target for residual row modulation. If ISO or aperture must change to prevent clipping, record the change and normalize it explicitly. A neutral-density filter is another option if its contribution is accounted for consistently.

These flat-target images should feed a dedicated sensor-row lighting calibration. The existing scene-reference archive uses feature registration, which is unsuitable for a featureless target; it should not be used as the calibration route for this series.

For stronger physical identification, record the lamp output with a suitably sampled, linear photodiode system. The trace can reveal frequencies, harmonics, duty cycle, and how exposure integration attenuates them. Camera-to-sensor timing synchronization would add phase/readout evidence. Without reliable synchronization, the trace still helps identify the lamp waveform but should not be presented as an exact timestamp for each sensor row. A single broadband detector does not establish RGB modulation; use color-sensitive measurements or the neutral-target RAW channels for that part.

## What would count as success

Fit the shared waveform and exposure response on only part of the calibration set. Reserve entire shutter durations, target positions/orientations, and capture sequences for testing. Also test separate photographs with known synthetic illumination applied to their original linear samples.

Set an acceptance target before fitting—for example, **less than 0.01 absolute log RMS error (approximately 1%)** on known-modulation recovery and valid, unclipped held-out flat-target measurements. Report upper-tail errors, spatial failures, clean-control changes, gain bounds, and exposure ranges. Do not subtract away the method's clean-input bias and label the resulting incremental score absolute correctness.

Then evaluate held-out natural scenes for visible bands, preserved texture and shading, noise amplification, clipping, and motion-related failures. Moving portraits generally lack pixel-exact clean references, so their visual success and remaining uncertainty must be reported separately from known-ground-truth tests.

This would support a reusable, measured session model with cheap per-frame phase and mixture estimation—and a defensible tolerance for “clean enough.” It would still not justify promising mathematically perfect removal from every existing photograph.
