# Characterization contracts for backend teammates

Version 0.4 adds two synchronous workers and four JSON Schemas, bringing the request/result total to fourteen. The existing application frontend is untouched. A backend job runner can call Python entrypoints or the CLI and serve returned artifacts. This package does not install an HTTP service or application UI.

## Flood characterization input

Python: `surface_dynamics.flood.process_flood(request, output_directory)`.

```json
{
  "contract_version": "1.0",
  "request_id": "qadirabad-flood-evidence",
  "source": {
    "mode": "local_cube",
    "cube_path": "products/flood_pilot/cases/qadirabad/cube.npz"
  },
  "baseline": {"start_date": "2026-06-01", "end_date": "2026-07-21"},
  "assessment": {"start_date": "2026-07-22", "end_date": "2026-09-30"},
  "reference": {
    "mode": "local_optical_pair",
    "path": "products/flood_pilot/cases/qadirabad/reference_pair.npz"
  },
  "policy_id": "flood_evidence_pilot_v1"
}
```

Use [flood-request.schema.json](../contracts/flood-request.schema.json). Windows must not overlap; end dates are inclusive UTC calendar dates. Local paths are **trusted backend paths**, never arbitrary public-client filesystem access. An HTTP wrapper must resolve approved job/asset identifiers itself. Credentials and forecast options are rejected by the schema.

`source.mode=catalog_point` instead accepts WGS84 `longitude`, `latitude`, optional `collection` (beta/provisional/standard GCOV), `max_dates` (4-12), `native_chip_pixels` (256 or 512) and `target_posting_m` (40/80/160). At least one compatible baseline and assessment date is necessary; three baseline dates are preferred in series selection, followed by earliest assessment availability. One compatible geometry/mode is retained. If capped, baseline/assessment sampling preserves the last baseline and first assessment. Power is block averaged before dB; no implicit interpolation or mixing of orbit directions/polarizations occurs. Coverage remains archive dependent.

`reference.mode=discover_optical` accepts a separate WGS84 `longitude`, `latitude` for bounded official DSWx-HLS discovery. Local pairs must match the exact cube x/y coordinates and projected CRS and have a SHA-256/provenance JSON sidecar. Reference is optional; without it the radar description remains available and the result is partial. Earthdata authentication is supplied to the worker environment, never JSON. Optional `context` contains start/end dates, an HTTPS source URL and description; it supplies independent scene context without becoming pixel labels.

## Flood output and display meaning

Use [flood-result.schema.json](../contracts/flood-result.schema.json). `status` is `no_data`, `partial` or `complete`. NoData has an explicit reason. Complete requires both reference epochs, >=50% reference coverage of common radar support, >=3 baseline and >=2 assessment acquisitions. It describes processing support, not scientific validation.

Actual full response examples are [qadirabad-flood-response.json](../contracts/examples/qadirabad-flood-response.json) and [kumamoto-readiness-response.json](../contracts/examples/kumamoto-readiness-response.json). Their relative artifact paths resolve against the original job directory; copying a manifest into this documentation directory does not copy the referenced datasets.

| JSON field | Meaning / use |
|---|---|
| `observations` | Actual dates, named measured polarizations, first assessment, native projected grid, source IDs/URLs/hashes and processing provenance |
| `reference_evidence` | Optical dates/offsets, source metadata and retained license, support counts/fractions and six-group legend |
| `signature_summary` | Co-/cross-pol choice, signal-mask counts and nominal analysis-cell areas, interpretation enum and object counts |
| `matched_controls` | Matched positive/control counts, baseline balance and co-pol descriptive contrast; conditional spatial interval when enough blocks exist |
| `proxy_comparison` | Proxy confusion counts, precision/recall/IoU, separate stable-nonwater/stable-water/water-loss flag rates, excluded counts and last-baseline sensitivity |
| `objects` | Radar-signature components with observed trajectory, date brackets, support, return censoring and nominal grid area |
| `insights` | Deterministic statements linked through `evidence_artifacts` to manifest IDs, each with an interpretation limit |
| `artifacts` | Relative `href`, role, media type, units where applicable and SHA-256; backend maps these to authorized artifact URLs |
| `flood_depth_m`, `flood_probability`, `causal_attribution` | Always null; do not replace null with zero |

Spatial intervals are conditional resampling sensitivity, not confidence in flood extent or cause. Proxy metrics are agreement with an imperfect independent optical classification. Zero positive-reference denominator yields null recall. Never present a large nonwater denominator as high flood accuracy.

### Array contracts

`characterization_arrays.nc` has dimensions `time,polarization,y,x`, with ascending east-positive x, descending north-up y, projected metres and a CF `spatial_ref` WKT. Coordinates are pixel centers. `time` contains actual unique acquisition dates.

| Variable | Shape | Units / definition |
|---|---|---|
| `gamma0_power` | `[T,C,H,W]` | Measured/linearly aggregated gamma0, unitless |
| `change_db` | `[T,C,H,W]` | Each observed dB value minus supported baseline median |
| `baseline_db`, `baseline_mad_db` | `[C,H,W]` | Median and 1.4826-scaled MAD in dB |
| `baseline_valid_count` | `[C,H,W]` | Number of finite baseline observations |
| `target_change_db` | `[C,H,W]` | First assessment minus baseline median, dB |
| `target_minus_last_baseline_db` | `[C,H,W]` | Diagnostic alternate comparator, dB; not a selected optimal model |
| `robust_scaled_target_change` | `[C,H,W]` | Dimensionless MAD-scaled deviation with 1 dB floor; not a z-test |
| `comparison_support` | `[H,W]` | Supported baseline and first-assessment co-/cross-pol samples |
| `reference_group` | `[H,W]` | 0 stable predominant nonwater; 1 new strict open water; 2 stable strict open water; 3 strict open-water loss; 4 mixed/partial; 255 unsupported |
| `matched_control_mask`, `matched_positive_mask` | `[H,W]` | Separate baseline-matched comparison populations |
| `candidate_darkening`, `co_pol_brightening` | `[H,W]` | First-assessment diagnostic radar masks, not phenomenon labels |
| `darkening_observation_frequency`, `brightening_observation_frequency` | `[H,W]` | Co-pol-only threshold count / supported assessment count; cross-pol condition is not required here |
| `co_pol_valid_assessment_count` | `[H,W]` | Explicit denominator of observation frequencies |
| `object_labels` | `[H,W]` | All 4-connected dark components; JSON/GeoJSON report only >=4 cells and top 100 |
| `pre_*`, `post_*` reference fields | `[H,W]` | Valid coverage and conditional shares of open/partial source class cells, not subpixel water fractions |

COGs mirror target change, robust scaled change, reference groups, matched controls and darkening observation frequency. Polarization-band order follows `observations.polarizations`; categorical COGs preserve the legend and use nearest-neighbor overviews. GeoJSON is EPSG:4326 with longitude/latitude positions. Its polygons are radar-signature cells, not confirmed flood extent. Group CSV includes polarization, date, valid-cell denominator, mean and median dB change. PNG is a scientific overview; quantitative clients should consume arrays and JSON.

## InSAR correction-readiness input/output

Python: `surface_dynamics.insar_readiness.process_readiness(request, output_directory)`; schemas [input](../contracts/insar-readiness-request.schema.json) and [output](../contracts/insar-readiness-result.schema.json).

Input has contract version, request ID, `source: {mode: "local_pairs", pair_paths: [...]}` with 2-6 trusted measured NPZ paths and checksum sidecars, plus optional `coherence_threshold` (0.1-0.9; default 0.35). Exact common native pixels, one compatible track/pass/frame/bandwidth/polarization and unique day-pair revisions are required. There is no implicit phase resampling.

Result always returns `status: "not_displacement_ready"`, `corrections_applied: []` and `los_displacement_cm: null` under this audit. It provides pair-specific fill/screen/uncertainty support, quantiles, common-support denominators, threshold sensitivity and `required_gates` with next required inputs. Even a candidate reference explicitly has `independently_stable: false` and `correction_validated: false`. Missing common phase support makes its conditional fraction null, rather than zero.

`correction_readiness_arrays.nc` preserves supplied ionosphere screens and product-reported uncertainties in radians, per-pair eligibility masks, common eligibility, original interval coherence and uncorrected shared-pixel phase when available. Eligibility requires coherent selected-component support, no filled-ionosphere bit, finite screen and finite positive reported uncertainty. It is **not** correction accuracy. A common mask COG and multi-band reported-uncertainty COG accompany the arrays.

## Offline commands

After installing and restoring fixtures with `scripts/download_examples.py`:

```powershell
python -m surface_dynamics flood-characterize contracts/examples/qadirabad-flood.json --output products/flood_pilot/runs/qadirabad
python scripts/run_flood_pilot.py
python scripts/run_insar_readiness.py
python scripts/verify_flood_pilot.py
```

The release includes input fixtures and selected previews/manifests; these commands regenerate all 33 new scientific artifacts without archive credentials. `contracts/examples/qadirabad-flood-live.json` instead demonstrates catalog plus optical discovery. [The evidence report](FLOOD_CHARACTERIZATION.md) supplies the actual interpretation and limitations.
