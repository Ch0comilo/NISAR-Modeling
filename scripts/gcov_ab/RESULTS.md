# Measured GCOV comparison

Modal Modeling commit `b7b7c00f5d9953de497abbc0fafd434c308d9b96`, build fingerprint `39ab48e4e0cc6db6b931bd12`. Explorer and FastAPI used the local development checkouts based on `8313f7c` and `ce49194`, including the existing local preview changes. The Explorer clicked a point link, submitted through FastAPI, and drew the measured Modal PNG. The browser timing includes the local API and asset transfer. The pinned scene plan omits a fresh catalog search. These timings are for the local chain and one Modal region.

The Chocó experiment uses the exact requested coordinates. The prior 134.119 s worker read used the same scenes but snapped its point to `[-76.684, 5.669]`. The crop origin therefore differs from the previous benchmark. Both readers and both postings in this comparison use identical pixel edges at each site.

## Latency and transfers

Each row uses fresh chip namespaces. Times are medians in seconds. The visible-layer median excludes the CORS-affected browser observations and the confirmed mid-job reattachment. The full CSV retains them.

| Site | Reader | Posting | Complete / attempted | Read | Aggregate | Analyze | Render | Worker | Click to drape | HTTP requests | ASF MB | Browser PNG |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Chocó | h5py | 10 m | 2 / 2 | 121.22 | 0 | 1.18 | 1.30 | 123.72 | 129.75 | 234 | 117.44 | 256 × 256 |
| Chocó | pyfive | 10 m | 3 / 3 | 17.51 | 0 | 1.16 | 1.31 | 20.03 | 25.01 | 232 | 72.69 | 256 × 256 |
| Chocó | h5py | 40 m | 2 / 2 | 123.53 | 0.03 | 0.26 | 0.26 | 124.09 | 130.04 | 234 | 117.44 | 96 × 96 |
| Chocó | pyfive | 40 m | 2 / 2 | 17.41 | 0.03 | 0.28 | 0.26 | 17.98 | 23.06 | 232 | 72.69 | 96 × 96 |
| Qadirabad | h5py | 10 m | 2 / 2 | 113.66 | 0 | 1.15 | 1.32 | 116.16 | 123.21 | 214 | 106.95 | 256 × 256 |
| Qadirabad | pyfive | 10 m | 0 / 2 | 17.40 | 0 | 0.88 | 1.18 | 19.49 | 29.90 | 205.5 | 64.30 | 256 × 256 |
| Qadirabad | h5py | 40 m | 2 / 2 | 111.47 | 0.03 | 0.30 | 0.26 | 112.07 | 117.58 | 214 | 106.95 | 96 × 96 |
| Qadirabad | pyfive | 40 m | 3 / 3 | 18.54 | 0.03 | 0.29 | 0.26 | 19.13 | 24.78 | 234 | 72.55 | 96 × 96 |

Both partial Qadirabad jobs returned four scenes. The July 14 scene reported an archive authorization failure in one and a generic archive read failure in the other. Those trials characterize time to a partial result.

The first invocation in the measured worker container was Chocó pyfive/10 m, with 5.123 s from submission to worker entry and 18.517 s of reads. Subsequent invocations in that container measured 0.383–1.334 s to worker entry. Cold-container comparisons across all four variants remain unmeasured.

| Chocó pyfive/40 m trial | Read (s) | Click to drape (s) | ASF requests / MB for this click | Browser PNGs |
| --- | ---: | ---: | ---: | --- |
| 10.24 km, fresh chips and result | 30.405 | 41.469 | 432 / 123.63 | 256 × 256, 154049 bytes on the globe and 1835 in the panel |
| 3.84 km, five reused chips and a fresh result | 0.010 | 5.795 | 0 / 0 | 96 × 96, 22411 bytes on the globe and 432 in the panel |
| 10.24 km, complete result cache hit | — | 3.245 | 0 / 0 | Same PNGs as the fresh 10.24 km job |

Pyfive/10 m in Chocó reached the globe in 24.435–25.595 s on the timed fresh runs and sent a 209664-byte globe PNG plus a 9469-byte panel PNG, both 256 × 256. Pyfive/40 m in Qadirabad sent a 25967-byte globe PNG and a 1087-byte panel PNG, both 96 × 96. Each complete 384-pixel pyfive run recorded 20 batch calls and up to four simultaneous HTTP requests per scene. The h5py control recorded zero batch calls.

## Numerical checks

| Site and footprint | Native EPSG and edges in metres (left, bottom, right, top) | Common cells at 10 m | Common cells at 40 m |
| --- | --- | ---: | ---: |
| Chocó, 3.84 km square | 32618 · 311540, 624930, 315380, 628770 | 147456 / 147456 | 9216 / 9216 |
| Qadirabad, 3.84 km square | 32643 · 376940, 3575720, 380780, 3579560 | 147456 / 147456 | 9216 / 9216 |
| Chocó, 10.24 km square | 32618 · 308340, 621730, 318580, 631970 | 1048576 / 1048576 in the input chips | 65536 / 65536 |

The two readers produced identical coordinates, raw masks, and linear HH/HV arrays on all five matched 384-pixel chips per site. For Qadirabad, the complete pyfive/40 m run supplied the raw 10 m input chip for July 14. That scene failed in both fresh pyfive/10 m jobs. The other four native pyfive/10 m COGs matched the reference. The independent linear-power reference matched all checked 40 m COGs within 0.00000215 dB with the same masks and native pixel edges. The 1024-pixel comparison checks the pyfive input chips against the aggregated COGs.

| Measurement from complete stacks | 10 m | 40 m |
| --- | ---: | ---: |
| Chocó target point HH / HV (dB) | −20.55 / −19.12 | −17.99 / −21.73 |
| Qadirabad target point HH / HV (dB) | −9.72 / −14.11 | −9.71 / −16.48 |
| Chocó dual darkening fraction | 8276 / 147456 · 5.61% | 20 / 9216 · 0.22% |
| Chocó co-pol brightening fraction | 9316 / 147456 · 6.32% | 2 / 9216 · 0.02% |
| Qadirabad dual darkening fraction | 12686 / 147456 · 8.60% | 337 / 9216 · 3.66% |
| Qadirabad co-pol brightening fraction | 12090 / 147456 · 8.20% | 153 / 9216 · 1.66% |

The 40 m point value represents the mean linear power of its 16 contributors, converted to dB. A fully supported Chocó block crossed by a mapped waterway near `[-76.68811, 5.66486]` spans −19.65 to −1.49 dB in native HH and returns −9.38 dB at 40 m. A fully supported Qadirabad block near the crop edge (row 92, column 2 of the 40 m grid) spans 29.65 dB in native HH. All native cells were supported in these windows. The strict behavior for a block with a masked cell is covered by the regression test. The diagnostic percentages describe the sampled signal under the specified grid and baseline. The experiment used radar evidence only.

## Interpretation

The reader is the primary latency lever. A 40 m output still reads the entire 10 m crop. In the first complete Chocó pyfive runs, both postings transferred 72,694,557 ASF bytes. Changing posting reduced analysis from about 1.17 s to 0.28 s and rendering from about 1.31 s to 0.26 s. The 40 m option changes the measured point value and the flood/wetland signatures. Use native 10 m for the default quantitative result and offer 40 m as a clearly labelled option for larger windows. Keep the h5py control and validate fallback for failed pyfive scenes before promoting it.

Preview failure records include the initial redirect handling and metadata compatibility failures, three FastAPI queue rejections during harness recovery, and the partial pyfive/10 m Qadirabad runs. The first two successful browser runs used a local origin that could decode the panel PNG but lacked CORS for Cesium textures. The first Chocó h5py/40 m browser observation reattached to a running job. Use the fresh replay of each affected variant for click-to-drape timing. A cached result carries the original worker timings and these should not be counted as new work.

The per-run CSV records cache identifiers, failures, browser PNG sizes, and all stage timings. The original result JSON files retain the archive metrics for each scene and the browser measurements for each asset. Reproduce the evidence with the [benchmark instructions](README.md).
