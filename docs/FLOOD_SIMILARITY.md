# Flood similarity in a new region

The held-out 80 m NISAR chip is in western Pursat, Cambodia. The target was September 28, 2026, 48 days after the last baseline radar date. Optical references were August 15 and September 29. [Regional flood record](https://data.gdacs.org/Floods/report.aspx?episodeid=10&eventid=1104132&eventtype=FL) · [OPERA](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-HLS_V1)

| Optical reference group | Cells | Median similarity | Cells above 50 |
|---|---:|---:|---:|
| New open water | 656 | 55.4 | 541 (82.5%) |
| Stable nonwater | 1,131 | 32.1 | 11 (1.0%) |
| Stable open water | 44 | 57.9 | 44 (100%) |

Pairwise ranking AUC against land was 0.965 for the image, 0.975 with history and 0.974 with neighbors. Against existing water it was 0.276, 0.188 and 0.246. A post-run check using HH darkening alone gave 0.984 and 0.517. One of eight sites qualified. Paired optical coverage was 28.4%. Seasonal water changes and spatial dependence limit interpretation.

## Score and handoff

The frozen library contains 1,228 earlier Barh, Matam, Ndioum and Qadirabad cells from three regions. Features use target HH/HV, signed change against three prior dates, MAD-scaled change and mean change in the surrounding cells. Distances use development-only scaling. Each class distance averages the five nearest samples within each of the two closest regions. The score is `100 × d_other / (d_new + d_other)`, where `d_other` is the better of two stable control classes. Scores above 50 indicate closer resemblance to new-water examples. Geography and optical classes in Pursat are evaluation context.

Run `python scripts/flood_similarity.py` with the inputs from the release bundle. The [holdout recipe](../configs/flood_similarity_holdout.json) can regenerate archive inputs with `prepare_flood_evidence.py`. The index records all admission outcomes. Per-case JSON supplies scores, actual dates, WGS84 example locations and matching histories. The COG contains relative similarity. The NPZ stores class distances in row-major `radar_support` order and columns for stable nonwater, new water and stable water. Geographic distance in JSON refers to pre-existing water visible in the optical chip.
