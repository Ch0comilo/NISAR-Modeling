# Geographic holdout for the exploration lenses

Five locations and their date windows were fixed before querying NISAR. They are 242–2,051 km from the nearest named points in the checked development configs, result summaries and local worldwide inventory. The runner records the locations and source hashes before catalog discovery, retains every location, and uses one compatible HH/HV series per site across all assigned lenses. All five sites returned five acquisitions with 4,096 common 10 m radar cells per site. The optical comparisons use a coarser grid of 256 cells per site.

## Agriculture

The lens compares a 60 m focal patch with the 120–300 m annulus around it. The contrast shift is focal minus annulus in dB, relative to the median of that contrast on the three baseline dates. The comparison uses the same cells on all five dates. The 2025 USDA Cropland Data Layer classifies the three center points as cropland. The land cover of the full neighborhoods and crop activity in 2026 remain unverified. [USDA Cropland Data Layer](https://www.nass.usda.gov/Research_and_Science/Cropland/metadata/metadata_CDL25_FGDC-STD-001-1998.htm)

| Site | Common focal and annulus cells | First October HH shift | Last October HH shift |
| --- | --- | ---: | ---: |
| Palouse upland, Washington | 116 and 2,375 | −3.12 dB on Oct 10 | −2.68 dB on Oct 22 |
| High Plains upland, Kansas | 113 and 2,378 | +1.02 dB on Oct 11 | +0.49 dB on Oct 23 |
| Platte Valley, Nebraska | 111 and 2,374 | +0.44 dB on Oct 9 | +0.28 dB on Oct 21 |

## Flood, vegetation disturbance and wildfire

| Fixed site and comparison | Observed result |
| --- | --- |
| Baja California Sur, flood and wetland | The July 26 and Sep 13 OPERA water snapshots shared 179 of 256 usable cells. All 179 were stable nonwater. The selected marker and snapshots provided no observed new-open-water group for a positive-case comparison. |
| High Plains, flood and wetland | Zero of 256 cells had usable optical observations in both windows. The radar result is available and the optical comparison was withheld. |
| Timber Fire, vegetation disturbance and wildfire | The Oct 22 OPERA snapshot showed confirmed loss in 157 of 256 usable cells within the observation bracket. Thirteen loss cells had unique baseline-matched controls. The matched HH contrast was −1.34 dB with a conditional spatial resampling interval of −3.17 to +0.08 dB. For HV it was +0.17 dB with an interval of −0.29 to +1.06 dB. Both intervals include zero. The wildfire diagnostic found 816 of 4,096 radar cells below the HV baseline band at both October dates. |
| High Plains, vegetation disturbance and wildfire | The Oct 26 OPERA snapshot had 256 usable cells, with zero confirmed loss in the bracket. The same wildfire diagnostic still found 79 of 4,096 cells below the HV band at both October dates. |

The event markers supply regional context. The comparison evaluates transfer beyond the known development points and tests how the lenses handle weak or missing evidence. Estimating detection performance requires independent, dated labels and multiple events and comparison sites. [GDACS Mexico flood report](https://www.gdacs.org/report.aspx?episodeid=7&eventid=1104099&eventtype=FL) · [CAL FIRE Timber Fire](https://www.fire.ca.gov/incidents/2026/8/8/timber-fire/)

## Second holdout with external footprints and field dates

Three Swedish trial locations, three mapped footprints or epochs for one Crete flood, and two US wildfire perimeters were fixed before retrieving NISAR. Crop contrasts compare the patch and its nearby annulus before and after the recorded trial date. Flood and fire comparisons use common radar cells inside and outside the mapped areas with a 40 m boundary exclusion. Omalos failed the reference geometry screen and was retained as unavailable.

| Reference and observation | Result |
| --- | --- |
| Swedish crop trial records | Sköllersta HH/HV shifts were +1.78/+1.05 dB across Aug 10–22. Råbelöv was −0.02/+0.66 dB across July 19–Aug 12 and Gotland was +0.13/+0.06 dB across July 27–Aug 20. Full harvested boundaries were unavailable. |
| Tzermiado, Crete · Oct 4 Sentinel-1 map | Dual darkening in 80/1,717 mapped inundation cells and 11/1,984 nearby. NISAR followed by 53 minutes. |
| Tzermiado, Crete · Oct 8 GeoEye-1 map | Dual darkening in 79/1,668 mapped inundation cells and 8/1,999 nearby. NISAR followed by 32 hours. The two snapshots cover one flood. |
| Bug Fire final perimeter | Repeated HV decline on Aug 28 and Sep 9 in 127/1,737 cells inside and 18/1,813 outside. The Sep 9 vegetation lens flagged 547/1,737 inside and 367/1,813 outside. |
| Weiser Knoll final perimeter | The original track returned zero usable pixels across five dates. The next ranked track showed repeated HV decline on Aug 26 and Sep 7 in 54/1,819 inside and 27/1,765 outside. |

Fire perimeters supply event context and can enclose unburned areas. The comparisons test whether diagnostics concentrate near independently observed change. Estimating detection performance requires multiple independent events and dated reference labels at comparable support. [Swedish rye trial](https://nfts.dlbr.dk/Forms/Dokumentation.aspx?GUID=6c558a36-cd71-4531-8119-ca05278419a2&KardexID=75095&applLangID=en) · [Crete mapping](https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code=EMSR933) · [Bug perimeter](https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/WFIGS_Interagency_Perimeters/FeatureServer/0/61953) · [Weiser perimeter](https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services/WFIGS_Interagency_Perimeters/FeatureServer/0/63540)

The [example requests](../contracts/examples/exploration-lens-requests.json) replay the radar through the local HTTP API. In the rehearsal, Weiser Knoll automatically recovered from track `157_D_067_4005_DHDH` to `019_A_023_4005_DHDH` and the sparse polar point reported no catalog coverage. The Crete radar lens completed with insufficient joint OPERA coverage. The Bug Fire vegetation request had 256 usable optical cells and zero confirmed loss in the bracket. These API results come from the automatic OPERA comparison. The holdout table uses the separate event references listed above.

## Reproduce the first holdout locally

With Earthdata access configured, run from the repo root. The [enrollment](../configs/exploration_holdout_v1.json) holds coordinates and windows. The runner uses live Earthdata catalog results, so a later run may see additional granules. Preserve the original output for exact review.

```powershell
.\.venv\Scripts\python.exe -B scripts/run_exploration_holdout.py --output scratch/exploration-holdout-replay
```

The replay directory contains `freeze.json`, `summary.json`, per-site coverage and lens reports, and measured raster layers. The holdout covers the four registered backscatter lenses. The InSAR pair workflow has a separate measurement contract.
