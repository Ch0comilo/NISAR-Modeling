# Computed agriculture lens

`POST /v2/jobs` with `"operation": "explore"` and `"lens": "agriculture"` returns `result.science.lens.evaluation` under the [agriculture v1 schema](../contracts/agriculture-lens-evaluation.schema.json). The [measured Sköllersta example](../contracts/examples/skollersta-agriculture-lens-evaluation.json) contains a complete evaluation. The existing `lens.agriculture` median focal-versus-annulus history is also available.

```json
{
  "operation": "explore",
  "longitude": 15.39726192,
  "latitude": 59.13216973,
  "start_date": "2026-07-05",
  "end_date": "2026-09-03",
  "lens": "agriculture",
  "baseline_end_date": "2026-08-17"
}
```

The calculation reuses the selected radar chips and existing observation queue. It accepts measured HH/HV or VV/VH, calculates the paired cross/co ratio, and uses the same cells across every baseline and assessment date. The temporal diagnostics report those same dates and common cell count. The descriptor works on an arbitrary supported point. Parcel and crop records provide separate evidence.

## What is computed

The focal region is the 60 m circle around the click. Controls are selected from the 120–300 m annulus. Both neighborhoods must fit within the native chip, retain five common cells, and retain half their possible grid cells. Unique controls match the focal cells using only the baseline co/cross median levels within 2 dB per channel, examining up to 128 candidates per focal cell. At least five matched focal cells and half the focal population are required.

These are geometric neighborhoods and baseline-level matches. The calculation has no independent parcel, crop, soil, irrigation, rain or stable-control evidence. Match coverage and baseline imbalance accompany every comparison.

| Output | Meaning |
|---|---|
| `series` | Matched focal and control means in dB at actual acquisitions, their difference, and that difference relative to its observed baseline median. |
| `target_contrasts` | Mean cellwise target-minus-baseline change in matched focal cells minus the corresponding control change, with conditional spatial intervals. |
| `steps.*.intervals` | Signed acquisition-to-acquisition change in the matched focal-minus-control mean contrast, with actual dates, elapsed days and spatial intervals. |
| `largest_observed_interval_code` | The largest absolute measured contrast step anywhere in the selected history, if it reaches 1 dB. |
| `largest_assessment_interval_code` | The largest absolute contrast step whose later acquisition is in the assessment period, if it reaches 1 dB. The last-baseline-to-first-assessment bracket is eligible. |
| `bootstrap_largest_assessment_fraction` | How often that interval was the largest assessment contrast step in resampled histories that contained a step of at least 1 dB. The count of qualifying draws is separate. |
| `synchrony_interval_code` | The largest absolute focal-mean assessment step, if it reaches 1 dB. This answers the within-patch question separately from the focal-minus-control contrast. |
| `synchrony` | Fraction of matched focal cells whose largest assessment step occurs in that focal-mean interval, reaches 1 dB and agrees in sign with the focal mean. Inactive and differently timed cells remain in the denominator. |

The target-change statistic uses each cell's median baseline. The trajectory's contrast shift uses the median of the baseline population contrasts. Their order of aggregation differs. The ratio is calculated per cell and acquisition before calculating its own baseline. It adds no independent polarization observation.

## Evidence states and uncertainty

The material-contrast threshold is an exploratory **1 dB** policy. A target or interval contrast `supports` the named material-difference signature when its entire conditional 95% spatial interval is ≥ +1 dB or ≤ −1 dB. It is `absent_on_sampled_support` when the entire interval lies strictly inside −1 to +1 dB. Overlapping bounds, unavailable intervals and missing support yield `insufficient_data`.

The synchrony signature uses a separate **60%** policy and requires at least two assessment intervals, including the baseline-to-assessment bridge. With only one interval, the co-change fraction remains descriptive and the synchrony state is insufficient. Its conditional spatial interval must lie entirely at or above 60% to support the signature, or entirely below it to report absence. Overlapping bounds leave it unresolved. The interval is conditional on the chosen focal-mean bracket. It omits the uncertainty of choosing that bracket. Contrast-step selection sensitivity is reported separately. Equal largest steps use the earliest observed interval, as recorded in the policy.

These thresholds and states describe radar measurements under a declared policy. They have no validated crop-management accuracy. A shared regional pulse can support synchrony while the focal contrast remains absent. A changing control population can create a focal-minus-control contrast while focal cells themselves remain stable. The separate trajectories expose both cases.

Spatial uncertainty resamples complete 8 × 8-cell block histories 400 times, weighting group means by retained cell counts. It requires four contributing blocks in each comparison population. This preserves the dates and channel coupling while reselecting the largest contrast interval in each draw. The response reports block dimensions in metres, group block counts and accepted draws. Synchrony fractions have their own focal-block resampling interval.

A record with no material steps has null largest-step codes and zero qualifying timing draws. It receives no invented date. All timing outputs are observed acquisition brackets. Sensor error, reference error, model-family error, spatial block independence and intervals adjusted for selecting a favorable bracket remain unestimated.

`independent_evidence` stays insufficient in this implementation. Dated parcel/management records and independent optical observations would help confirm or refute harvest, tillage, irrigation or vegetation explanations. Phenomenon class, probability, cause and displacement stay null.

## Layers and integration

The allowlisted assets in `lens.radar.layers` include SHA-256, band labels and units on the native radar grid.

| Role | Display and interpretation |
|---|---|
| `agriculture_populations` | Three bands identify supported focal cells, matched focal cells and matched controls. Values are 0/1, with NaN outside common support. |
| `agriculture_step_interval` | Per-feature code for each cell's largest absolute assessment step of at least 1 dB. Code zero denotes no qualifying cell step. Other codes map to the actual date brackets in `steps.*.intervals`. |
| `agriculture_step_magnitude_db` | The signed magnitude of that cell's largest assessment step. Read with its interval code and support mask. |

Use nearest-neighbor sampling for populations and interval codes. A map can show which cells selected a bracket while the linked plot shows the focal and control trajectories. Show interval length and contributing cell/block counts when selecting a step or synchrony result. Separate the full-history and assessment-period largest steps, as either can occur away from an independent event date.

## Measured regression replays

The retained Swedish cases each supply three baseline and two assessment acquisitions. The point and date records supply external trial context, while full harvested parcel footprints remain unresolved. They were previously inspected and are used here as regression cases.

| Case | Matched focal/control cells | Measured result |
|---|---:|---|
| Sköllersta rye | 29 / 29 | The largest HH assessment contrast step is +1.84 dB across August 10–22. Its material-difference state is unresolved by the spatial interval. HH synchrony is 75.9%, with conditional interval 66.7–91.7%, supporting the narrower measured synchrony signature. |
| Råbelöv wheat | 29 / 29 | Target contrasts remain unresolved under the stated material threshold and intervals. The largest material assessment ratio step is also unresolved. HV synchrony is absent under the tested rule. |
| Gotland wheat | 28 / 28 | The target HV contrast is −0.14 dB, with conditional interval [−0.71, +0.53] dB, supporting absence of a material target contrast under the 1 dB rule. Its assessment synchrony signatures are absent under the tested rule. |

The existing median focal-versus-annulus histories reproduced exactly. The new matched means answer a different comparison. Sköllersta's earlier +1.78 dB HH harvest-bracket contrast belongs to that existing median/annulus statistic.

The earlier cached fixture runs took roughly 0.5–0.6 seconds on the local single-threaded numeric setup. Those timings exclude cold archive reads, interpreter imports, catalog discovery and deployment concurrency.
