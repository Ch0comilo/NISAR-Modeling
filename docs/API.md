# NISAR Measured Surface Dynamics API v2

The API serves provisional or beta NISAR L-band observations and descriptive science. The API version is 2.0. Wrapped phase is in radians and coherence is unitless. GCOV backscatter is gamma-zero dB and signed differences are later minus comparator. Field null means unavailable. Zero is a measured value or a count when the support definition permits it.

## Routes

| Method and path | Response |
| --- | --- |
| GET /health | API and worker versions, normalized build fingerprint in `revision`, and source `git_sha` when available |
| GET /v2/capabilities | Deployed identity, supported visualization recipes, input limits, quotas and unsupported science claims |
| GET /v2/openapi.json | Generated OpenAPI with JSON Schema components from the Pydantic models |
| GET /v2/coverage | Public CMR footprint records grouped by compatible series, with exact acquisition dates and granule titles |
| GET /v2/cases | Precomputed case index with query points, north-up native CRS, pixel-edge bounds, shape [height,width], and measured dates |
| GET /v2/cases/{case_id} | Manifest for chokwe, qadirabad, richmond, kumamoto or choco when included in the deployed bundle |
| GET /v2/cases/{case_id}/series | Observation and group trajectories, optional diagnostic-band timeline, or GUNW pair intervals |
| GET /v2/cases/{case_id}/layers/{name} | Published PNG, native COG, measured arrays or CSV |
| POST /v2/jobs | Submit a bounded analysis and return its job_id |
| GET /v2/jobs/{job_id} | Job status, error or result manifest |
| GET /v2/jobs/{job_id}/layers/{name} | An artifact listed in the job manifest |
| GET /frames/{track}-{frame}/analysis | Curated legacy URL with explicit contract=2 opt-in |

## Radar visualizations

Prepared GCOV case manifests and completed `explore` job results include `visualizations`. Each view supplies `id`, `recipe`, `title`, actual UTC `dates`, `interpretation`, `components`, `colormap`, `supported_cells` and `total_cells`. The counts are on the native grid before display reprojection. A cell must be valid for all inputs to the recipe. `grid` describes the numeric COG's native CRS, pixel-edge bounds, [height,width] and [x,y] posting in metres. `bounds` and `crs` describe the north-up display PNG in EPSG:4326. Use the individual PNG bounds when draping.

| Recipe | Computation and display |
| --- | --- |
| `polarization_rgb` | On the target date, R = co-pol dB [−25,0], G = cross-pol dB [−32,−5], B = cross-pol minus co-pol dB [−18,−2]. Each measured HH/HV or VV/VH pair can produce its own view. This is false color for scattering balance. |
| `cross_co_ratio` | Cross-pol dB minus co-pol dB, equivalent to 10 log₁₀(cross/co linear power). Viridis [−18,0] dB. |
| `cross_co_ratio_change` | Later cross/co log ratio minus earlier cross/co log ratio on same-grid joint support across both dates and channels. Diverging [−8,+8] dB. The prepared case uses its last baseline pass and first assessment. The live job uses its measured A/B pair. |
| `three_date_rgb` | Assign co-pol to R/G/B at the first, lower middle and target usable acquisitions. Each display range is [−25,0] dB. Three distinct dates are required. |
| `temporal_median` | Per-channel median over at least three usable dates through the target, on complete per-channel support. Grayscale [−30,0] dB. |
| `temporal_mad` | 1.4826 × median absolute deviation over the same dates and support. Magma [0,8] dB. This describes observed temporal variability. |

`components` gives band order, label, source polarizations and fixed `display_db` range. RGB COGs carry three unclipped float dB bands for client-side mixing. Scalar COGs carry one unclipped float band. `png_url` and `cog_url` are relative to the API origin. NoData is transparent in PNGs. Live `artifacts` includes content hashes and sizes. Layer responses carry ETags.

Live jobs prioritize display PNGs and chart outputs within the bounded artifact bundle.
A live layer can have `cog_url: null` when the numeric asset exceeds the publication budget.
Use the PNG for the view and offer numeric downloads only for published assets.

Unavailable recipes are omitted. A missing target does not borrow a different date. An HH/VH-only observation has no matched co-/cross-pol view. Incompatible native grids withhold cross-date combinations. Original measurements and signed channel differences remain in `layers`. Derived views describe measured L-band inputs and cannot identify a material or validate a hazard.

`analysis` jobs can return `previews` with `href`, `title`, `interpretation` and optional `legend`. Available figures can include the physical dashboard, observed-date GIF, historical GP fit and experimental spectra or learned-model views. GIF playback spacing is cosmetic. Prepared Kumamoto also includes a common phase-support mask and adjacent-pair coherence contrasts. Pair durations may differ. Live GUNW wrapped phase is in radians. LOS displacement remains null.

`visualization_seconds` measures job layer rendering from existing inputs. Derived views do not trigger archive reads. The one-minute roadmap target requires measurement by request tier, including archive retrieval and queue time.

The CORS allowlist is https://nisar-hazard-watch.vercel.app and http://localhost:5173. All layer URLs are relative to the API origin. Use a fetch wrapper that preserves an AbortSignal and parses JSON errors with message and code. The API permits byte ranges and serves ETag and If-None-Match on artifacts. Case metadata and assets use public max-age=300. Coverage uses max-age=60. Job and error responses use no-store.

A measured Chókwè example uses the last measured baseline pass and first assessment pass. The manifest and assets were generated offline from the published measured release fixtures.

    GET /v2/cases/chokwe
    GET /v2/cases/chokwe/series
    GET /v2/cases/chokwe/layers/change_db_hh.png
    GET /v2/cases/chokwe/layers/change_db_hh.tif
    GET /frames/114-165/analysis?hazard=flood&before=2026-01-05&after=2026-01-17&contract=2

In this fixture the darkening signature covers 2,880 supported 40 m cells, or 460.8 nominal analysis-cell hectares. The interpretation is optical_open_water_change_with_weak_or_mixed_radar_response. The processing state is partial. The COG's grid is EPSG:32736 with native bounds [496020,7284100,501140,7289220] and shape [128,128]. The display PNG uses its own manifest bounds in EPSG:4326 and transparent nodata. Use the layer bounds on the globe.

Chocó uses the 18 July to 11 August 2026 GUNW coherence interval from ascending
track 148, frame 004. The bracketing interval has median coherence 0.247162
over 3,844 finite cells on the 62 × 62, 80 m EPSG:32618 grid. The 11–23 August
pair contributes to the correction-readiness check and is listed separately in
the series. The coherence PNG uses cividis on [0,1] with its own display bounds.
Readiness reports zero common phase-support cells, `not_displacement_ready`,
and `los_displacement_cm: null`. The case is available through the v2 case
routes.

### Coverage and jobs

    GET /v2/coverage?longitude=-97.7&latitude=48.2&start_date=2026-10-07&end_date=2026-10-19&product=GCOV

Submit the observed Forest River comparison from the science example.

    POST /v2/jobs
    Content-Type: application/json

    {
      "operation": "explore",
      "longitude": -97.7,
      "latitude": 48.2,
      "start_date": "2026-10-07",
      "end_date": "2026-10-19",
      "series_key": "105_A_026_4005_DHDH",
      "compare_dates": ["2026-10-07", "2026-10-19"]
    }

A new submission returns 202. Poll the Location or status_url after three seconds and back off. A repeated active submission returns the same ID with cache=in_flight. A completed match returns 200 with cache=hit. A fresh submission has cache=miss. The measured point is snapped to a 0.001-degree lattice and returned explicitly. Cache identity includes operation, canonical arguments, date window, and the hash of the worker sources, schemas and frozen model assets. Recent catalog revisions can become visible to a repeated job after the 24-hour cache expires.

The API admits five new jobs per client IP and 12 across the app per UTC day. The counters reset at midnight UTC. Catalog queries, polling, cache hits, and in-flight duplicates are exempt from these job quotas.

| Operation | Inputs and result |
| --- | --- |
| explore | Point history and optional compare_dates, series_key, lens, optical_evidence and baseline_end_date |
| analysis | Optional series_key and analyses from physical, spectral, stochastic and transformer |
| characterization, flood, fingerprint | Required baseline_end_date splits two inclusive acquisition windows |
| insar-pair | Requires pair_id from GUNW coverage, with optional coherence_threshold |
| insar-network | GUNW network plan from the point and inclusive window |
| insar-stack, insar-readiness | Optional compatible series_key and coherence_threshold, with up to three same-series 5 km GUNW pairs |

Queued and running describe work in progress. Succeeded means the worker returned a scientific result. Its science_status may be complete, partial, no_data, or not_displacement_ready. Failed means the result could not be produced, including an upstream read or a deadline. Complete is processing/support. Coverage responses keep pixel_support=unchecked. Lens evidence can describe observed_departure, within_observed_baseline or insufficient_data, and carries independent evidence separately.

The public interface allows point windows up to 366 days. `explore` keeps its five-date cap. The existing `analysis` operation selects at most six compatible measured dates when its `analyses` list includes `spectral`; other analysis jobs retain five. This sixth date allows the existing spectral support gate to be met. The UI can default to the most recent five months. Actual date selection follows measured catalog availability in a compatible series. No pass is synthesized to meet nominal monthly spacing. The live explore chip is a 64-pixel native window and the scientific workers use their own bounded native reads. Each case publishes the measured footprint and grid. Arbitrary polygons and an invariant 10 km grid are future work. The guided one-minute view should use the precomputed cases. A live job can take up to four minutes of worker execution, plus queue and startup time.

## Lens and temporal evidence

Existing request fields, lens IDs and result fields remain valid. An evaluated lens can carry `science.lens.temporal_analysis` on its actual dates and common radar support. Read the spectral and stochastic statuses separately. The spectral card records the sampling gaps and withholds candidate periods below six dates. The historical GP needs five measured dates on the lens path and at least eight complete cells per channel. Its bands are conditional on the model. Constant histories and failed fits leave the model bands null. The GP does not supply sensor uncertainty or change the lens verdict. Lens diagnostics reuse the loaded radar window.

Flood and agriculture can also return a versioned `science.lens.evaluation` companion. Flood adds joint-darkening and co-pol brightening flags, baseline-matched surroundings and separately dated optical water evidence. Agriculture adds baseline-matched focal/control trajectories, a material step test and sampled synchrony. The old point result, flood counts, agriculture annulus series and independent optical status remain available. Render available diagnostic PNG/COG layers from `result.layers`, or read native diagnostic artifacts by `science.lens.radar.layers`. Signature states describe the sampled radar behavior or a named optical claim. Causal attribution, phenomenon class/probability and displacement stay null. [Flood evaluation schema](../contracts/flood-lens-evaluation.schema.json) · [Agriculture evaluation schema](../contracts/agriculture-lens-evaluation.schema.json)

For standalone spectral and stochastic results, submit the existing `operation: "analysis"` with `analyses: ["spectral", "stochastic"]`. Spectral jobs read up to three measured archive windows in parallel, with the same per-window byte limit and grid checks. When supported, `spectral_series.csv` carries the frequencies, candidate periods, sampling window and regional spectral power. A frequency is fitted jointly with a linear trend on observed timestamps. The permutation diagnostic assumes exchangeable residuals. Candidate periods from short series are tentative. `gp_historical_series.csv` carries measured means, support and conditional 80%/95% latent bands where the channel fit converges. Use the existing job layer routes and `artifacts` links for both CSV files.

## Dashboard data

| Panel | Read from |
| --- | --- |
| Prepared case timeline and evidence | Case manifest and its `series_url`. Series can include point and group dB histories, diagnostic-band counts, or GUNW pair intervals. |
| Live radar timeline and comparison | `result.science.observations` and `result.science.comparison`. Read the actual dates, per-pass point power, source and support. |
| Combined radar imagery | `result.visualizations` or case `visualizations` with component labels, actual dates, measured support, display PNG and numeric COG. |
| Lens | `result.science.lens` with the lens ID, radar evidence, point readout and available `evaluation` and `temporal_analysis`. Use `result.layers` for map overlays. |
| Standalone charts | `result.science.dashboard.series` contains JSON rows from `regional_timeseries`, `spectral_series` and `gp_historical_series` when those tables exist. Keys match the CSV column headers. Numeric gaps are null. The dashboard also supplies `acquisition_gaps_days`, in order between consecutive `science.observations.dates`. |
| Analysis overlays | `result.layers` lists supported per-polarization median, temporal MAD, slope, largest absolute step and valid acquisition count for physical jobs. Spectral jobs can add tentative period in days and normalized spectral power. Display IDs begin with `display_` and end with the channel. Use each layer's PNG bounds, palette and range, plus its COG for native values. The period range is the job's scanned period band. |
| Figures, downloads and scientific arrays | `result.previews` lists available figures and animations. `result.artifacts` lists CSV, PNG and georeferenced raster downloads and available NetCDF with coordinates, units and support arrays. |

### Observed passes for the time-lapse

In a live `explore` result, select `result.layers[]` by the exact ID
`display_<YYYY-MM-DD>_<pol>`, for example `display_2026-10-19_HH` or
`display_2026-10-19_HV`. The date is the measured UTC acquisition date and
`<pol>` is the uppercase channel (`HH`, `HV`, `VV`, or `VH`) when delivered.
Match the acquisition against `result.science.observations[].date` and use the
layer's `png_url`, `bounds`, and `crs` for the overlay. Observed-pass layers
have `units: "dB"`, `colormap: "gray"`, and `range: [-30, 0]` across dates and
polarizations. Pixels without valid data are transparent. Use the published
`cog_url`, when available, for numeric values.

Treat a missing date/channel layer as unavailable for playback. The `display_`
prefix also appears on change and diagnostic layers, so match the full date
and channel ID when looking up an observed pass.

For standalone charts, read `science.capabilities` to decide which panels to show and `science.observations.polarizations` for channel order. Regional intervals from spatial block resampling and GP conditional bands describe different quantities. Spectral power is dimensionless and represents a fit score. The observation dates and sampling window are part of interpreting each spectrum.

Spectral requests can publish `spatial_spectrum.png` independently of temporal period support.
This preview shows a 2D FFT of the first measured channel's temporal-median image. Its axes are
native grid east and north in cycles/km. The worker fills missing cells with the image median,
removes the mean, applies a Hann taper and excludes DC. The legend reports finite-median support
and the display shows power relative to the peak, clipped below −45 dB. Flat or unsupported
images omit the figure. The temporal `spectral.png` and `spectral_series` describe candidate
periods in days and retain their separate temporal support gates.

## Frontend mapping

| Draft field or copy | v2 rendering |
| --- | --- |
| kind and hazard | The legacy URL retains the two existing routing tags. Use the hazard as context in titles such as “Measured radar change” and “Interferometric coherence.” |
| before and after | Legacy v2 uses actual UTC dates. Use the manifest sources and pair metadata for the granule identity. The baseline comparison names all baseline dates. |
| affectedAreaHa, “ha burned/flooded/cleared” | Null. Use evidence.threshold_cell_area_ha with “ha of analysis cells meeting the radar rule,” plus threshold_policy and support counts. |
| meanChangeDb | Use the measured per-group dB trajectory or report comparator. Keep polarization, sign, comparator and valid support alongside the value. |
| severityHa and severityUrl | Null or absent. Use diagnostic signature_mask and the independently sourced reference_groups with their own legends. |
| displacementUrl, colorScaleCm, maxTowardCm, maxAwayCm, deformedAreaKm2, deformedThresholdCm | Unavailable. Use the date-labeled coherence layer on a fixed 0–1 scale. Wrapped phase, when available, uses radians and a cyclic palette. |
| Ground motion/peak cm/area moved | Display “Interferometric coherence” or “Wrapped phase (rad)” and the correction/reference state. Keep los_displacement_cm null. |
| beforeUrl and afterUrl | Use baseline_median_HH/HV and target_HH/HV layer IDs in lowercase. Both use a fixed −30 to 0 dB gray stretch. |
| layers.bounds | Read the bounds on each Layer. Use the layer's PNG for draping and its COG for native scientific values. |
| generatedAt and isDemo | Use measured dates and catalog_queried_at_utc where supplied. isDemo=false accompanies measured products. Keep seeded and unsupported UI content marked DEMO or TO FILL. |
| Band chart and uncertainty | Group IQR is spatial heterogeneity. Point dB is the configured case pixel or the live job's measured pixel. Observation noise is unavailable. GP conditional latent uncertainty is separate and appears only where supported by the analysis worker. |
| Timeline bar | Label “Cells outside the diagnostic band” and show numerator and denominator. Null means no eligible denominator. |
| Retry and empty states | Handle 202/polling, evidence no_data/partial, 409 contract opt-in, 422 schema, 429 limits, 503 capacity and upstream 502/504. |

The existing frontend assumes several numeric hazard claims are non-null. It must update its TypeScript types and renderers before switching VITE_ANALYSIS_API_URL. A request without contract=2 receives HTTP 409. Arbitrary frame requests should use coverage and POST jobs. The legacy GET serves exact curated matches and never starts a job.

JSON Schemas for public typed models are generated with Pydantic, and FastAPI uses those same types. Existing worker response payloads under science retain their own versioned JSON Schema where one exists. Case and layer manifests are versioned independently from the current science code revision.
