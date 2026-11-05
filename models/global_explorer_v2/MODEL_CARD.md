# Global Explorer v2: experimental observed-dynamics representation

**42,292 parameters · L-band HH/HV · 160 m linear-power aggregation · no forecasting**

The frozen checkpoint learns contextual descriptions of measured surface trajectories. It produces no event classes, physical displacement, hazard probability or causal explanation. A second geographic evaluation informed neither training nor checkpoint selection. Color quality is not an accuracy measure.

## Data and fitting

Nine training regions: Forest River, Deliblato, Chenab, Niscemi, Okavango, California, Mekong, Kafue and southern Iceland. There are **7,671 sampled, spatially correlated pixel sequences**, not that many independent world cases. California and Iceland extend the training record into January–October. All inputs use 160 m posting, with arithmetic linear-power aggregation before dB and strict block support. Thirteen total regions across six continents provide 128 accepted regional acquisition chips; one additional Pantanal chip had zero valid observations and is preserved in the exclusion/provenance record.

Spatial selection uses the first 75% of supported columns for training, a buffer, and columns beyond 85% for validation. This handles partially observed footprints: only 26.17% of the Mekong chip has complete temporal support, west of an unobserved query-center pixel. The exact split columns are recorded in `training_report.json`. Spatial dependence is not eliminated by this split. Both latent projections fit only the training side, without validation or geographic test pixels.

The earlier Amazon/Murray results informed this generation, so they are **legacy development cases**. **Hokkaido and Pantanal are the two new geographic tests**, excluded from fitting and selection. The Pantanal July 5 acquisition had zero supported pixels; it was excluded on input quality, without reading model scores, leaving eight real observed dates. Partial observations are retained. No date or measurement is invented to fill a gap.

## Architecture and objective

Two bidirectional Transformer layers, width 48, four attention heads, feed-forward width 96 and dropout 0.1. Inputs retain the four named polarization slots and missingness; this checkpoint only trained on measured HH/HV at L band. Its normalization uses each trajectory's **visible-only** channel mean and standard deviation (0.5 dB floor). An actual-time interpolation baseline and left/right observation gaps enter the representation. A zero-initialized residual head starts from interpolation, and its correction is bounded by half the visible channel standard deviation. Relative-duration Fourier features replace dependence on an absolute time origin.

Training masks internal observations within actual contiguous subrecords of length 4–12. Sixteen epochs, seed 109; epoch **15** is selected using the case-balanced training-region spatial validation score, **1.012 dB**, compared with initial interpolation at **1.074 dB** on those stripes. The model is not selected using geographic test scores. Test and inference code checks hidden-value leakage, time-origin/level shift invariance, missing channels, zero-residual interpolation and checkpoint reloadability.

Instance normalization is motivated by [RevIN (author implementation, ICLR 2022)](https://github.com/ts-kim/RevIN). This is an original masked-reconstruction model, not copied code, transferred weights or a reproduction of the paper's forecasting system.

## Frozen reconstruction results

Every internal acquisition is hidden in turn. All five methods use identical complete-trajectory sample support and actual acquisition times on the same 160 m cubes; at most 512 correlated pixels are sampled per case. The version comparison changes both data and architecture, so it is **not an architecture ablation**.

| Region and role | Masked dates | V1 | V2 | Interpolation | Visible mean | Matérn GP |
|---|---:|---:|---:|---:|---:|---:|
| Amazon, legacy development | 6 | 1.156 | 0.454 | 0.456 | 0.489 | 0.482 |
| Murray, legacy development | 14 | 3.594 | 2.065 | 2.171 | 2.947 | 2.320 |
| Hokkaido, new geographic test | 8 | 0.785 | 0.661 | 0.652 | 0.701 | 0.638 |
| Pantanal, new geographic test | 6 | 0.588 | 0.561 | 0.560 | 0.468 | 0.480 |

Values are reconstruction **RMSE in dB**, not forecast errors. V2 improves on v1 in all four cases. It remains slightly worse than interpolation on both new tests; the mean or GP can also be better. The original v1 benchmark used different spatial posting/support and is not directly comparable with this table. Only **two new geographic cases** exist; pixels and overlapping acquisition windows are correlated. Complete-support selection may bias the benchmark toward easier observations. There is no claim of globally validated accuracy or physically interpretable latent axes.

The GP is a separate empirical-Bayes Matérn-3/2 conditional reconstruction fitted to **visible data only** for each masked acquisition. Its nominal 95% Gaussian observation intervals cover **93.21%** of Hokkaido and **89.39%** of Pantanal scored targets. Hyperparameter and estimated-mean uncertainty are omitted; these intervals are uncalibrated. This observation-distribution check does **not** validate the different GP latent-state bands emitted by ordinary analysis jobs. All 68 GP fits returned optimizer success; that does not establish model adequacy.

`evaluation_freeze.json` records the checkpoint, recipe and prediction-source hashes before new geographic scores are opened. `evaluation_report.json` contains all per-date scores, support counts and GP fit diagnostics. No further training or test-driven tuning was performed after this evaluation.

## Dynamic views and uncertainty

Raw contextual states include time-encoding responses. The additional **constant-trajectory control contrast** subtracts the same frozen model's response to each channel held at its observed mean, with identical dates and masks. A constant-input test confirms that this removes pure calendar-induced state evolution. `dynamic_projection.npz` is fitted on training-side per-date contrasts only; RGB is frozen across dates and locations.

This contrast is not causal attribution: nonlinear interactions, context and missingness can still matter. `dynamic_context_change_per_day` is an RMS latent-coordinate difference divided by elapsed days, not a physical speed or event score. Raw and controlled states remain separately available. Four-draw MC-dropout output describes latent sensitivity; it is not calibrated sensor uncertainty or an event probability.

The checkpoint declines other band/polarization semantics, incompatible posting, or oversized learned-state jobs. Native quantitative inputs must be aggregated explicitly; the worker does not silently interpolate an unsupported grid. Predictions are restricted to the reconstruction experiment; normal analysis provides measured-date representations, with no future acquisition output.
