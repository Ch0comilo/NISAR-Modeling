# Measured Feature Lab

Add `"feature_lab": true` to `POST /v1/explore` to receive the versioned `feature_lab` companion on the same observation job. A lens is optional. The [schema](../contracts/feature-lab-result.schema.json) and [measured Colombia example](../contracts/examples/colombia-feature-lab-result.json) define the response.

```json
{
  "longitude": -73.2,
  "latitude": 4.8,
  "start_date": "2026-07-01",
  "end_date": "2026-11-06",
  "max_dates": 6,
  "feature_lab": true
}
```

The request uses the existing native chip cache and observation queue. Each observation and the date-pair comparison publishes while acquisitions arrive. Feature Lab remains `pending` during that stage, then publishes its cards and allowlisted maps. Its optional flag defaults to false. Add `"lens": "flood_wetland"` to evaluate the flood lens on the same fixed cell population.

`baseline_end_date` is supported with Feature Lab alone. It reserves three baseline acquisitions, followed by assessment dates within the existing six-date budget. Without a cutoff, dates before the target define the observed baseline. The target is the later `compare_dates` date, or the latest selected date. Analysis excludes all observations later than a pinned target. Source observations may still show those later selected acquisitions.

## Cards and gates

| Card | Backend fields | Support, units and interpretation |
|---|---|---|
| Measured channel histories | `features[].series`, `point_timing`, `roi_timing` | Actual HH/HV or VV/VH dB measurements, the clicked pixel and a fixed common-grid mean with spatial quartiles. Individual raw point dots retain their masks. Baseline median, scaled MAD and diagnostic band require three baseline dates and a complete point or ROI series. |
| Polarization balance | `HV_over_HH` or `VH_over_VV` feature | Per-cell, per-date cross-pol dB minus co-pol dB. Point linear ratios are also supplied. The target ratio is compared to the median of the baseline ratios. It can differ from subtracting separately computed channel changes. A missing measured polarization leaves that ratio unavailable. Ratios depend on their source channels. |
| Spatial structure | `spatial.directional_texture`, `spatial.patches` | Target-date directional semivariances and median absolute gradients at native lags 1, 2, 4 and 8 cells. At least 64 common cells and 30 valid cell pairs per bin are required. Distances use both x and y posting. NoData pairs are excluded. Gradients, sampling geometry, speckle and nonstationarity can dominate these empirical measures. |
| Historical behavior | `point_timing`, `roi_timing`, `gp` | The first observed departure bracket, changed acquisition count and denominator, plus an optional historical GP at actual acquisition dates. A bracket spans the last supported baseline-like observation and first observed departure. Missing or insufficient records return a reason. |

Every feature includes its definition, physical meaning, source channels and units. Current Feature Lab supports measured diagonal power channels and the two paired cross/co ratios. Single-channel data still provide that channel's measured history. The card objects carry individual availability states, so short records can retain useful measurements.

The ROI is the entire native chip's fixed common population. It requires valid measurements in every used acquisition and named channel. `support.common_mask_sha256` hashes the row-major uint8 mask. `grid` supplies EPSG, bounds, shape and posting. `point` records the requested pixel separately from its complete-series support.

When Feature Lab and a lens are requested together, `lens.shared_observation_support` records the same used dates, cell count and mask hash. The lens's native calculations are restricted to those cells. This can exclude cells that would have supported a two-snapshot lens alone. The existing progressive date-pair comparison keeps its own two-date support. Optional optical evidence retains its separate coarser grid and snapshot dates.

## Patch and boundary measurements

Patches use an absolute target-minus-cellwise-baseline change of at least 3 dB, separately for decreases and increases. They require three baseline dates, a valid target, 64 common cells and four connected cells. Connectivity is eight-neighbor. Area is in square metres at the recorded posting. Patch IDs are negative for decreases and positive for increases. Zero denotes no retained diagnostic patch, and NaN denotes unsupported cells.

The response includes patch count and area quartiles, then the largest 100 objects with their cells, signed mean change, boundary contrast and clipping flag. `objects_truncated` identifies longer lists. Boundary contrast compares mean change inside the patch with the adjacent one-cell outer neighborhood, requiring four outer cells. Touching the chip edge or a NoData boundary is recorded. These footprints describe thresholded measurements at this scale.

The timing diagnostic uses a different question. It requires change strictly outside `max(3 dB, 3 × max(scaled baseline MAD, 1 dB))`. A 3 dB patch can coexist with a point whose variation remains inside its own historical band.

## GP and uncertainty

GP cards require six actual dates and eight complete spatial cells. The current point route supplies at most six dates. Constant regional histories, numerical failure and an unsuccessful optimizer return explicit unavailable reasons while other measurements remain available.

The implementation reuses the existing empirical-Bayes Matérn-3/2 worker, with parameters fitted to at most 64 complete cells per feature. It returns a fixed-grid mean curve and 128 conditional historical draws per feature, plus draw quantiles and fit parameters. These outputs are at observed dates within the selected historical interval. Treat the regional mean as one representative scalar process with the fitted pixel covariance. Its variance is not divided by the pixel count. Mean, hyperparameter, reference and model-family uncertainty are omitted, and the residual term mixes omitted variation and errors.

Spatial quartiles summarize heterogeneity among common cells. The baseline MAD summarizes observed temporal variability. GP bands describe a conditional model distribution. Sensor and speckle uncertainty remain unavailable. This Feature Lab card has no per-query hindcast validation or demonstrated reconstruction advantage. The observed temporal mean and spread accompany the fit for comparison. The earlier [model validation](MODELING_V2.md) documents the separate reconstruction experiment and its baseline results.

## Artifacts and frontend integration

All maps use the native comparison grid and carry SHA-256, units and band names. Download through each layer's job-relative `href`.

| Layer role | Use |
|---|---|
| `common_support` | One-band 0/1 mask for the fixed population. Display with nearest-neighbor sampling. |
| `target_db` | Target power and log-ratio bands, all in dB, on common support. |
| `baseline_db` | Cellwise median per feature over the declared baseline dates. |
| `change_db` | Target minus that cellwise baseline, in dB. |
| `patch_ids` | Signed diagnostic object IDs per feature. Use categorical colors and nearest-neighbor sampling. |

The owner can link the selected feature's map band to its measured point/ROI plot, show the baseline band separately from spatial quartiles, and overlay the optional GP curve and model band at actual dates. A patch selection can read the corresponding signed ID and object details. Show the contributing date interval, native posting and support when displaying its area or boundary contrast.

The retained six-date Colombia replay used 4,096 common 10 m cells. Feature Lab alone completed in approximately 0.32 seconds, and Feature Lab plus the flood lens in 0.54 seconds, using the already cached inputs with one BLAS thread. The features and fixed support were identical with and without the lens. A pinned five-date record withheld GP output. These timings exclude interpreter imports, catalog discovery, cold archive reads and host concurrency.
