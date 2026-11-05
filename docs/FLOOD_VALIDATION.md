# Flood pilot validation report

**Assessment: Share with noted caveats.** The observed-signal processing and explicit evidence contracts are suitable for an educational backend. These results do not validate a worldwide flood classifier, flood extent or geodetic displacement.

## Methodology and reproducibility

Three declared development scenes contribute 21 quantitative GCOV chips and three independently sourced optical-reference pairs. Geography, windows and fixed diagnostic thresholds are recorded in `configs/flood_pilot.json` and `contracts/flood-policy.json`. Source IDs, original URLs, chip hashes, product flags, sensing timestamps and optical license metadata travel with the fixtures. Caches are not substituted with browse images. Beta/provisional calibration context remains explicit.

The candidate cap and radar-series timing policy were refined for coverage before any radar/reference agreement was computed. No agreement-based threshold selection or Transformer retraining occurred. The last-baseline sensitivity diagnostic was added after inspection to expose sensitivity; it is not a held-out experiment or a replacement optimized rule. All three scenes are development cases, not independent test geographies for a trained flood model.

All **59 offline tests pass**. A [live worker check](results/flood_live_worker_check.json) performs fresh NISAR catalog and optical discovery for the Pakistan request, reuses individually checksummed measurement caches, and reproduces the offline scientific summary and proxy comparisons exactly. This checks the live orchestration path without presenting reused data as an independent validation scene.

Run `python -m unittest discover -s tests -v`, then `scripts/run_flood_pilot.py`, `scripts/run_insar_readiness.py` and `scripts/verify_flood_pilot.py`. [The machine report](results/flood_pilot_verification.json) records five new real-data jobs and **33 verified artifacts**. The verification independently recomputes dB differences, proxy counts, matched contrasts and input-support masks from the retained measurements.

## Checks and issues resolved

- Measured linear powers, dates, named channels, native coordinates, COG centers and hashes agree with source cubes. No power is inferred from colors or resampled into invented detail.
- Cloud/fill contributions remain in the coverage denominator. Mixed/partial optical classes remain unknown for strict binary scoring. Stable open water and water-loss transitions are scored as non-new-water groups, rather than being silently excluded from precision.
- Only baseline information selects controls. Unmatched positive cells are excluded from matched contrasts; retained fractions and baseline balance are exported. Spatial bootstrap draws weight sums/counts rather than equally weighting unequal-population block means.
- Both reference epochs pass the temporal boundary and seven-day offset policy. Full sensing timestamps remain available. Radar baselines and optical snapshots have different temporal support; the report explains this and publishes comparator sensitivity.
- Zero denominators produce null where the quantity is undefined. Richmond's absent new-water group does not create a recall score or a finding that no flood occurred.
- Object areas are nominal areas of qualifying analysis cells. First-change brackets and signal-return confirmation use observed dates. Gaps do not become continuous durations.
- InSAR eligibility independently intersects coherent selected components, fill flags, finite screens and positive finite uncertainty. The 15 km window's 1,614 common phase cells reduce to three common eligible correction-input cells; no independent stable datum or displacement estimate is claimed.
- All new output manifests validate against packaged schemas. The existing v2 Transformer checkpoint remains byte-identical to its frozen evaluation record.

## Required interpretation caveats

Chokwe has only 22.80% usable joint reference support, beta calibration and a single assessment. Richmond has 13.23% joint reference support and radar acquisition after the reported flash-flood interval. Qadirabad has complete coverage but substantial mixed/partial reference cells, seven-day post-reference offset and strong baseline sensitivity. None of the references is a surveyed ground-truth flood polygon. Optical products cannot validate under-canopy water, and scene context does not provide pixel-level urban/vegetation strata.

Spatial resampling intervals do not include sensor error, reference misclassification, temporal mismatch, uncertain spatial dependence or unmeasured confounders. Multiple pixels/objects from a scene are not independent geographic test cases. No global accuracy, hazard probability, flood depth, continuous flood duration, causal attribution or corrected LOS/vertical displacement is supported.

Scientific previews were visually inspected for readable axes, legends and units. The [main report](FLOOD_CHARACTERIZATION.md) links the primary event and product documentation. New event adapters or stronger global claims require independently labeled, geographically/seasonally separated evaluation and suitable measurement-quality inputs.
