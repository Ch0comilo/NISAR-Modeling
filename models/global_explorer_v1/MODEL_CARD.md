# Model card: experimental masked temporal Transformer

Version global_explorer_v1 · 4 November 2026 · CPU-trained from scratch.

## Intended purpose

Learn a shared descriptive representation of measured NISAR scattering trajectories for exploration. No event classes, hazard probabilities, future forecasts, biomass/moisture inversions or validated deformation estimates are produced.

## Actual implementation and data

Two bidirectional Transformer encoder layers, width 48, four attention heads, 96-unit feed-forward blocks, dropout 0.1. **41,716 trainable parameters.** Inputs use named HH/HV/VV/VH slots, availability masks and actual elapsed-time Fourier features; hidden acquisitions are masked before feature embedding. This checkpoint is trained on **L-band HH/HV only**, and declines other band/polarization semantics. A unit test changes hidden measurements arbitrarily and confirms they cannot leak into model output.

Five training geographies: Forest River (USA), Deliblato (Serbia), Chenab (Pakistan), Niscemi (Italy), Okavango (Botswana). There are 1,536 sampled pixel trajectories per region; pixels are correlated, not 7,680 independent world examples. Spatial training uses the first 75% of columns; model selection uses a separated stripe beyond 80%. Ten epochs, AdamW, seed 47; checkpoint selection uses only training-region spatial validation.

Two whole geographic cases are held out: Amazon floodplain (Brazil) and Murray Basin (Australia). The latter has a longer acquisition count/time span than training examples, so it also tests a different temporal context. These are development evaluations on two regions, not a global benchmark or proof of universal transfer.

The self-supervised task withholds an internal measured acquisition, reconstructing it using earlier **and later** observed context. This is not forecasting. Full-input decoder responses and latent colors are not validation metrics.

## Held-out reconstruction results

| Held-out geography | Masked acquisition | Transformer RMSE (dB) | Visible temporal mean (dB) | Actual-time interpolation (dB) |
|---|---|---:|---:|---:|
| amazon | 2026-09-03 | 1.822 | 1.715 | 1.674 |
| murray | 2026-05-24 | 1.849 | 1.770 | 1.305 |

**The Transformer does not beat interpolation on either held-out region.** It also does not beat the visible temporal mean here. Keep the learned view experimental and preserve physical measurements/PCA as the primary interpretable baselines. Do not claim a DL performance advantage from attractive embeddings.

Each regional score uses 512 spatially correlated pixels and one masked acquisition. Effective independent geographic test size is two. Hyperparameters were not selected on these two scores. Preprocessing alignment fixes were made before the accepted run; the final raw cube hashes and actual projected grids are checked in the artifact audit.

## Representations and uncertainty

Per-date state vectors have 48 dimensions and use bidirectional context from the entire requested interval. The pooled state is their temporal mean. A single training-only PCA projection/scale is frozen across every location/date. Colors are arbitrary latent axes, not physical material classes. Three RGB axes compress information; raw vectors remain available.

Four Monte Carlo dropout passes yield a small-sample latent-sensitivity map. Four draws are too few for a reliable uncertainty calibration, and the output is not a posterior, event probability or validated novelty detector. Distance from standardized training embeddings is descriptive. Global accuracy on other climates, seasons, geometries, instrument modes, noise floors or SAR bands remains unmeasured.

## Source ideas and external models

This compact implementation borrows the general masked-reconstruction idea; it is not a reproduction of Ti-MAE/TimeMAE, CROMA or TerraMind and does not use their weights. [Ti-MAE paper](https://arxiv.org/abs/2301.08871), [TimeMAE paper](https://arxiv.org/abs/2303.00320).

[CROMA official code](https://github.com/antofuller/CROMA) exposes SAR representation learning, while [TerraMind's author model card](https://huggingface.co/ibm-esa-geospatial/TerraMind-1.0-base) supports Sentinel-1 modalities and missing modalities. Neither establishes native, verified NISAR HH/HV/phase performance for this worker. The exact named-channel input contract and CPU budget motivated the small model instead of silently treating NISAR channels as pretrained Sentinel-1 channels.

## Reproduction

Run `.venv/Scripts/python.exe scripts/train_global_example.py`. Current checkpoint, frozen projection files and `training_report.json` live together in this folder. Training and inference are local; no weights or user data were uploaded.
# Expanded frozen evaluation (v0.2)

The checkpoint is unchanged. A separate benchmark hides every internal acquisition in each held-out region, scoring the Transformer and both baselines on identical observed target support with valid temporal neighbors. Six Amazon dates give Transformer/mean/interpolation RMSE **1.600/1.178/1.211 dB**; 14 Murray dates give **3.637/3.007/2.254 dB**. Both baselines remain better on both regions. Pixels and overlapping temporal windows are correlated; the independent geographic case count remains two. No retraining or selection used these scores. See `docs/results/model_benchmark_all_dates.json` at repository root for all per-date results and the checkpoint hash.

