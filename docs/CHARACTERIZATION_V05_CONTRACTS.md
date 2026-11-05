# Shared characterization contract: v0.5

Python entrypoint: `surface_dynamics.characterization.process_characterization(request, output_directory)`.

CLI: `surface-dynamics characterize request.json --output directory`.

The new [request schema](../contracts/characterization-request.schema.json) and [result schema](../contracts/characterization-result.schema.json) bring the request/result schema total to sixteen. The worker is synchronous; an application backend can wrap it in its own job system and serve authorized artifact URLs. No HTTP server or frontend is introduced.

## Input example

```json
{
  "contract_version": "1.0",
  "request_id": "wheatbelt-surface-behavior",
  "theme": "agriculture",
  "source": {
    "mode": "local_cube",
    "cube_path": "products/characterization_v05/cases/wheatbelt/cube.npz"
  },
  "baseline": {"start_date": "2026-06-01", "end_date": "2026-08-31"},
  "assessment": {"start_date": "2026-09-01", "end_date": "2026-10-31"},
  "water_reference": {
    "mode": "local_optical_pair",
    "path": "products/characterization_v05/cases/wheatbelt/reference_pair.npz"
  }
}
```

Local paths are trusted backend asset paths. A public API wrapper must resolve approved asset identifiers rather than expose arbitrary filesystem paths. Authentication belongs in the worker environment; credentials and forecast parameters are rejected by the schema. Windows are ordered, non-overlapping, inclusive UTC calendar dates. The baseline is explicitly declared, not chosen for the strongest signal contrast.

`source.mode=catalog_point` accepts WGS84 longitude/latitude and the same bounded GCOV options as the flood worker: collection, 4-12 maximum dates, native chip size 256/512 and 40/80/160 m target posting. Compatible geometry/mode selection preserves the last baseline and first assessment. Aggregation must be an integer native block in linear power; unsupported coverage stays explicit.

Optional `selection` has longitude, latitude and `radius_m` (0-25,000). Radius zero selects the nearest native analysis cell. Circles are clipped to the chip. The report uses the intersection of selected cells measured at every chosen baseline/assessment date and in its named co-/cross-pol channels. A point outside the grid/projection or without fixed common observations returns NoData, with a reason. A single cell can have a partial descriptive report; it cannot support a spatial bootstrap interval.

`include_methods` defaults to true and emits physical, spectral and stochastic component results when their support requirements are met. `transformer_checkpoint` optionally adds the existing learned branch. Its domain checks remain active: the frozen v2 checkpoint requires measured L-band HH/HV semantics at 160 m. A 40 m request is not silently passed through that checkpoint or silently resampled. The separate versioned representation-probe experiment performs explicit common-posting aggregation for its matched comparison.

`water_reference` accepts a checksum-backed local optical pair or `discover_optical` with WGS84 point coordinates. It is independent optical classification, not surveyed ground truth. `disturbance_reference` is a trusted aligned DIST-HLS NPZ path with a checksum/provenance sidecar. These references must match the exact cube grid/CRS and temporal contract. Optional `context` supplies source URL, dates and narrative; it does not create pixel labels.

## Output fields

| Field | Contents / use |
|---|---|
| `status` | `complete`, `partial`, `no_data`; processing support only, not confirmed physical interpretation |
| `observations` | Actual dates, baseline/assessment dates, first assessment, measured channel names, grid, source/processing provenance and local input fingerprint |
| `quality` | Selected grid count, fixed common cell count/fraction, per-date/channel supported counts and the population rule |
| `what_changed` | Per-channel mean/median/spatial quantiles of first-assessment dB change, temporal dispersion and cross/co log-ratio change |
| `when` | Bracketed regional first threshold crossing, actual changed-acquisition count/span and confirmed radar-signal return where supported |
| `behavior` | Regional pattern, separate spatial cell-pattern distribution, most frequent cell pattern and legend |
| `evidence` | `not_supplied`, `insufficient_independent_support`, `limited_independent_support` or `independent_product_comparison`, plus typed evidence items |
| `methods` | Availability, limits and metadata for the optional method components |
| `insights` | Deterministic statements, interpretation limits and IDs of supporting registered artifacts |
| `alternative_explanations` | Theme-relevant possible mechanisms, never an inferred cause |
| `artifacts` | Unique IDs, relative paths, SHA-256, media types, roles and optional units/dimensions/legends |
| `phenomenon_probability`, `causal_attribution`, `displacement_cm` | Always null |

The actual [full response example](../contracts/examples/wheatbelt-characterization-response.json) includes all fields. Relative artifact paths resolve against the original job directory. Copying that manifest into the contracts directory does not copy its referenced datasets. Method and water-component IDs are namespaced to avoid map collisions; the independent water manifest is also registered.

Complete means sufficient radar dates/cells for the declared report. It can coexist with limited or absent independent evidence. Never turn a NoData/null quantity into zero change, zero risk or zero event probability. A regional no-crossing pattern can coexist with localized repeated or intermittent changes; use the spatial distribution and maps alongside the regional summary.

## Arrays and scientific plots

`behavior_arrays.nc` contains `target_change_db[polarization,y,x]`, `pattern_code[y,x]`, `comparison_support`, `selection_mask`, `observed_change_frequency` and `valid_assessment_count`. Coordinates are native projected metre pixel centers, ascending east-positive x and descending north-up y, with CF `spatial_ref` WKT. The first-assessment change is relative to each cell's supported baseline median in dB. Observation frequency uses absolute co-pol change >=3 dB divided by actual supported assessment count, not continuous duration or event probability.

Pattern codes follow [behavior-policy.json](../contracts/behavior-policy.json): 0 insufficient temporal support; 1 no observed threshold crossing; 2/3 repeated observed darkening/brightening; 4 intermittent crossings; 5 mixed change; 6/7 gradual observed darkening/brightening; 255 unsupported. Categorical map colors encode these definitions, not land cover or natural phenomena.

Observed-pattern codes and optical-reference group codes have separate legends: the same integer can have different meanings in these different layers. Use the legend attached to the specific artifact/variable.

COGs mirror pattern code, first-assessment channel changes and observed frequency. `fixed_support_trajectories.csv` gives date, channel, common-cell denominator, mean/median and spatial interquartile values. The scientific preview places both channel maps, pattern/frequency maps, trajectories and evidence status together. Optional method artifacts add spectra, actual-time heatmaps when supported, GP distributions, spatial intervals and learned states within model domain. Spatial quantiles and GP/bootstrapped intervals are different quantities.

Independent water evidence retains the v0.4 schema, groups and limitations. Its report uses the selected fixed-support cube; the separate frozen flood-validation experiment uses the original cube and its original first-date support policy. Their denominators differ and are documented in their respective manifests.

DIST-HLS evidence targets the last assessment acquisition, with an explicit `comparison_sar_date`, optical date/offset, recent-coverage denominator, strict confirmed in-window vegetation-loss count and matched descriptive contrast. COGs retain recent coverage, confirmed/provisional class shares and confirmed disturbances outside the window. Older/stale detections do not become new event evidence. `wildfire_attribution` and `burned_area_ha` remain null. Neither the status class share nor its nominal cell area is independently mapped burned extent.

## Reproduction

After installation and fixture restoration:

```powershell
python -m surface_dynamics characterize contracts/examples/wheatbelt-characterization.json --output products/characterization_v05/runs/wheatbelt
python scripts/run_characterization_v05.py
python scripts/evaluate_characterization_v05.py
python scripts/run_insar_v05.py
python scripts/verify_characterization_v05.py
python scripts/report_characterization_v05.py
```

`world-characterization.json` demonstrates live global-point plus optical discovery. `gimcheon-characterization.json` demonstrates insufficient baseline support; `rowe_creek-characterization.json` adds independent optical disturbance evidence. The complete fixture bundle needs no archive credentials for these offline runs.

Live regeneration uses `prepare_characterization_v05.py`, then the same script with `--references`, and `prepare_insar_v05.py`. These perform archive reads and require Earthdata authentication in the process environment. The scripts refuse to overwrite consumed protocol/probe freezes with changed configurations or predictors. The inspected version-0.5 test scenes must not be silently reused as untouched future tests.
