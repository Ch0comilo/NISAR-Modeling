# NISAR Surface Dynamics

Explore how Earth's surfaces change through measured radar time series and interferometry. This is the **science and data-processing backend** for an educational exploration project: agriculture, wildfires, floods, landslides, wetlands, earthquakes and more.

**Observed dynamics · worldwide point queries · explicit quality flags · no forecasting**

![Controlled dynamics across thirteen real NISAR regions](docs/images/global_dynamic_atlas_v2.png)

[Team integration guide](TEAM_HANDOFF.md) · [V2 modeling and InSAR results](docs/MODELING_V2.md) · [V2 model card](models/global_explorer_v2/MODEL_CARD.md) · [Download measured examples](https://github.com/Ch0comilo/NISAR-Modeling/releases/tag/v0.3.0)

## Current experiment: v0.3.0

The new candidate uses **visible-only normalization and an interpolation-residual Transformer**, trained on nine regions with broader seasonal support. Thirteen regions across six continents provide **128 accepted regional acquisition chips** at a common 160 m posting; Hokkaido and Pantanal are two newly reserved geographic tests. Raw contextual states and a **constant-trajectory control contrast** are separate outputs, so calendar-induced latent evolution is distinguishable from trajectory-related representation changes.

On the new geographic tests, V2 reconstruction RMSE is **0.661 dB in Hokkaido** and **0.561 dB in Pantanal**, improving over V1's 0.785 and 0.588 dB on the same data/support. Interpolation remains slightly better at 0.652 and 0.560 dB; the temporal mean or Matérn GP can also win. The learned views remain experimental. The [full protocol and results](docs/MODELING_V2.md) distinguish these new tests from the older Amazon/Murray development cases.

New **multi-pair InSAR** processing compares three actual Kumamoto intervals, exports coherence contrasts and gates phase comparison on a shared reference. The 5 km window lacks common phase support; a separate 15 km window has 4.57%. Neither establishes stable ground or validated displacement. The release adds ten request/result schemas, point-query quality/probe outcomes, **43 offline tests**, and independent verification of **298 artifacts across 15 new jobs**.

## What you can explore

| Module | Implemented methods | Results for an exploration app |
|---|---|---|
| Measured surface change | Gamma0 power, robust dispersion, actual-date slopes and steps, spatial texture, POD | Georeferenced layers, trajectories, transition rates, modes and measured-date animations |
| Spectral structure | Actual-time generalized Lomb–Scargle, sampling-window diagnostics, permutation tests, local sinusoidal fits when supported | Candidate-period maps, spectra and time-frequency arrays |
| Stochastic states | Empirical-Bayes Matérn-3/2 Gaussian processes, Monte Carlo draws, spatial block bootstrap | Historical latent-state distributions and separate spatial-heterogeneity intervals |
| Shared learned representation | Masked temporal Transformers; 42,292-parameter v2 candidate; training-only projections | Per-date 48-dimensional states, constant-control contrasts, shared colors and dropout sensitivity |
| InSAR pair | Native wrapped/unwrapped grids, coherence, connected components and ionosphere flags | Phase/coherence maps, threshold sensitivity, supported-component statistics |
| InSAR intervals | Compatible measured pair stacks, common native pixels, coherent-component/reference gates | Interval coherence contrasts, phase support, shared-pixel radians when available and explicit partial outcomes |
| InSAR network **new** | Compatible acquisition graphs, connected components, minimum temporal-baseline forest, independent cycle paths | Available pair intervals, event bracketing and catalog-level time-series readiness |
| Map probe | Pixel or metric-radius extraction from an existing NetCDF | Measured histories, descriptors and contextual learned states for a map selection |

Themes supply questions and candidate physical explanations. They do not turn a radar change into a confirmed fire, flood or landslide label. Missing data and unsupported model domains have explicit results.

## Quick start

Python 3.11+; the CPU reference environment uses Python 3.13.5. From a fresh clone on Windows:

```powershell
git clone https://github.com/Ch0comilo/NISAR-Modeling.git
cd NISAR-Modeling
python -m venv .venv
.venv\Scripts\python.exe -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux, use `.venv/bin/python` in place of `.venv\Scripts\python.exe`. `requirements-lock.txt` records the exact reference CPU environment; install it after the CPU Torch step to reproduce those versions.

Restore the regional cubes and InSAR pairs from the checksummed release asset, then run an offline job:

```powershell
.venv\Scripts\python.exe scripts\download_examples.py
.venv\Scripts\python.exe -m surface_dynamics analyze contracts\examples\hokkaido-v2.json --output products\global_v2\runs\hokkaido
.venv\Scripts\python.exe -m surface_dynamics insar-stack contracts\examples\kumamoto-stack-offline-wide.json --output products\example_insar_stack
.venv\Scripts\python.exe -m surface_dynamics probe contracts\examples\hokkaido-v2-probe.json --output products\probes\hokkaido-v2.json
```

No Earthdata credentials are needed for these downloaded fixtures. The release ZIP also contains the source, wheel, model weights, provenance and offline examples. The helper verifies its checksum and restores only measured fixtures; it refuses to overwrite different existing measurements.

For a live worldwide point/date request:

```powershell
# Supply EARTHDATA_TOKEN through your local environment or secret manager.
.venv\Scripts\python.exe -m surface_dynamics analyze contracts\examples\global-v2.json --output products\runs\world-v2
.venv\Scripts\python.exe -m surface_dynamics insar-network contracts\examples\kumamoto-network.json --output products\networks\kumamoto
```

Public catalog and InSAR-network discovery need no login. Quantitative archive reads require Earthdata authentication. Never put credentials in JSON requests, source files or commits.

## Backend contracts

Use the versioned [JSON Schemas](contracts/) and [request/response examples](contracts/examples/). The [handoff](TEAM_HANDOFF.md) explains shapes, units, coordinate conventions, artifact roles and interpretation limits.

- **Inputs:** WGS84 longitude/latitude with a UTC date window, or measured linear-power cubes with named polarizations, masks and a projected metric grid. InSAR accepts a measured GUNW pair or a point/event-date query.
- **Outputs:** validated JSON manifests; COG maps; NetCDF scientific arrays; CSV trajectories; NPZ draws/latent states; scientific PNG/GIF previews; network topology and observability diagnostics.
- **Python entrypoints:** `engine.analyze`, `engine.analyze_insar`, `network.plan_network`, `probe.probe`.

This repository provides synchronous workers and a CLI. Your backend can wrap them in its job system and serve manifest artifacts to the existing frontend. No frontend code is included.

## V1 reference evidence and continuing limits

The v0.2 reference run used **66 measured GCOV chips from seven regions across six continents**, plus a live global-point job and a real Kumamoto earthquake GUNW pair. Its nine original jobs have 152 checked artifacts. The current offline suite has **43 tests**; CI runs without downloading satellite data. The table below is the earlier V1 experiment on its original posting/support; use the [V2 report](docs/MODELING_V2.md) for the new matched-grid comparison.

Worldwide querying and processing are implemented within chip, resource and measurement limits. Coverage is uneven, and these regional examples do **not** establish globally validated model accuracy.

The expanded frozen-checkpoint benchmark hides **every internal acquisition** in the two held-out regional cubes. All methods use the same scored pixel/channel support and actual observation times:

| Geographic holdout | Acquisitions hidden one at a time | Transformer RMSE | Visible temporal mean | Actual-time interpolation |
|---|---:|---:|---:|---:|
| Amazon, Brazil | 6 | 1.600 dB | 1.178 dB | 1.211 dB |
| Murray Basin, Australia | 14 | 3.637 dB | 3.007 dB | 2.254 dB |

The Transformer currently loses to both simple baselines on both regions. Keep its representations experimental. Temporal windows and sampled pixels are correlated; these are **two independent geographic cases**, not thousands. No tuning or retraining used this expanded holdout result. See the [full benchmark](docs/results/model_benchmark_all_dates.json).

InSAR reports **phase-equivalent centimetres**, with `los_displacement_cm: null`. Atmospheric/ionospheric corrections, a verified stable reference and a validated sign are not established. The actual Kumamoto catalog contains three separate compatible networks with **no redundant closure cycles**; catalog connectivity alone cannot verify unwrapping. The [ASF GUNW guide](https://nisar-docs.asf.alaska.edu/gunw/) documents the supplied phase/correction layers; the [MintPy workflow](https://mintpy.readthedocs.io/en/latest/) provides the reference context for future corrected time-series work.

The network planner also passed [eight live public-catalog point checks](docs/results/global_network_checks.json), returning 27 compatible networks and 211 unique day-pair edges. These checks discover available products; they do not download or validate eight phase stacks.

![Real NISAR phase and coherence; phase-equivalent scale is not validated deformation](docs/images/interferometry.png)

## Keep Git small

Git contains source, contracts, documentation, compact trained weights and selected previews. Raw chips, NetCDF/COG job outputs, caches, virtual environments and ZIP bundles stay outside Git. Download the measured fixtures from [Releases](https://github.com/Ch0comilo/NISAR-Modeling/releases), or regenerate outputs locally.

`python scripts/repository_audit.py` checks the tracked tree for forbidden data paths, oversized files and common credential structures. The total tracked-file budget is 10 MiB. A normal `.gitignore` change does not remove data already present in Git history; see [the data policy](docs/DATA_AND_GIT.md).

## Development

```powershell
.venv\Scripts\python.exe scripts\benchmark_global_model.py
.venv\Scripts\python.exe scripts\run_global_examples.py
.venv\Scripts\python.exe scripts\run_global_v2.py
.venv\Scripts\python.exe scripts\verify_global_v2.py
```

`train_global_example.py` retrains the experimental representation; it is separate from the frozen benchmark. The model's five training geographies and two holdouts are documented in its card. Future scientific work should prioritize independent geography/season validation, native NISAR support, coherent common reference support and correction provenance before stronger model or deformation claims.
