# Frontend handoff for measured radar visualizations

1. Use `GET /v2/coverage` for a selected WGS84 point and UTC window, then `POST /v2/jobs` with `operation: "explore"`. Poll `status_url` and display queued, running and unavailable states from the API. Observation Plan dates are schedule information.
2. Use `result.visualizations` for a combined-view selector. Render title, actual dates, support counts, component labels and fixed dB ranges. Drape each `png_url` using its own WGS84 bounds. Keep numeric `cog_url` downloads and original `layers` available.
3. Use `GET /v2/cases/{id}` for prepared views. Richmond has measured HH/HV and VV/VH. Kumamoto has coherence, adjacent-pair coherence contrast and common phase support. Label any live wrapped phase in radians and keep LOS null.
4. Fix Qadirabad's marked-point sentence. At (73.713, 32.329) HH and HV increased +0.715 and +1.259 dB versus the baseline median. The area summary separately has 332/516 flagged new-water reference cells. Replace demo dates and bars with `diagnostic_band_timeline`. Use manifest bounds for Chókwè and Richmond.
5. Show `previews` as figures or animations with their supplied interpretation labels. Keep GP model spread, spatial quartiles and temporal MAD distinct. Display a null observation-noise field as unestimated.

| Roadmap item | Science API handoff |
| --- | --- |
| Point or polygon | Current API takes points. Polygon sampling needs a separate AOI and resource contract with Data Lab. |
| Five months, five images | Use five months as a UI default. Live jobs cap measured compatible dates at five. A one-month spacing policy would need to select from actual acquisitions. |
| Ideal 10×10 km | Use the returned shape and posting. The audited 64×64 native 10 m request spans 640 m. Richmond's prepared grid spans 10.24 km. |
| L/S and mission switch | Current service publishes L-band products. Gate any future bands on measured availability and source metadata. |
| One minute | Prepared and cached routes are the judging path when time is limited. Show elapsed time and actual status for archive reads. |
| Downloads | PNGs, COGs and measured series are served by science. Globe screenshot and PDF assembly belong to the frontend. |

The Modal science explorer demonstrates the backend contract. The globe team owns integration and presentation. [API contract](API.md)
