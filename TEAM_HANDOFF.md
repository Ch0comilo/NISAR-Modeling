# Team handoff: worldwide descriptive surface dynamics

Current direction approved 4 November 2026: **exploration, teaching and entertainment through measured radar dynamics**. No forecasting or automatic phenomenon classification is part of this implementation. The mockup supplies case-study context only; its methods and design did not determine this model. Neither the mockup nor application frontend is modified.

## Current additions: version 0.5

Use [CHARACTERIZATION_V05_CONTRACTS.md](docs/CHARACTERIZATION_V05_CONTRACTS.md) for the new shared `characterize` worker and sixteen request/result schemas. [The scientific results](docs/CHARACTERIZATION_V05.md) and [validation report](docs/CHARACTERIZATION_V05_VALIDATION.md) explain eight shared reports, five new measured scenes, fresh flood/control comparisons, an optical disturbance adapter, a frozen representation probe and a new earthquake-context InSAR stack. No frontend or mockup changes are included.

The report separates `what_changed`, `when`, `behavior`, `evidence`, alternative explanations and linked method artifacts. Processing status, observed pattern and independent evidence are separate concepts. Fixed common cells prevent a changing footprint from creating an apparent regional trajectory. Spatial bootstrap means are now cell-weighted; GP bands, spatial quantiles and resampling intervals retain separate meanings. Null cause/probability/displacement fields must remain unavailable to clients.

No globally validated phenomenon classifier is delivered: the fresh flood-context sites lack qualified new-water positives, and the learned-feature kNN probe fails strongly on an Australian nonwater control. The frozen temporal Transformer remains an exploratory representation. Oregon vegetation loss is not attributed to wildfire, and the Venezuela stack remains correction/reference limited. Read the complete evidence status before presenting a phenomenon interpretation.

## Previous additions: version 0.4

Use [CHARACTERIZATION_CONTRACTS.md](docs/CHARACTERIZATION_CONTRACTS.md) for the new flood and InSAR-readiness worker inputs, result fields, dimensions, units and offline commands. [FLOOD_CHARACTERIZATION.md](docs/FLOOD_CHARACTERIZATION.md) describes three real development cases; [FLOOD_VALIDATION.md](docs/FLOOD_VALIDATION.md) gives the evidence limits. Four new schemas bring the request/result count to fourteen. Flood depth/probability/causal attribution and InSAR displacement remain null.

`flood-characterize` returns measured signatures, independent optical-product transitions, baseline-only matched controls, conditional spatial intervals, trajectories, observed timing and radar-signature objects. Optical agreement is not ground-truth accuracy; mask area is not confirmed inundation extent. `complete` describes processing support, not a validated phenomenon label. Null recall with no reference positives is meaningful. Preserve unsupported/mixed groups and separate stable open water from newly open water.

`insar-readiness` publishes actual correction-input eligibility and missing gates. Three common eligible cells in the wider Kumamoto stack do not meet the declared reference policy. Supplied screens are not applied, and no stable-ground or displacement claim follows from high coherence. Full new arrays regenerate offline from the release fixtures; source product IDs, checksums, sensing timestamps and optical license metadata are retained.

The existing 160 m frozen Transformer is unchanged. The new 40 m flood cubes are not silently passed through a checkpoint trained for different posting. The prior spectral/stochastic/learned workers remain available under their existing measurement and record-length gates. No frontend or mockup work is included.

## Previous additions: version 0.3

The [v2 experiment report](docs/MODELING_V2.md) and [model card](models/global_explorer_v2/MODEL_CARD.md) cover the new frozen candidate and InSAR interval worker. There are thirteen 160 m regional cubes, nine training regions, two older development cases and two new geographic tests. The new model improves over v1 but remains slightly worse than interpolation on both new tests. Reconstruction results, latent colors and GP conditional coverage do not establish global physical accuracy.

For v2, use `models/global_explorer_v2/masked_temporal_transformer.pt` explicitly. Native point requests can specify `target_posting_m: 160`; it is mutually exclusive with `aggregate_pixels`. Aggregation must be an integer native block, in linear power before dB. This checkpoint declines other posting/band/polarization semantics instead of silently resampling them. The legacy checkpoint remains available for existing explicit requests.

New offline examples are `contracts/examples/hokkaido-v2.json`, `hokkaido-v2-probe.json`, and `kumamoto-stack-offline-wide.json`. `global-v2.json` and `kumamoto-stack.json` exercise live catalogs/quantitative reads. Public catalog search needs no login; archive reads use credentials only from the backend process environment. Model weights and all three fixed projection files travel together.

### Additional analysis arrays and quality

| Field | Shape | Meaning |
|---|---|---|
| `learned_state`, `learned_embedding` | `[time,y,x,48]`, `[y,x,48]` | Raw contextual representation; includes time/mask response and all selected observed context |
| `contextual_state_change_per_day` | `[transition_time,y,x]` | RMS raw-state difference divided by actual days; not physical motion |
| `dynamic_state` | `[time,y,x,48]` | Frozen actual state minus a constant-trajectory control at identical dates/masks; no causal interpretation |
| `dynamic_rgb_time` | `[time,y,x,3]` | Fixed training-only PCA of the control contrast; no class colors |
| `dynamic_context_change_per_day` | `[transition_time,y,x]` | RMS control-contrast coordinate change/day; not speed, event severity or calibrated anomaly likelihood |
| `maximum_controlled_state_change.tif` | `[y,x]` | Largest supported contrast rate across adjacent observed dates |
| `controlled_dynamics.gif` | measured-date frames | Measured power beside the model control contrast, with fixed colors and uniform playback |

`transition_time` uses each interval's **end acquisition date**; the interval starts at the preceding member of `time`. Zero contrast is a vector property; it need not display as black after PCA. Black pixels in the scientific previews denote unavailable support. Learned quantities use latent units, not calibrated physical units. Raw and controlled RGB projections have separate files; their SHA-256 fingerprints are included in model metadata.

`observations.input_quality` gives valid fraction per date and complete channel/pixel support. `observations.processing` preserves aggregation/excluded-empty-acquisition provenance. `observations.query_point_quality` distinguishes the selected query pixel from the broader chip: it lists measured polarizations per date and observed-date count. A geometric catalog/grid hit is not proof that the center pixel was observed. NoData texture remains masked; gradient-axis orientation is radians counterclockwise from east toward north, modulo pi, with actual x/y posting.

Map probes optionally return `dynamic_latent_at_observed_dates`, and the two state-rate arrays in `descriptors_at_selected_pixel`. Their lengths are T−1 and correspond to adjacent unique acquisition dates. The new `no_valid_observations` outcome means the selection lies inside the grid but contains no supported measurements; `outside_observed_footprint` remains distinct. Do not replace either outcome with zero change.

### InSAR interval contract

The new `insar-stack-request.schema.json` and `insar-stack-result.schema.json` bring the request/result schema count to ten. Python entrypoint: `surface_dynamics.insar_stack.process_stack(request, output_directory)`; CLI: `surface-dynamics insar-stack request.json --output directory`.

`source.mode=local_pairs` accepts `pair_paths` (2–6 trusted backend NPZ paths with checksum sidecars). `catalog_point` accepts longitude/latitude, UTC start/end dates, optional `series_key`, `max_pairs` (2–6) and `extent_km` (2–15). Both dates of every selected pair must be inside the window. One compatible track/pass/frame/bandwidth/polarization series is retained. Native unwrapped grids must share posting/CRS and exact common pixel centers; no implicit phase resampling occurs. The common reference uses at least eight jointly supported pixels; stability is never presumed.

The result has `complete`, `partial` or `no_data` status, explicit pair intervals, coherent-component fractions, common-spatial-support coherence medians/histograms, common-phase reference/support quality, null displacement and artifact hashes. `partial` can still provide measured coherence when no phase reference is available.

The NetCDF contains `coherence[pair,y,x]`, `coherence_interval_contrast[pair_transition,y,x]` (later pair minus earlier), `selected_component_support`, `ionosphere_filled`, `phase_relative_to_shared_pixel_rad`, common-support masks, and separate reference/secondary timestamps. `pair_transition` connects adjacent rows of the pair list; it is **not** a physical time derivative. COGs include minimum coherence, pair-contrast bands and common phase support. The same pixel is used as phase datum in each supported component; component IDs themselves are local and are not compared across pairs.

No phase integration, corrections, SBAS inversion, LOS/vertical motion or earthquake attribution is provided. Different interval durations confound coherence contrast. The real 5 km example is partial with no phase reference; the separately documented 15 km example has 4.57% common support and an unverified reference. These outcomes are useful teaching observations rather than hazard classifications.

## What is implemented

The Python package `surface_dynamics` accepts a measured cube or arbitrary WGS84 point/date window and emits science artifacts plus a validated result manifest. It selects a compatible NISAR geometry/mode rather than mixing orbit directions, frames or polarizations. It can return `no_data` or partial capabilities instead of inventing observations.

The shared analysis includes physical temporal descriptors; spatial Fourier/structure-tensor texture; actual-time generalized Lomb–Scargle spectra and permutation diagnostics; locally weighted time-frequency fits when support permits; proper orthogonal decomposition; empirical-Bayes Matérn-3/2 Gaussian-process smoothing and Monte Carlo historical draws; and a trained masked bidirectional temporal Transformer with fixed global embedding colors and MC-dropout sensitivity. These are descriptive outputs at measured dates, not forecasts.

The panel themes are **agriculture, wildfires, floods and landslides**. The same engine also supports exploration under earthquake, volcano, wetland, ice and subsidence themes. A theme supplies questions/context; it does not change a measurement into a predicted event class.

### Version 0.2 additions: InSAR networks and stronger validation

`surface_dynamics.network.plan_network(request, output_directory)` and the CLI `insar-network` discover compatible GUNW acquisition networks. Two additional schemas, `insar-network-request.schema.json` and `insar-network-result.schema.json`, publish this contract. See `contracts/examples/kumamoto-network.json` and `docs/results/kumamoto_network.json` for a real request and response. Source mode is either `catalog_point` (longitude, latitude, start/end UTC dates) or trusted `local_catalog` (`catalog_path` to a recorded catalog response). `max_baseline_days` is a temporal filter; `event_date` supplies optional bracketing context.

Each separate track/pass/frame/bandwidth/polarization series returns date nodes, real source-ID edges with reference/secondary dates, day baselines, context bracketing, connected components, a minimum temporal-baseline forest and independent cycle paths. Dates remain in the graph if baseline filtering isolates them. Revisions of the same day pair do not create artificial parallel cycles. Catalog queries filter reference acquisitions; secondary dates can extend outside the requested window. This is network planning: quantitative common support, phase closure and corrected SBAS displacement are not computed. The real Japan catalog has three compatible chain networks and zero redundant cycles, so no closure check is available from those edges.

Pair `quality.observability` now supplies a coherence-threshold sensitivity table, connected-component counts, selected-component ionosphere-filled fraction and wrapped-coherence quantiles. Filled ionosphere flags are retained separately from data anomalies. Every local/downloaded pair is checked for native-grid consistency, projected metres, integer masks, valid coherence domain and positive finite center frequency. Empty coherent support yields a typed `no_data` result rather than an exception masquerading as success. Cached downloads require the same source, point and extent.

The CLI `benchmark --cubes ... --checkpoint ... --output ...` evaluates a frozen checkpoint with each internal observed acquisition hidden in turn. It uses identical target support for the Transformer, visible temporal mean and actual-time interpolation, including per-channel missing neighbors. It does not update the model. Full two-region results are in `docs/results/model_benchmark_all_dates.json`.

## Run the worker

From this workspace in PowerShell:

```powershell
.venv\Scripts\python.exe -m surface_dynamics analyze contracts\examples\forest-river.json --output products\runs\forest_river
.venv\Scripts\python.exe -m surface_dynamics insar contracts\examples\kumamoto-earthquake.json --output products\runs\kumamoto-earthquake
.venv\Scripts\python.exe -m surface_dynamics probe contracts\examples\map-probe.json --output products\probes\forest-map-probe.json
```

The equivalent Python entrypoints are `surface_dynamics.engine.analyze(request, output_directory)`, `analyze_insar(request, output_directory)`, and `surface_dynamics.probe.probe(request)`. They are synchronous worker functions. No HTTP service, task queue or UI is included; the backend team can wrap them in its existing job system.

Use `.venv`, the isolated CPU runtime. The older `scratch/.venv` inherits Conda site packages and exhibited a duplicate OpenMP-runtime conflict when combining Torch and SciPy; it is retained for historical experiments, not recommended for this package. See `requirements-lock.txt` and `SCIENCE_IMPLEMENTATION.md` for installation and measured verification.

## Input contracts

Versioned JSON Schemas live in `contracts/` and are included in the package wheel under `surface_dynamics/schemas/`:

| Contract | Purpose |
|---|---|
| `analysis-request.schema.json` | Local measured cube or global catalog point; requested analysis modules; optional independent event context |
| `analysis-result.schema.json` | Module capabilities, observations, artifact manifest, supported insights and interpretation limits |
| `insar-request.schema.json` | Existing measured pair or global point/event-date bracketing search |
| `probe-request.schema.json` | Extract a measured pixel/region trajectory and descriptors from an existing analysis NetCDF |
| `insar-result.schema.json`, `probe-result.schema.json` | Typed InSAR and map-probe responses, including unavailable/correction-state outcomes |

`phenomena-catalog.json` supplies the exploration questions, measured inputs, candidate physical mechanisms and limitations for each theme. `python -m surface_dynamics capabilities` returns that catalog. Themes do not select a classifier.

Example request:

```json
{
  "contract_version": "1.0",
  "request_id": "world-point-demo",
  "theme": "floods",
  "source": {
    "mode": "catalog_point",
    "longitude": 73.97,
    "latitude": 32.41,
    "start_date": "2026-06-01",
    "end_date": "2026-10-31",
    "max_dates": 8,
    "native_chip_pixels": 512,
    "aggregate_pixels": 4
  },
  "analyses": ["physical", "spectral", "stochastic", "transformer"],
  "transformer_checkpoint": "models/global_explorer_v1/masked_temporal_transformer.pt",
  "stochastic_draws": 256
}
```

Longitude comes first. Request dates are UTC calendar dates, inclusive. Real native posting controls the footprint: 512 pixels is 5.12 km at 10 m or 10.24 km at 20 m. Output grid metadata gives actual coordinates, CRS, bounds and spacing. An AOI larger than one chip needs backend tiling; this worker does not claim complete world mosaics in one request. Dateline and polar points are queried as WGS84 points, with native projected output or honest no-data status.

The result identifies the selected compatible series, available/selected date counts and the selection policy. Capped dates can be deliberately subsampled, so gaps between selected frames are not evidence of satellite outages.

### Local measured cube

NPZ arrays, `allow_pickle=False`:

| Key | Shape / type | Meaning |
|---|---|---|
| `power` | `[T,C,H,W]`, float32 | Positive **linear gamma0 power**, not dB, amplitude, RGB or a browse image |
| `valid` | matching bool mask | Observed valid support; invalid samples are not training targets |
| `dates` | `[T]`, increasing unique ISO dates | Actual acquisitions; no interpolated dates |
| `polarizations` | `[C]`, strings | Exact measured HH/HV/VV/VH semantics |
| `x`, `y` | `[W]`, `[H]` | Regular projected pixel-center coordinates in metres; east-positive, north-up |
| `epsg` | scalar integer | Projected metric CRS; geographic-degree arrays must be reprojected explicitly |
| `radar_band` | scalar `L` or `S` | Actual instrument band; A/B under LSAR are not L/S |
| `source_metadata_json` | scalar JSON string | Granule URLs, versions, flags, checksums and acquisition provenance |

Array limits are T≤128, C≤4, H/W≤1024 and at most 16,777,216 measured channel/pixel/date values. The learned-state branch additionally permits at most one million pixel/date vectors; exceeding that budget returns a partial result with a reason to coarsen the grid. At least three acquisitions and sufficient measured support are required. Package chip acquisition uses a stricter 3–32 selected-date request limit and ≤512 native pixels. Interpolation for diagnostics never overwrites the observed cube.

## What the frontend/backend team receives

Each analysis writes `result.json` with:

- `status`: `complete`, `partial` or `no_data` describes job/data availability, not scientific validation.
- `capabilities`: each requested module reports `available`, `experimental`, `insufficient_data`, `unsupported_domain`, or `failed`; unrequested modules remain `not_requested`.
- `observations`: actual dates, named polarization channels, grid/CRS/bounds, source and processing provenance.
- `artifacts`: relative `href`, MIME type, SHA-256, role, units, dimensions and a legend where applicable.
- `insights`: numerical observations, their evidence artifact IDs, and a separate interpretation limit. These are descriptive sentences, not event predictions.
- `limitations`: machine-readable interpretation boundaries for the returned job.

Artifact paths are relative to the job directory, never authenticated signed URLs. The backend should resolve job IDs and serve allowlisted artifacts. The worker's local file/checkpoint paths are **trusted backend inputs**; do not forward arbitrary browser-provided filesystem paths into them. Supply Earthdata credentials in the worker's `EARTHDATA_TOKEN` environment, or set `EARTHDATA_TOKEN_DPAPI_PATH` to an existing current-user Windows-protected blob; the worker only reads it. Credential fields are rejected in request JSON. The offline examples do not need authentication.

### Scientific outputs

| Artifact / variable | Useful meaning | Required interpretation |
|---|---|---|
| `median_db`, `mad_db`, `slope_db_per_day`, `maximum_step_db` COGs | Typical return, variation, measured trend and largest acquisition-to-acquisition difference | Not crop, burn, water or landslide labels |
| `valid_date_count` COG | Actual temporal support | Missing coverage is not a surface class |
| `regional_timeseries.csv` | Named-channel trajectories with 50/80/95% spatial block-bootstrap bands | Spatial heterogeneity of mean dB, not sensor-error confidence |
| `surface_dynamics.nc` | Observations, all feature maps, GP means/SD, POD modes, learned vectors, fixed RGB and coordinates | CF-style coordinates plus WKT/EPSG; invalid data remain NaN |
| `candidate_period_days`, `peak_normalized_power` | Strongest bounded actual-time spectral candidate | No per-pixel significance or confirmed seasonality |
| `spectral_arrays.npz` | Periodograms, sampling window, spatial FFT, optional local time-frequency array | Spectral peaks can be sampling artifacts; regional permutation null has assumptions |
| `modal_arrays.npz` | POD spatial modes, temporal coefficients and explained variance | A mathematical decomposition, not a discovered natural law |
| `stochastic_draws.npz` | Full conditional historical GP draws `[draw,polarization,time]` | Empirical-Bayes model assumptions; uncalibrated; no forecasts |
| `learned_arrays.npz` | Shared 48-dimensional vectors at each observed date, pooled embedding and dropout sensitivity | Bidirectional context from the selected interval; experimental, no event probabilities |
| `physical.png`, `spectral.png`, `stochastic.png`, `learned.png` | Reproducible static scientific views | Read their dates, physical scales and limits |
| `observed_evolution.gif` | Measured scattering evolution with fixed colors | Uniform playback; unequal acquisition intervals; no generated observations |
| `learned_evolution.gif` | Observed power beside contextual Transformer states at observed dates | Fixed shared projection; derived states use all selected dates, not online/forecast states |

COGs preserve native projected coordinates and use NaN nodata. Continuous-power overviews use averages; angular/mask products use nearest-neighbor overviews. JSON contains `null`, never NaN/Infinity. Raw power is averaged before dB; regional mean dB is explicitly a geometric-power statistic.

## Map exploration input

`probe` takes the output NetCDF, longitude/latitude and optional radius in metres. A single-pixel query returns the measured trajectory and descriptors with no fabricated spatial confidence interval. A sufficiently supported region also returns spatial bootstrap bands. An out-of-footprint coordinate returns `outside_observed_footprint`; a region is clipped to actual observed support.

## Earthquake/InSAR contract

The InSAR branch selects a real pair bracketing a supplied event date or reads an existing measured pair. It provides wrapped radians, coherence, component-filtered referenced unwrapped radians, ionosphere-filled flags and **phase-equivalent centimetres**. One NISAR L-band fringe is about 12.1 cm for the actual frequency in the demonstrated pair.

`los_displacement_cm` and `deformation_attribution` are explicitly `null`: atmospheric/ionospheric/tide signals have not been removed and a stable geodetic reference/sign has not been validated. Phase-equivalent cm is a wavelength teaching scale, not a deformation estimate. A single selected component does not reference other disconnected islands. Coherence is not a confidence probability.

The Kumamoto demonstration uses the real **21 July→14 August 2026** NISAR pair around the independently documented 28 July earthquake. The interval can contain coseismic, postseismic and atmospheric changes together. The separately retained GSI vertical raster is a credited **external ALOS-2/ALOS-4/NISAR multi-mission analysis**, not a result of our model. Its downloaded file lacks unit/nodata metadata; it is not exposed as a quantitative worker result until those definitions are verified. [GSI source/DOI](https://www.gsi.go.jp/uchusokuchi/20260728kumamoto-e.html)

## Domain support and honest failure behavior

The physical/spectral/stochastic code has no fixed country or event label. Real smoke tests span North America, Europe, Asia, Africa, South America and Australia. This is worldwide **processing capability**, not globally proven model accuracy. The compact Transformer was trained on five regional HH/HV L-band cases and geographically tested on two other cases; its checkpoint declines unseen polarization/band semantics rather than silently mapping VV/VH or S-band into trained HH/HV inputs. It must remain labelled experimental.

Sparse records, no complete trajectories, unsupported learned channels, missing phase, weak coherence and disconnected components are explicit capability/quality outcomes. A failed input contract raises a validation error before analysis. Archive authentication or network failures raise worker errors for the backend job system; no substitute sensor, synthetic data or empty success is silently inserted.

## Useful learning questions for the four panels

- **Agriculture:** Which parcels brighten at different times? Is cross-pol change different from co-pol change? Neither answers crop type or harvest cause alone.
- **Wildfires:** Does the measured response persist across the independently reported fire interval? Absence of a strong SAR signal is an informative result; no burn perimeter is inferred.
- **Floods:** Are decreases consistent with smooth open-water scattering, or increases with vegetation/structure interaction? Neither is a validated inundation extent or depth.
- **Landslides:** Are there localized scattering/coherence changes? Do measurements actually bracket the event? Terrain visibility, DEM/offset/geodetic evidence are needed for movement interpretation. The Niscemi example is aftermath-only.

Do not present stochastic draws or embedding colors as a classification result. The model comparison, actual validation numbers, references and remaining limits are in `SCIENCE_IMPLEMENTATION.md` and the model card.
