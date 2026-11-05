# Surface Dynamics v0.3.0

Continued modeling for worldwide exploration of observed surfaces, with no forecasting or automatic phenomenon classifier.

- Trained a 42,292-parameter visible-normalized residual temporal Transformer on nine regions and broader seasonal data. Thirteen regions at common 160 m posting provide 128 accepted regional acquisition chips; Hokkaido and Pantanal are new geographic tests.
- Frozen comparison across 34 masked acquisitions: v2 improves on v1 in all four evaluated regions, while interpolation remains slightly better on both new tests. Visible-only Matérn GP reconstruction and empirical conditional coverage checks are included. No test-driven tuning followed the new evaluation.
- Added constant-trajectory control contrasts, fixed training-only dynamic RGB, observed-date latent contrast rates, arrays, COGs, animations and map probes. These are descriptive latent quantities, not classes, motion or event probabilities.
- Added measured three-pair Kumamoto InSAR interval processing with exact native-grid intersection, coherence contrasts and shared-reference gating. The 5 km window lacks common phase support; a 15 km sensitivity window has 4.57%, with reference stability unverified. No corrections, integration or displacement claim.
- Improved input/NoData/source-cache checks, preserved wholly invalid acquisition exclusions, added point-level support outcomes, and documented ten versioned request/result schemas.

The portable ZIP includes both compact trained models, the installable wheel, code/contracts, thirteen common-posting regional fixtures, legacy inputs and two real three-pair InSAR windows. Satellite data and generated products stay out of Git. The download helper pins the archive checksum, restores only curated input paths, verifies all files before writing and preserves different existing user measurements. In the extracted portable ZIP it verifies already bundled inputs without network access.

The release is exploratory: two fresh geographic cases do not establish global model accuracy; conditional GP intervals remain uncalibrated. No frontend/mockup files are included or changed.
