# Exploration lenses

A lens adds a question and an evidence comparison to a measured observation. Its response keeps radar behavior, independent evidence and unresolved alternatives separate. Use `GET /v1/lenses` to discover the flood and wetland, vegetation disturbance, wildfire, and agriculture lenses.

Add a lens ID to a [point request](EXPLORE_API.md) and choose a date window that contains the target and at least three earlier dates in a single compatible series. The target is the later requested comparison date or the latest selected date. The lens uses the same native chips as the measured history. It uses successful measured dates before the target and lists any unusable selected dates. Set `baseline_end_date` to reserve dates on or before a known pre-event cutoff. The worker prioritizes those dates when it fills the acquisition budget.

The point result separates three states:

- `observed_departure` means at least one channel exceeded its historical median/MAD diagnostic band at the clicked pixel.
- `within_observed_baseline` means the target fell within those bounds for both channels on the common support.
- `insufficient_data` covers thin history, an unavailable target, incompatible grids or polarizations, or a masked pixel.

The lenses report signed dB relative to the median of the earlier dates and the common cell count. The baseline band is a diagnostic rule. The flood lens also reports co/cross darkening counts. Stable open water, soil moisture, crops and shadow can all affect these measurements.

## Computed flood evaluation

The flood and wetland lens now adds `lens.evaluation`, with its own `contract_version: "1.0"` and `policy_id: "explore_flood_observations_v1"`. The [evaluation schema](../contracts/flood-lens-evaluation.schema.json) describes this companion object. Existing observations, comparison layers, baseline-band point states and optical groups remain available. `GET /v1/lenses` advertises the evaluation version and schema.

| Field | What the frontend can display |
|---|---|
| `point` | Named co/cross-pol dB observations at actual dates, cellwise baseline median and scaled MAD, target change, cross-minus-co ratio change, and the last-baseline-to-target bracket. |
| `point.signature_states` | Whether joint darkening or co-pol brightening is present at this pixel under the recorded thresholds. |
| `grid_signatures` | Counts and fractions on the fixed common radar grid, with the denominator. A changed window can surround an unsupported clicked pixel. |
| `surrounding_comparison` | Changes and trajectories for a 60 m focal patch and unique baseline-level matched cells in the 120–300 m annulus. Includes match coverage, signed focal-minus-control contrasts and conditional spatial block intervals. |
| `water_comparison` | A separate optical grid, exact snapshot dates, new-water/stable-water/stable-nonwater group summaries and baseline-matched new-water/control contrasts. The point state refers to the optical comparison cell containing the click. |
| `uncertainty` | Common-grid spatial change quartiles, and explicit unavailable observation-error and fitted-model uncertainty. Channel contrast intervals live alongside their block counts in the relevant comparison. |
| `confounders` | Available optical group evidence for existing water. Rain, moisture, freeze-thaw, wind, incidence, noise and terrain visibility remain unresolved by this calculation. Processing flags are retained in the source observations. |

Joint darkening means co-pol change ≤ −3 dB and cross-pol change ≤ −1.5 dB. Co-pol brightening means change ≥ +3 dB. `supports` describes the named measured signature. `absent_on_sampled_support` describes a supported pixel or grid where that diagnostic is absent. Missing target, fewer than three baseline dates, unpaired polarizations, invalid support and off-grid points return `insufficient_data` with reasons.

The surrounding comparison requires both geographic neighborhoods to fit in the chip, at least five common cells and half the potential cells in each, and five matches covering at least half the focal cells. Matches use only baseline co/cross levels within 2 dB per channel, with up to 128 candidates for each focal cell. These surrounding cells have no independently established stability or land cover. The differential-darkening subclaim requires both channel intervals to lie beyond the corresponding negative thresholds. Intervals overlapping those bounds leave that subclaim unresolved. The existing optical-group matcher retains its 16-candidate policy and reports the matched subset separately.

Spatial contrast intervals resample complete 8 × 8-cell blocks 400 times and require at least four contributing blocks per group. The response reports their size in metres. A measured contrast can be published with an unavailable interval when blocks are sparse. These intervals quantify conditional spatial resampling sensitivity. Sensor error, reference errors, causal confounding and block independence remain unestimated.

At the optical scale, `point_new_open_water_state` supports the explicitly stated new-open-water snapshot claim for a dry-to-open-water cell. It contradicts that claim for stable nonwater, stable water or water loss. Mixed, masked and inadequate coverage remain insufficient. This claim requires both sensing dates, a pre snapshot before the radar target, a post snapshot on or after it, and at most four days from each radar anchor. A long radar gap still leaves timing unresolved. Optical water may be present without radar darkening, and radar darkening may occur over existing water.

The evaluation accompanies the radar result while `water_comparison.status` is `pending`. Optical failure or a full evidence queue preserves measured radar outputs and publishes a reason. On completion, the optical comparison updates alongside its separate grid and dates.

The new `flood_signature_flags.tif` has two named bands, `dual_darkening` and `co_pol_brightening`. Each uses 0 for absent, 1 for present, and NaN for unsupported radar cells. Read the native grid from the radar response, use nearest-neighbor display/overviews, and resolve the allowlisted URL in `lens.radar.layers`. The layer includes its SHA-256. This diagnostic footprint supports inspection of measured change.

The local `flood-lens-case` command produces the same evaluation on checksum-verified case inputs. Its point is explicitly the grid center. Live point requests evaluate the requested location, and optical comparisons use the corresponding cell on their coarser grid. Phenomenon class, probability, cause and displacement fields stay null.

## Request optical evidence

Set `"optical_evidence": true` alongside `"lens": "flood_wetland"` to request the independent optical comparison. A separate local worker looks for OPERA DSWx-HLS within four calendar days of the last radar baseline and of the radar target. The pre snapshot must precede the radar target. The post snapshot must be on or after it. The worker rechecks the sensing date on the downloaded asset and ranks at most three candidates per epoch using timing and coverage.

The job returns its radar results while the optical stage is pending. Radar power is aggregated on complete blocks to a grid of at least 30 m before comparison with the optical classes. The native radar layer stays available on its original grid. Each optical epoch requires at least 80% valid optical coverage within an analysis cell, and at least 25% of common radar cells must have usable coverage at both epochs to publish the optical comparison. Its grid, actual dates, source IDs, coverage and group denominators travel with the result. The optical TIFF is categorical and masks unsupported cells. A clear comparison can show stable water or stable nonwater. Thin coverage, absent candidates and unavailable assets have separate outcomes.

## Recompute the Barh tour case

Run this from the repo root with the measured case inputs restored:

```powershell
.\.venv\Scripts\python.exe -m surface_dynamics flood-lens-case products/flood_evidence_b/cases/barh --output scratch/lenses/barh
```

The command checks the radar and optical input hashes and calls the same lens calculation as the live point worker. The local case adds two aligned OPERA DSWx-HLS optical observations. The output includes `lens.json`, signed target-minus-baseline and diagnostic-band TIFFs, and an optical class-group TIFF. Resolve these paths relative to the report directory. Optical class codes are categorical. The report carries their legend, separate dates and usable-cell denominators. The [original evaluation](FLOOD_EVIDENCE.md) documents the data selection and comparison gates.

At Barh the optical target follows the radar target by one day. The last radar baseline precedes the target by 48 days. Present the new-open-water, stable-open-water and stable-nonwater groups together, including the number of flagged cells and total observed cells. The groups describe optical classes at the two sampled times. They leave obscured and temporally unresolved changes open.

## Vegetation disturbance

Set `"lens": "vegetation_disturbance"` and optionally `"optical_evidence": true`. The optical worker checks up to two OPERA DIST-ALERT-HLS acquisitions from the target date through seven days afterward. It reads the status, disturbance date and last-observed date layers. Each analysis cell needs at least 80% recent usable optical coverage. The stage requires at least eight cells and at least 25% of the shared radar grid to publish a comparison.

The response distinguishes confirmed optical loss in the observation bracket, provisional loss, confirmed loss outside the bracket, optically non-disturbed cells, and mixed or other supported cells. It matches cells with confirmed loss to unique optically non-disturbed cells using their earlier HH/HV or VV/VH levels. That comparison group describes the optical product's observation. Its land cover or disturbance cause may still be unknown. The response includes signed contrasts by polarization, matched counts, and conditional spatial resampling intervals where enough blocks contribute. It can report a usable optical snapshot with zero confirmed loss or a measured contrast whose interval includes zero.

Recompute the measured Rowe Creek case from the repo root:

```powershell
.\.venv\Scripts\python.exe -m surface_dynamics vegetation-lens-case contracts/examples/rowe_creek-characterization.json --output scratch/lenses/rowe-creek
```

The command uses the saved baseline and the Sep 23 radar and optical observations. It checks the aligned optical reference checksum and writes the signed radar change, diagnostic-band and categorical optical GeoTIFFs. Rowe Creek is a measured example of limited evidence near a documented fire. The [characterization report](CHARACTERIZATION_V05.md) describes the scene and reference limits.

## Wildfire

Use `"lens": "wildfire"` with a `baseline_end_date` before the period of interest. The live worker prioritizes three baseline acquisitions and then recent assessment dates within the six-date budget. Two or more valid assessment dates enable a pattern comparison on cells supported across all selected dates.

The cross-polarized response falls outside the baseline on the low side when its signed dB change is below the negative median/MAD diagnostic bound. The result records how many sampled dates met the rule and distinguishes a decline on the last two assessment dates, a return within the baseline band after a decline, declines on other dates, and no sampled decline. It includes native GeoTIFFs for the count and the pattern. These categories describe the radar measurements at the dates sampled.

With `"optical_evidence": true`, the worker compares OPERA optical vegetation loss with baseline-matched optically non-disturbed cells. For matched cells it also returns their cross-pol changes at each assessment date. Optical disturbance and documented fire context are separate fields. The live route looks up optical disturbance and has no fire-event feed. [OPERA DIST product guide](https://www.jpl.nasa.gov/go/opera/products/dist-product-suite/)

```powershell
.\.venv\Scripts\python.exe -m surface_dynamics wildfire-lens-case contracts/examples/rowe_creek-characterization.json --output scratch/lenses/rowe-creek-wildfire
```

The Rowe Creek case combines the archived observations and optical reference with documented regional fire context. The scene-level comparison leaves the cause of individual changes unresolved. [USGS Rowe Creek account](https://www.usgs.gov/media/images/rowe-creek-complex-fire-oregon-2026)

## Agriculture

The [computed agriculture v1 guide](AGRICULTURE_LENS_V1.md) describes the new `lens.evaluation` companion, baseline-matched controls, separate contrast and synchrony states, block uncertainty, observed step brackets and linked map layers. The [schema](../contracts/agriculture-lens-evaluation.schema.json) and [measured example](../contracts/examples/skollersta-agriculture-lens-evaluation.json) support frontend integration.

Use `"lens": "agriculture"` and choose a `baseline_end_date` for the period you want to compare against. The lens asks whether the selected patch changed differently from its surroundings. For each selected date it reports the median radar level inside 60 m of the point and between 120 and 300 m from the point. It reports their signed difference by polarization, the departure from the median pre-window difference, and the two observed dates that bound the largest step. A step may have occurred anywhere between those samples.

Each neighborhood must fit inside the grid and retain at least five cells and half its grid cells on common support across the selected dates. The radii describe geographic sampling. A patch or its surroundings can span multiple fields or land covers. The lens currently reports radar behavior alone, so omit `optical_evidence`. Crop, irrigation, harvest and other causes require dated independent observations.

The [geographic holdout](EXPLORATION_HOLDOUT.md) uses the same local point pipeline to run the four lenses at fixed locations beyond the documented development sites.
