# Version 0.5 validation report

**Assessment: Share with noted caveats; do not promote a phenomenon classifier.** The standardized report is suitable for exploration of measured surface behavior. The new experiments identify important evidence and generalization limits rather than validating global event classification or corrected displacement.

## Protocol and provenance

Five a priori scene candidates add 42 quantitative GCOV acquisitions. Four scenes are fresh fixed-policy flood/control comparisons; Oregon is a separate wildfire-context development case. Coordinates/windows and the original flood thresholds/weights were hashed before new agreement was inspected. The representation comparison separately hashes its source and exact inputs before opening fresh labels/scores. No case was relocated or replaced based on a favorable response. Initial transient archive HTTP 502 errors were retried with bounded backoff; byte-range and transfer-budget checks remain mandatory, with no full-file fallback.

The existing neural weights remain unchanged. A fixed five-neighbor classifier probes physical versus learned features, using development-only scaling and matched support; it is not a new global trained phenomenon model. Test units are geographic scenes, not individual correlated cells. The new test scenes are consumed after this inspection and require fresh replacements for subsequent independent evaluation.

## Checks completed

All **70 offline tests pass**. Synthetic checks cover pattern definitions, sparse records and large gaps, missing fixed populations, polarization-ratio signs, projected selection, weighted spatial resampling, disturbance class/date freshness, explicit coarsening, development-only feature scaling, null denominators and strict HTTPS/bounded responses.

A [live orchestration check](results/characterization_v05_live_check.json) performs fresh catalog and optical discovery for Morocco while reusing individually checksummed measurement caches. Its measured-change summaries, timing, patterns, quality and independent-water comparison exactly reproduce the offline report. This is an orchestration check, not an additional independent validation scene.

The [machine verification](results/characterization_v05_verification.json) independently recalculates measured dB changes, full-series common masks, summary statistics, observation-frequency denominators, categorical support counts and native-grid/COG centers. It validates all component schemas, source/artifact SHA-256 fingerprints, unique artifact IDs and explanation references. It rechecks all fresh flood confusion counts and stable-nonwater denominators, all probe confusion/support matrices, and the new InSAR filled/finite/positive-uncertainty intersection. **291 artifacts across eight shared reports, four fresh flood-policy jobs and two new InSAR jobs are checked.** Scientific previews were inspected for readable axes, legends, units and interpretation limits.

Regional bootstrap means now use retained cell counts to weight unequal-population blocks and explicitly document their accepted complete-support population. This removes an average-of-block-means ambiguity. Spatial intervals still depend on unverified block independence and omit sensor, reference and model errors. These intervals do not calibrate the GP bands or any phenomenon probability.

## Findings that constrain delivery

- Rajshahi has partial focused full-series support and cloud-limited optical comparison. Gimcheon has one pre-event radar acquisition and only 1.59% joint optical support under the original flood policy. No strict new-water positives survive at either flood-context site, so new-water recall is unavailable. This is not evidence that flooding never occurred.
- Candidate control scenes are not declared universally event-free. The Australian scene contains a small new-water optical group. The frozen radar rule's stable-nonwater flag rates are reported with their own denominators and cannot be presented as global specificity.
- The 160 m representation probe has only one new-water proxy cell across four tests. Its learned-feature kNN labels 90.5% of reference-stable nonwater as new water in the Australian control. Neither positive-class performance nor a globally better model is demonstrated; no event-label endpoint is added.
- The Oregon optical adapter identifies nine strict confirmed in-window vegetation-loss cells. The matched contrast interval spans zero. No independently validated fire perimeter, burn extent or fire cause is supplied. Product-confirmed vegetation loss remains different from confirmed wildfire attribution.
- The Venezuela stack has 17,311 common coherent cells but only two common eligible correction-input cells. There is no independently stable reference, geodetic validation or redundant closure cycle. Corrected LOS displacement stays null.
- Processing completeness, observed-pattern categories, independent-product agreement and validated physical cause have separate fields and meanings. No global accuracy, hazard probability, continuous event duration, crop-health inversion or forecasting is supported.

The [scientific report](CHARACTERIZATION_V05.md) links primary event/product documentation and full results. The [integration contract](CHARACTERIZATION_V05_CONTRACTS.md) explains inputs, array units, evidence states and reproduction commands for teammates.
