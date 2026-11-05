# Flood evidence in new regions

This retrospective evaluation asks whether a frozen surface-change diagnostic separates newly observed open water from two controls, stable nonwater and existing open water. The event reports connect a radar change to actual acquisition dates, independent evidence and gaps in coverage. The same report structure can be reused with independent vegetation disturbance, mapped slope failures or earthquake context. The measurements and evidence gates for those phenomena differ.

## What changed in Senegal and India

**Matam** had 1,469 strict new-open-water reference cells and 12,609 stable-nonwater cells. The recent baseline flagged 262 (17.8%) and 135 (1.1%), respectively. The stricter dual-darkening rule flagged 120 (8.2%) and 62 (0.5%). Across 1,040 pairs matched on pre-target HH and HV, new-water cells changed by an average **1.31 dB less in HH** than the controls. Resampling spatial blocks put the mean contrast between −1.85 and −0.63 dB. Optical observations were September 21 and October 15, 2026. The target radar observation was October 14. [Senegal flood activation](https://disasterscharter.org/activations/flood-in-senegal-activation-1053-)

**Ndioum** had 49 new-water and 11,473 stable-nonwater cells. The departure flagged 10 (20.4%) and 247 (2.2%), respectively. Dual darkening flagged 3 (6.1%) and 25 (0.2%). The matched mean HH contrast was −1.22 dB, with a spatial resampling interval of −2.41 to +0.74 dB. All 49 new-water cells found baseline matches. Optical observations were September 17 and October 14, 2026. The target radar observation was October 14. [Senegal flood activation](https://disasterscharter.org/activations/flood-in-senegal-activation-1053-)

Both sites belong to the same flood region and had zero cells in the strict stable-open-water group. The comparison at these sites therefore speaks to optical new water versus stable nonwater. The signed dB layers and curves show radar behavior in cells missed by either flag. They also show why a strong-looking local signature can coexist with weak agreement over the larger extent.

Of the ten first-cohort sites, five lacked compatible radar bracketing. Luang Namtha and Betravati fell below the joint optical coverage gate, with five and zero usable overlapping cells, respectively. Podor had 9,657 jointly usable cells but zero strict new-water transitions at the selected snapshots. Those three cases retain descriptive sheets and a null benchmark comparison. [UNOSAT product catalog](https://unosat.org/products/?page=0), [Copernicus Rasuwa flood activation](https://mapping.emergency.copernicus.eu/news/flood-in-nepal-emsr927/)

**Barh** qualified in the separately frozen six-site follow-up cohort. The new-water group had median target changes of **−5.67 dB HH and −2.97 dB HV**. Stable nonwater changed by +0.72 and +0.25 dB. Existing open water also darkened by −2.34 and −1.79 dB. The recent baseline flagged **2,363/2,702 new-water cells (87.5%)**, **75/1,226 stable-nonwater cells (6.1%)** and **165/506 stable-open-water cells (32.6%)**. Dual darkening flagged 2,468, 1 and 232 cells in those groups, respectively. The matched HH contrast was −8.39 dB (conditional interval −9.10 to −7.58 dB), for the 171 of 2,702 new-water cells that found baseline matches. [Bihar flood records](https://fmiscwrdbihar.gov.in/fmis/inundation26.aspx)

Barh's paired optical observations were July 26 and September 13, 2026. The radar target on September 12 followed the final baseline acquisition by **48 days**. Only 5,431 of 16,384 radar-supported cells had jointly usable optical coverage. Changes can accumulate over that observation gap, and the matched subset covers only 6.3% of new-water cells. The other five follow-up sites failed the joint optical coverage gate. No selected series in either cohort had three compatible prior-season acquisitions.

The 16 sites produced **three admitted comparisons across two regions**, **11 measured evidence sheets** and 55 verified map, array and plot artifacts. The Matam, Ndioum and Barh results use the same frozen rules. Their difference shows why signed dB, optical coverage, and stable-water comparisons belong alongside the flag. These cases cover observable open-water transitions at selected dates. This sampling leaves performance in obscured, vegetated or built-up areas unresolved. Admitted granules report that thermal-noise correction was off.

## Run the evaluation

```powershell
# Public metadata screen
.venv/Scripts/python.exe scripts/prepare_flood_evidence.py --metadata-only
# Supply EARTHDATA_TOKEN locally for the bounded archive reads
.venv/Scripts/python.exe scripts/prepare_flood_evidence.py
.venv/Scripts/python.exe scripts/report_flood_evidence.py
.venv/Scripts/python.exe scripts/verify_flood_evidence.py
```

The default output is `products/flood_evidence_a`. Each script accepts `--recipe` and `--output`. For the follow-up cohort, pass `--recipe configs/flood_evidence_followup.json --output products/flood_evidence_b` to each command. Use a new directory for a changed scientific protocol. The ZIP linked from the repository release includes measured cubes and aligned optical inputs for offline report generation and verification. [Release assets](https://github.com/Ch0comilo/NISAR-Modeling/releases/tag/v0.5.0)

### Input recipe

The cohort and analysis settings live in [flood_evidence.json](../configs/flood_evidence.json). Each case declares:

| Field | Meaning |
|---|---|
| `id`, `region`, `place` | Stable case ID, event/geography grouping, display name. Multiple sites on the Senegal River share one region. |
| `point` | WGS84 longitude and latitude, in that order. The analysis chip is 10.24 km across at 80 m posting. |
| `onset` | UTC date boundary from public event documentation. All recent baseline and pre optical acquisitions must precede this date. |
| `target_start`, `target_end` | Inclusive assessment window in UTC. The window can start after onset to reflect documented local observation timing. |
| `event_source` | Public event context, independent of radar response. |
| `collection` | Optional explicit GCOV collection. The default is PROVISIONAL. BETA cases are screened separately. |

The public catalog screen uses one compatible L-band dual-pol series per case. It requires at least three distinct acquisition dates within 120 days before onset. It selects the earliest target with same-side optical catalog candidates within four days of the final baseline and target radar dates. Date ties favor a larger count of baseline acquisitions, up to six, then the series key. Optical assets are selected by date and valid spatial coverage. Case windows, source code and thresholds were hashed before measurement scoring. Nearby White River sites belong to the same broad Indiana geography as an earlier development case and were included as screening candidates. Their catalog screen failed. The admitted cases are in fresh study areas away from the earlier scenes. [NISAR GCOV product guide](https://nisar-docs.asf.alaska.edu/gcov/), [NASA OPERA DSWx-HLS product](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-HLS_V1)

The pipeline averages linear gamma-zero on complete native blocks before conversion to dB. It requires at least half the analysis chip to have measured support across every selected radar acquisition. Within that population, joint usable optical coverage must reach 25%. A strict new-water group requires at least 20 cells across at least four nonoverlapping 640 m blocks, with at least 40 stable-nonwater comparison cells. Every screened candidate gets a status. A failed screen or unresolved optical comparison remains visible in the cohort.

### Comparison

- **Recent baseline departure** uses the existing local median and scaled MAD for each polarization and cell. A flag means at least one absolute change strictly exceeds its diagnostic band. The band half-width is `max(3 dB, 3 × max(1.4826 × MAD, 1 dB))`.
- **Dual darkening** uses the existing flood pilot thresholds against the same recent median and common population. Co-pol must decrease by at least 3 dB and cross-pol by at least 1.5 dB.
- **Prior-season comparison** requires at least three dates from the same collection and compatible series within 30 days of the target's previous-year calendar anniversary. Both baselines use the identical target and intersected radar support. The seasonal result is null when the archive or shared support is insufficient.
- **Independent optical groups** require at least 80% usable optical coverage within each analysis cell at both dates. Open water requires at least 80% of valid optical class cells marked open water. The nonwater endpoint requires at most 20% open and at most 20% partial water. Mixed, partial and cloud-obscured cells are excluded from the flag fractions.
- **Matched contrasts** compare new-water cells with unique stable-nonwater controls matched using pre-target median radar levels within 2 dB in both channels. Spatial block resampling intervals describe sensitivity to spatial composition when enough blocks contribute. The intervals do not account for sensor or reference errors.

## Teammate output contract

Start with `index.json`. It lists every case, its `region`, status, relative result URL and SHA-256. Resolve each entry's `href` from the index directory. Resolve artifact paths in a result relative to that result's directory. All raster outputs are on the same projected pixel grid. Timeline rows use UTC acquisition dates and named polarizations.

| Result field | How to use it |
|---|---|
| `admission_status`, `admission` | Show the screen outcome and the reason a comparison is unavailable. The nested fields record selected source granules, date support, coverage, reference class counts and input checksums when available. |
| `observations` | Map grid, baseline dates, single target date and bounding radar observations. The interval does not resolve exact onset, continuous duration or recovery. |
| `optical_dates` | Actual optical observation dates for the pre and post reference. Compare them with radar dates before interpreting agreement. |
| `observed_groups` | Named groups, cell counts, per-acquisition median and interquartile range in dB, signed target change, change in polarization balance and local co-pol contrast. |
| `comparison.recent` | Separate per-rule and per-optical-group flag numerators and eligible denominators. Use the reported fractions only for admitted cases. |
| `comparison.seasonal` | Identically supported recent versus prior-season rows when available. Read `admission.seasonal.status` if null. |
| `matched_contrasts` | Counts, baseline matching differences, signed dB contrasts and conditional spatial resampling intervals. |
| `event_attribution` | Null. `event_source` supplies documented context for the region. |
| `artifacts` | Checksummed PNG, full map NPZ and COGs. Reports lacking compatible measurements or a paired optical input may have an empty artifact list. |

`target_change_db.tif` has one band per named polarization. `reference_groups.tif` uses 0 stable nonwater, 1 new open water, 2 stable open water, 3 open-water loss, 4 mixed or partial and 255 unavailable. `departure_reference_agreement.tif` uses 0 new water with a flag, 1 new water without a flag, 2 flag in another resolved group, 3 other resolved group without a flag and 4 unscored. Render categorical layers with nearest-neighbor resampling. `maps.npz` adds both rule masks, common radar support, matched populations, coordinate vectors and EPSG. A report that misses the admission threshold may still have descriptive plots and layers. The numeric benchmark comparison is null for those cases.

The two optical snapshots describe a change in a mapped water class. Stable nonwater means the optical classification stayed in that group across two dates. Within-group radar departures can come from real surface dynamics as well as geometry and observation differences. There is no verified all-purpose normal label or calibrated flood probability. Cell counts within a site are spatially dependent, and nearby sites along the same river cannot be counted as independent geographic tests.
