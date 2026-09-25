# Corrected RAW delivery

This product includes DNG technology under license by Adobe.

The deliverable uses **Bayer DNG**, retaining the sensor mosaic and full sensor dimensions. The original Sony ARW and its XMP sidecar stay unchanged. A proprietary Sony ARW encoder is not available in this project; an RGB TIFF renamed `.ARW` or `.DNG` would not preserve the RAW workflow.

`src/flicker_removal/dng.py` writes an uncompressed, 16-bit CFA DNG. It uses DNG 1.4 identification and the 1.1 backward-compatibility floor because the file uses `ActiveArea`. It does not demosaic, denoise, sharpen, interpolate image detail, or synthesize pixels. Only the estimated gain field is interpolated. Each corrected sensor sample is computed as:

```text
corrected = round(black + (original - black) × correction_gain)
```

The interpolation and final integer rounding are deterministic. The two green Bayer positions share the green correction gain.

## Geometry and gain coordinates

The inspected Sony A7 IV files expose:

| Region | Height × width | Meaning |
|---|---:|---|
| Stored Bayer array | 4688 × 7040 | Full decoded sensor array retained in DNG |
| LibRaw visible array | 4688 × 7028 | Coordinate domain used by the gain grid |
| Default photograph crop | 4672 × 7008 | Starts at sensor `(y=8, x=12)` |

The default crop is metadata; it does not discard the surrounding RAW samples. Gain grids refer to the unrotated **visible sensor array**, before the default crop. Pixel centers are mapped to gain-grid cell centers, with bilinear interpolation and constant extension at the edges. Valid inputs are a positive scalar, per-row vector, `rows × 3` camera-RGB gains, or `rows × columns × 3` gains. An RGB triplet alone is ambiguous and is not a supported shape; use `(1, 3)`.

The batch proxy averages sensor blocks. Its 878 columns cover 7024 source columns before rescaling; mapping that grid over 7028 visible columns stretches it by approximately 0.057%. This is negligible for the deliberately smooth mixing field, but should be accounted for if a future implementation estimates sharp spatial changes. The row mapping is exact: 586 proxy rows cover 4688 sensor rows.

## Preserved information

The DNG includes the actual CFA pattern, black offsets, source white level, default crop, active area, camera make/model, orientation, camera white balance, and LibRaw's camera color matrix. It also transfers available exposure time, aperture, ISO, focal length, exposure compensation, capture time including subsecond/offset fields, and lens model. These basic exposure fields now use conventional ExifIFD placement. The original IFD0 layout made Apple ignore ISO and choose a darker rendering baseline; see the [macOS Preview repair](journal.md#rendering-compatibility). The embedded XMP carries source rating, label, and creation date where present.

The color matrix is LibRaw's camera XYZ-to-camera transform, written as `ColorMatrix1`, with D65 calibration. `AsShotNeutral` is the reciprocal of the source camera white balance, normalized to green. It is not an invented identity matrix. LibRaw documents the direction of this matrix in its [maintainer explanation](https://www.libraw.org/node/2099) and exposes it through rawpy's [`rgb_xyz_matrix`](https://github.com/letmaik/rawpy/blob/main/rawpy/_rawpy.pyx).

The source filename and SHA-256, correction bounds, gain-grid shape, and processing metadata are recorded in the DNG image description. The source itself is not embedded a second time.

## Boundaries of this conversion

- **Not a lossless archival replacement for ARW.** Bayer values intentionally change; source ARW files remain the archival originals.
- Sony MakerNotes, manufacturer lens shading/distortion tables, focus metadata, embedded JPEGs, and proprietary Adobe camera/look profiles are not copied. Original ARW/XMP files retain them. This writer does not promise identical default rendering in every editor.
- Source XMP files remain intact. The new embedded XMP copies selected descriptive fields, not camera-look defaults or development recipes, and does not copy stale ARW identifiers or digests.
- Sensor values above the original white level after correction are retained, up to the 16-bit container limit. `newly_above_white_fraction` reports these values separately from actual uint16 clipping. RAW editors may clip values at their interpreted saturation threshold. Original saturated highlights cannot be reconstructed, and local brightening amplifies the original noise.
- No rendered preview is embedded. Some file browsers therefore will not display a thumbnail; use a RAW editor or the separately generated comparisons.
- The writer is validated here for the supplied Sony A7 IV Bayer files. Other cameras require their own black-level, matrix, CFA, crop, and rendering validation.

The [Adobe DNG specification](https://helpx.adobe.com/content/dam/help/en/camera-raw/digital-negative/jcr_content/root/content/flex/items/position/position-par/download_section_733958301/download-1/DNG_Spec_1_7_1_0.pdf) defines the CFA, black-level, white-balance, matrix, crop, and version tags. Its metadata section allows TIFF-EP tags in IFD0. This writer uses uncompressed integer samples; it does not use Deflate compression, whose DNG applicability is restricted for this data type.

## Verification and evidence

Every normal write first produces a temporary file, verifies it, and then publishes the destination. A failure removes the temporary file. Existing output files require an explicit `overwrite=True`. The source is never opened for writing.

Validation checks required DNG tags, 16-bit CFA encoding, and exact equality between the intended corrected Bayer array, the TIFF-decoded array, and the independently reopened LibRaw array. It also checks dimensions, semantic CFA colors, black levels, white level, and source white-balance ratios.

Development checks included identity-gain conversions with exact Bayer equality, same-renderer source/DNG comparisons, independently computed gain samples, unchanged margin pixels, source-hash verification, and existing-destination protection. The public tests cover these contracts with generated data and an explicitly supplied optional RAW fixture.

Identity and corrected DNG pilots also opened and rendered in Adobe Camera Raw 16.1. This is a limited manual compatibility check, not Adobe DNG SDK certification or per-file approval. The validation field `adobe_camera_raw_verified` remains false because automatic verification does not control Adobe. Other editors and camera models need their own checks.

## API

```python
from flicker_removal.dng import write_corrected_dng

result = write_corrected_dng(
    "data/session/source.ARW",
    "outputs/corrected/source.dng",
    gain_grid,  # rows × columns × 3, camera RGB multiplicative correction
    metadata={"lighting_profile": "session-profile.json"},
)
assert result["validation"]["bayer_exact_match"]
```

The uncompressed DNG size is approximately 66 MB for a 33-megapixel Sony A7 IV frame. This avoids reliance on an unverified lossless-JPEG writer. Plan output storage before large batches.
