# Surface Dynamics v0.2.0

Worldwide descriptive NISAR science workers for exploration and teaching. This is the initial lightweight GitHub publication; measured fixtures and the portable handoff are release assets, keeping generated data out of Git history.

- Physical, spectral and stochastic analysis of measured surface trajectories; experimental shared masked temporal Transformer, fixed latent colors, arrays and map probes.
- InSAR pair validation and observability: separate native grids, coherence-threshold sweeps, connected components, ionosphere-filled support and typed no-data results.
- New global InSAR network planning: compatible acquisition graphs, connected components, temporal-baseline spanning forests, context bracketing and potential closure paths. Catalog topology only; no phase inversion or displacement claim.
- Expanded frozen-model benchmark covers 20 internal acquisitions in two held-out regions. Both simple baselines outperform the Transformer; full results and limitations are documented.
- Eight versioned request/result schemas, examples, Python API/CLI, teammate handoff and CI with 22 offline tests.

Download `nisar-science-teammate-handoff.zip` for seven measured cubes (66 acquisition chips), a real earthquake pair, trained weights, provenance, code, examples and the v0.2.0 wheel. No Earthdata login is required for those offline fixtures. `SHA256SUMS.txt` checks the assets; the repository's `scripts/download_examples.py` verifies and restores only the measured input fixtures.

Forecasting and confirmed event classification are outside the implementation. InSAR output remains uncorrected phase/phase-equivalent scale, with `los_displacement_cm: null`. Worldwide processing capability is not proof of global learned-model accuracy. Frontend and mockup files are excluded and unchanged.
