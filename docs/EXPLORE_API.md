# Measured point exploration API

Run the API from the repo root after installing the package and configuring the existing Earthdata credential provider for the worker process. Catalog discovery and previously cached chips also work without an Earthdata credential.

```powershell
.\.venv\Scripts\python.exe -m surface_dynamics serve-explore --cache scratch/explore-cache
```

The server listens on `127.0.0.1:8787`. Use `--port` to change the port. Give each running server its own cache directory. The prototype runs one archive worker and one read-only worker for fully cached requests. It accepts up to four active archive jobs and two active cached jobs, including running jobs. The last 32 jobs and their TIFFs are held locally for the life of the process. The native chip cache persists across restarts and is managed by the operator.

| Route | Response |
|---|---|
| `GET /v1/coverage?longitude=-97.7&latitude=48.2&start_date=2026-10-07&end_date=2026-10-19` | CMR footprint count, compatible series, UTC dates, and an indicator for the supported frequency. Pixel support is unchecked at this stage. |
| `POST /v1/explore` | Performs the same catalog lookup and returns coverage and a job. HTTP 202 indicates active work. HTTP 200 returns a completed, reused, or empty result. |
| `GET /v1/jobs/{job_id}` | Selected dates and series, attempted series, observations as they arrive, point and window support, and the signed date comparison when ready. Poll about once per second while queued or running. |
| `GET /v1/jobs/{job_id}/assets/{name}` | Downloads a TIFF published in that job's manifest. Each TIFF has a native projected grid, named polarization bands, and NaN nodata. |
| `GET /v1/lenses` | Lists the available evidence lenses. Add a lens identifier to a point request to include its result. Set `optical_evidence` to `true` to run the lens's independent optical stage. |
| `GET /v1/insar/pairs` | Lists GUNW footprint matches at a point. Use the same query fields as coverage. Both acquisition dates must fall inside the requested UTC window. |
| `POST /v1/insar` | Queues one selected GUNW pair for a measured 5 km window. Poll the same job and asset routes. |

Forest River request body for a two-date comparison:

```json
{
  "longitude": -97.7,
  "latitude": 48.2,
  "start_date": "2026-10-07",
  "end_date": "2026-10-19",
  "series_key": "105_A_026_4005_DHDH",
  "compare_dates": ["2026-10-07", "2026-10-19"],
  "max_dates": 2
}
```

Only the four point and date fields are required. The API accepts an inclusive UTC window of up to 366 days and selects up to six recent acquisitions in one compatible provisional L-band GCOV frequency-A series. The comparison uses the two newest selected dates by default. Specify `compare_dates` for another pair within one series. The worker reads the later comparison date first, then the earlier date, then the rest of the selected history.

When an automatically selected series has zero usable pixels across every selected date, the worker can try one alternate in catalog rank order. For a lens, the alternate must offer co- and cross-polarization with at least three selected baseline dates and an assessment date. The retry preserves an explicit `compare_dates` pair. Supplying `series_key` pins the series. A partial measurement or an archive error ends the radar attempt without changing series. Up to six dates may be read per attempt.

`series_attempts` records completed radar attempts with the key, dates, radar status and usable-window date count. While a retry runs, the job stays active and `series_key`, `selected_dates` and `observations` reflect the new series. All layers and the optical stage use the final series. Jobs with an uncached candidate are admitted through the archive queue so concurrent requests have a single chip-cache writer. `optical_evidence_requested` distinguishes a radar-only request from one that asked for independent optical evidence. `lens.evidence` reports what the optical stage could establish and why a comparison was withheld when applicable.

The observations return measured gamma-zero power in dB at the clicked pixel, a source ID and UTC timestamp, native grid metadata, measured support counts and source processing flags. The comparison subtracts earlier dB from later dB on identical native grids with common finite positive focused-power support. It reports signed dB, common pixel counts, and its TIFF. A window can contain usable imagery when the clicked pixel is masked. Missing point values are JSON null. The support test describes focused power and does not establish signal-to-noise or terrain quality.

Job states are `queued`, `running`, `complete`, `partial`, `no_data`, `unavailable`, and `failed`. A single valid observation can complete with an unavailable comparison. A catalog miss, unsupported frequency, missing requested pair, masked pixel, and archive error each have distinct states or reasons. HTTP 502 or 504 indicates a catalog error. HTTP 503 indicates a full queue and includes the coverage snapshot.

Repeating the same point and date request reuses the job if the candidate granule revisions match and the previous run did not encounter a retryable error. The chip cache keys include the collection, source identity and revision, exact point, frequency and window size. Repeated reads verify the cached file checksum. This interface currently serves complete small TIFF downloads. Production deployment would need a public gateway, persistent job and artifact storage, and a map tile service.

See [exploration lenses](EXPLORATION_LENSES.md) for the four lens IDs and the optional baseline cutoff. The [example requests](../contracts/examples/exploration-lens-requests.json) include agriculture, flood, wildfire, vegetation disturbance, track retry and a point with no coverage in the requested window. The [geographic holdout](EXPLORATION_HOLDOUT.md) documents the local transfer checks.

## Measured InSAR pair

Fetch the pair inventory with the point and time window, then submit those same fields and one returned pair `id` as `pair_id`. The inventory records the reference and secondary dates, pass/track/frame series, and elapsed days between acquisitions. It leaves pixel support unchecked until a quantitative read. Requests for a pair outside the point inventory return HTTP 404.

```json
{
  "longitude": 130.65,
  "latitude": 32.65,
  "start_date": "2026-07-21",
  "end_date": "2026-08-14",
  "pair_id": "NISAR_L2_PR_GUNW_026_024_A_018_028_4000_SH_20260721T202900_20260721T202934_20260814T202858_20260814T202933_P05023_N_F_J_001",
  "coherence_threshold": 0.35
}
```

The pair worker reads the native wrapped grid. It has a separate queue with room for two active jobs and reuses the downloaded chip when the phase display threshold changes. The result provides:

- `coherence.tif` with focused coherence in the range 0–1. It retains valid low-coherence values.
- `wrapped_phase_rad.tif` with native wrapped phase in radians where the complex measurement and coherence threshold permit it. Use a cyclic palette and nearest-neighbor sampling at display scale.
- Point values, per-layer valid pixel counts, native grid metadata and polarization. A pair can return usable coherence with `partial` status and no usable phase pixels.

The worker uses the product's wrapped phase as delivered. It applies zero additional phase corrections. LOS displacement remains null. The pair inventory and measurements answer separate coverage and observability questions.
