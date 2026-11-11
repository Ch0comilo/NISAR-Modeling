# NISAR Measured Surface Dynamics API v2

The API serves provisional or beta NISAR L-band observations and descriptive science. The API version is 2.0. Wrapped phase is in radians and coherence is unitless. GCOV backscatter is gamma-zero dB and signed differences are later minus comparator. Field null means unavailable. Zero is a measured value or a count when the support definition permits it.

## Routes

| Method and path | Response |
| --- | --- |
| GET /health | API and worker-package versions |
| GET /v2/capabilities | Deployed revision, input limits, daily job quotas, data band and unsupported science claims |
| GET /v2/openapi.json | Generated OpenAPI with JSON Schema components from the Pydantic models |
| GET /v2/coverage | Public CMR footprint records grouped by compatible series, with exact acquisition dates and granule titles |
| GET /v2/cases | Precomputed case index with query points, north-up native CRS, pixel-edge bounds, shape [height,width], and measured dates |
| GET /v2/cases/{case_id} | Manifest for chokwe, qadirabad, richmond or kumamoto |
| GET /v2/cases/{case_id}/series | Observation and group trajectories, optional diagnostic-band timeline, or GUNW pair intervals |
| GET /v2/cases/{case_id}/layers/{name} | Published PNG, native COG, measured arrays or CSV |
| POST /v2/jobs | Submit a bounded analysis and return its job_id |
| GET /v2/jobs/{job_id} | Job status, error or result manifest |
| GET /v2/jobs/{job_id}/layers/{name} | An artifact listed in the job manifest |
| GET /frames/{track}-{frame}/analysis | Curated legacy URL with explicit contract=2 opt-in |

The CORS allowlist is https://nisar-hazard-watch.vercel.app and http://localhost:5173. All layer URLs are relative to the API origin. Use a fetch wrapper that preserves an AbortSignal and parses JSON errors with message and code. The API permits byte ranges and serves ETag and If-None-Match on artifacts. Case metadata and assets use public max-age=300. Coverage uses max-age=60. Job and error responses use no-store.

A measured Chókwè example uses the last measured baseline pass and first assessment pass. The manifest and assets were generated offline from the published measured release fixtures.

    GET /v2/cases/chokwe
    GET /v2/cases/chokwe/series
    GET /v2/cases/chokwe/layers/change_db_hh.png
    GET /v2/cases/chokwe/layers/change_db_hh.tif
    GET /frames/114-165/analysis?hazard=flood&before=2026-01-05&after=2026-01-17&contract=2

In this fixture the darkening signature covers 2,880 supported 40 m cells, or 460.8 nominal analysis-cell hectares. The interpretation is optical_open_water_change_with_weak_or_mixed_radar_response. The processing state is partial. The COG's grid is EPSG:32736 with native bounds [496020,7284100,501140,7289220] and shape [128,128]. The display PNG uses its own manifest bounds in EPSG:4326 and transparent nodata. Use the layer bounds on the globe.

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

The public interface allows point windows up to 366 days and up to five dates per live radar job. The UI can default to the most recent five months. Actual date selection follows measured catalog availability in a compatible series. No pass is synthesized to meet nominal monthly spacing. The live explore chip is a 64-pixel native window and the scientific workers use their own bounded native reads. Each case publishes the measured footprint and grid. Arbitrary polygons and an invariant 10 km grid are future work. The guided one-minute view should use the precomputed cases. A live job can take up to four minutes of worker execution, plus queue and startup time.

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
