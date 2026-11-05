"""Score fresh L-band observations against earlier independent water-class examples."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch
import numpy as np
from rasterio.warp import transform
from scipy.ndimage import convolve, distance_transform_edt
from scipy.spatial import cKDTree
from scipy.stats import rankdata

from surface_dynamics.analysis import measured_db
from surface_dynamics.embedding_atlas import aggregate_hh_hv, sample_blocks
from surface_dynamics.engine import safe_json, write_cog
from surface_dynamics.flood import load_reference
from surface_dynamics.flood_signatures import reference_groups
from surface_dynamics.io import load_cube
from surface_dynamics.representation_probe import coarsen

from prepare_flood_evidence import ROOT, digest, freeze, read_json, write_json

OUTPUT = ROOT / "products/flood_similarity"
HOLDOUT = ROOT / "products/flood_similarity_holdout"
RECIPE = ROOT / "configs/flood_similarity_holdout.json"
DEVELOPMENT = (
    ("barh", "bihar_ganga", "products/flood_evidence_b/cases/barh"),
    ("matam", "senegal_river", "products/flood_evidence_a/cases/matam"),
    ("ndioum", "senegal_river", "products/flood_evidence_a/cases/ndioum"),
    ("qadirabad", "chenab", "products/flood_pilot/cases/qadirabad"),
)
CLASSES = {0: "stable_nonwater", 1: "new_open_water", 2: "stable_open_water"}
FEATURES = ("target_HH_db", "target_HV_db", "HH_change_db", "HV_change_db",
            "HH_scaled_change", "HV_scaled_change", "neighbor_HH_change_db", "neighbor_HV_change_db")
VIEWS = {"image": 2, "image_history": 6, "image_history_neighborhood": 8}
MAIN = "image_history_neighborhood"
K = 5


def scene(cube, baseline, target):
    cube = aggregate_hh_hv(cube, posting=80)
    dates = np.asarray([*baseline[-3:], target], dtype="datetime64[D]")
    if len(dates) != 4 or not (np.diff(dates) > np.timedelta64(0, "D")).all():
        raise ValueError("Three recent acquisitions and one later target are required")
    indices = [list(cube["dates"].astype(str)).index(str(day)) for day in dates]
    db = measured_db(cube)[indices].astype(float)
    common = np.isfinite(db).all(axis=(0, 1))
    base = np.full_like(db[0], np.nan)
    mad = base.copy()
    base[:, common] = np.median(db[:3, :, common], axis=0)
    mad[:, common] = 1.4826 * np.median(abs(db[:3, :, common] - base[:, common]), axis=0)
    change = np.where(common, db[-1] - base, np.nan)
    kernel = np.ones((3, 3))
    kernel[1, 1] = 0
    count = convolve(common.astype(float), kernel, mode="constant")
    nearby = np.stack([np.divide(convolve(np.nan_to_num(channel), kernel, mode="constant"), count,
                                 out=np.full_like(channel, np.nan), where=count >= 4) for channel in change])
    features = np.concatenate([db[-1], change, change / np.maximum(mad, 1), nearby]).transpose(1, 2, 0)
    support = common & np.isfinite(features).all(axis=-1)
    return {"cube": cube, "dates": dates, "db": db, "features": features, "support": support}


def load_development(case, region, path):
    cube = load_cube(path / "cube.npz")
    if case == "qadirabad":
        before = [day for day in cube["dates"].astype(str) if day <= "2026-07-21"]
        target = "2026-07-26"
        reference, _ = load_reference(path / "reference_pair.npz", cube, before[-1], target, "2026-07-22")
        if reference is None:
            raise ValueError("Qadirabad optical reference fails its original time gate")
        cube, reference = coarsen(cube, reference, posting=80)
    else:
        admission = read_json(path / "admission.json")
        assert admission["status"] == "admitted"
        before, target = admission["metadata"]["recent_dates"], admission["metadata"]["target_date"]
        with np.load(path / "reference.npz", allow_pickle=False) as data:
            reference = {key: data[key] for key in data.files}
    return {"id": case, "region": region, **scene(cube, before, target), "groups": reference_groups(reference)}


def library(scenes):
    banks = {}
    for index, item in enumerate(scenes):
        for code in CLASSES:
            mask = item["support"] & (item["groups"] == code)
            cells = sample_blocks(mask, 128 if item["region"] == "senegal_river" else 256, 4, 220 + index)
            entries = banks.setdefault((code, item["region"]), [])
            entries.extend((index, int(row), int(col)) for row, col in zip(*np.unravel_index(cells, mask.shape)))
    banks = {key: np.asarray(entries) for key, entries in banks.items() if len(entries) >= K}
    if any(sum(code == key[0] for key in banks) < 2 for code in CLASSES):
        raise ValueError("Each optical class requires libraries from at least two separate regions")
    samples = {key: np.stack([scenes[i]["features"][row, col] for i, row, col in entries])
               for key, entries in banks.items()}
    # Equal total weight per optical class, then per region within that class.
    weights = {key: 1 / (3 * sum(other[0] == key[0] for other in samples)) for key in samples}
    center = sum(weights[key] * x.mean(0) for key, x in samples.items())
    scale = np.maximum(1., np.sqrt(sum(weights[key] * ((x-center)**2).mean(0) for key, x in samples.items())))
    return banks, samples, center, scale


def distances(samples, center, scale, x, width):
    z = ((x - center) / scale)[:, :width]
    result = {}
    for key, reference in samples.items():
        tree = cKDTree(((reference-center)/scale)[:, :width])
        distance, ids = tree.query(z, k=K)
        result[key] = (distance.mean(1)/np.sqrt(width), ids[:, 0])
    return result


def similarity(result):
    # Require resemblance in two regions for every competing optical class.
    d = np.stack([np.sort([entry[0] for key, entry in result.items() if key[0] == code], axis=0)[:2].mean(0)
                  for code in CLASSES], axis=-1)
    rival = np.minimum(d[:, 0], d[:, 2])
    return 100 * np.divide(rival, d[:, 1] + rival, out=np.full_like(rival, .5), where=(d[:, 1] + rival) > 0), d


def auc(positive, control):
    n, m = len(positive), len(control)
    return float((rankdata(np.r_[positive, control])[:n].sum() - n*(n+1)/2)/(n*m)) if n and m else None


def metrics(score, groups):
    rows = {}
    for code, name in CLASSES.items():
        values = score[groups == code]
        rows[name] = {"cells": len(values), "median": float(np.median(values)) if len(values) else None,
                      "above_50": int((values > 50).sum())}
    return {"groups": rows, "auc_vs_stable_nonwater": auc(score[groups == 1], score[groups == 0]),
            "auc_vs_stable_open_water": auc(score[groups == 1], score[groups == 2])}


def observation(item, row, col):
    cube = item["cube"]
    lon, lat = transform(f'EPSG:{int(cube["epsg"])}', "EPSG:4326", [cube["x"][col]], [cube["y"][row]])
    return {"row": int(row), "column": int(col), "point": [lon[0], lat[0]],
            "dates": item["dates"].astype(str).tolist(), "HH_HV_db": item["db"][:, :, row, col],
            "features": dict(zip(FEATURES, item["features"][row, col]))}


def illustrations(item, scores, labels, result, banks, scenes):
    rows, cols = np.where(item["support"])
    examples = []
    for code, quantile in ((1, .9), (1, .1), (2, .9), (0, .5)):
        ids = np.flatnonzero(labels == code)
        if not len(ids):
            continue
        j = ids[np.argmin(abs(scores[ids] - np.quantile(scores[ids], quantile)))]
        matches = []
        for group in (1, 0, 2):
            ranked = sorted((key for key in result if key[0] == group), key=lambda key: result[key][0][j])
            for key in ranked[:2 if group == 1 else 1]:
                distance, ids = result[key]
                i, r, c = banks[key][ids[j]]
                matches.append({"optical_group": CLASSES[group], "region": key[1], "case": scenes[i]["id"],
                                "regional_mean_5_neighbor_distance": distance[j], **observation(scenes[i], r, c)})
        examples.append({"optical_group": CLASSES[code], "within_group_score_quantile": quantile, "score": scores[j],
                         **observation(item, rows[j], cols[j]), "reference_examples": matches})
    return examples


def render(path, title, item, score, groups, examples):
    fig, ax = plt.subplots(2, 3, figsize=(13, 7.7), layout="constrained")
    maps = ax[0]
    settings = {"origin": "upper", "interpolation": "nearest"}
    im = maps[0].imshow(score, cmap="viridis", vmin=0, vmax=100, **settings)
    maps[0].set_title("Flood similarity score")
    fig.colorbar(im, ax=maps[0], shrink=.74, label="Relative similarity (0–100)")
    colors = ["#cbb79b", "#0072b2", "#63c8e7", "#e5e5e5"]
    resolved = np.where(np.isin(groups, [0, 1, 2]), groups, 3)
    maps[1].imshow(resolved, cmap=ListedColormap(colors), vmin=-.5, vmax=3.5, **settings)
    maps[1].set_title("Independent optical transitions")
    maps[1].legend(handles=[Patch(color=c, label=n.replace("_", " ")) for c, n in
                            zip(colors, [*CLASSES.values(), "other or unavailable"])], fontsize=7)
    im = maps[2].imshow(item["features"][:, :, 2], cmap="RdBu_r", vmin=-8, vmax=8, **settings)
    maps[2].set_title("HH target minus recent median")
    fig.colorbar(im, ax=maps[2], shrink=.74, extend="both", label="dB")
    for a in maps:
        a.set(xlabel="Column (eastward)", ylabel="Row (southward)")
    for index, (axis, example) in enumerate(zip(ax[1], examples), start=1):
        for a in maps:
            a.scatter(example["column"], example["row"], s=75, facecolor="none", edgecolor="black", lw=1.2)
            a.annotate(str(index), (example["column"]+2, example["row"]-2),
                       fontsize=8, color="black", bbox=dict(facecolor="white", alpha=.7, edgecolor="none", pad=1))
        for row, color, style, label in (
            (example, "#141c26", "-", "New scene"),
            (example["reference_examples"][0], "#0072b2", "--", example["reference_examples"][0]["case"]),
            (example["reference_examples"][1], "#a45009", "--", example["reference_examples"][1]["case"]),
        ):
            days = np.array(row["dates"], dtype="datetime64[D]")
            days = (days-days[-1]).astype(int)
            axis.plot(days, np.asarray(row["HH_HV_db"])[:, 0], marker="o", ms=3, ls=style, color=color, label=label)
        axis.set(title=f'{index}  {example["optical_group"].replace("_", " ")}  ·  {example["score"]:.0f}/100',
                 xlabel="Days relative to each radar target", ylabel="Observed HH (dB)")
        axis.legend(fontsize=8)
    for axis in ax[1, min(len(examples), 3):]:
        axis.axis("off")
    fig.suptitle(f'{title}\nNISAR target {item["dates"][-1]}  ·  80 m grid', fontsize=15)
    fig.savefig(path, dpi=140)
    plt.close(fig)


def predict(case, admission, folder, output, banks, samples, center, scale, scenes):
    for name, checksum in admission["input_sha256"].items():
        if digest(folder / name) != checksum:
            raise ValueError(f"Holdout measurement changed for {case['id']}/{name}")
    dates = admission["metadata"]
    item = scene(load_cube(folder / "cube.npz"), dates["recent_dates"], dates["target_date"])
    support = item["support"]
    x = item["features"][support]
    # All held-out radar scores are computed without accessing the query optical pixels.
    views = {name: distances(samples, center, scale, x, width) for name, width in VIEWS.items()}
    scores, d = similarity(views[MAIN])
    with np.load(folder / "reference.npz", allow_pickle=False) as ref:
        groups = reference_groups(ref)
        wet = (ref["pre_open_class_fraction"] >= .8) & (ref["pre_valid_coverage"] >= .8)
    labels = groups[support]
    distance = distance_transform_edt(~wet, sampling=80)[support] if wet.any() else np.full(len(x), np.nan)
    stats = {name: metrics(similarity(v)[0], labels) for name, v in views.items()}
    context = {}
    for code, label in CLASSES.items():
        group = labels == code
        context[label] = {"median_distance_m_to_pre_observed_water": np.median(distance[group]) if group.any() else None,
                          "median_HH_HV_change_db": np.median(x[group, 2:4], axis=0) if group.any() else None}
    examples = illustrations(item, scores, labels, views[MAIN], banks, scenes)
    out = output / case["id"]
    out.mkdir(exist_ok=True)
    score_map = np.full(support.shape, np.nan)
    score_map[support] = scores
    cube = item["cube"]
    np.savez_compressed(out / "maps.npz", score=score_map, reference_groups=np.where(support, groups, 255),
                        radar_support=support, class_distances=d, x=cube["x"], y=cube["y"], epsg=cube["epsg"])
    write_cog(out / "similarity.tif", score_map, cube["x"], cube["y"], cube["epsg"], "relative score 0-100")
    render(out / "evidence.png", case["place"], item, score_map, np.where(support, groups, 255), examples)
    return {"case_id": case["id"], "admission_status": admission["status"], "region": case["region"],
            "status": "comparison_available", "radar_dates": item["dates"].astype(str).tolist(),
            "optical_dates": {p: v["source"]["observation_time"][:10] for p, v in admission["reference_epochs"].items()},
            "radar_cells": int(support.sum()), "metrics": stats, "geographic_context": context,
            "examples": examples, "event_source": case["event_source"],
            "artifacts": {p.name: digest(p) for p in out.iterdir() if p.suffix != ".json"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--holdout", type=Path, default=HOLDOUT)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    files = [RECIPE, Path(__file__), *(ROOT / name for name in (
        "scripts/prepare_flood_evidence.py", "src/surface_dynamics/embedding_atlas.py",
        "src/surface_dynamics/analysis.py", "src/surface_dynamics/flood_signatures.py",
        "src/surface_dynamics/representation_probe.py", "src/surface_dynamics/schemas/flood-policy.json"))]
    for case, _, path in DEVELOPMENT:
        names = ("cube.npz", "reference_pair.npz", "reference_pair.json") if case == "qadirabad" else (
            "cube.npz", "reference.npz", "admission.json")
        files.extend(ROOT / path / name for name in names)
    freeze(args.output / "protocol.json", {"files": {p.relative_to(ROOT).as_posix(): digest(p) for p in files},
           "features": list(FEATURES), "neighbors_per_region_class": K,
           "scope": "Earlier regions set features, scale and examples. New regional optical data are for assessment."})
    scenes = [load_development(case, region, ROOT / path) for case, region, path in DEVELOPMENT]
    banks, samples, center, scale = library(scenes)
    write_json(args.output / "library.json", safe_json({"center": center, "scale": scale, "feature_order": FEATURES,
        "cells": {f"{region}/{CLASSES[code]}": len(banks[code, region]) for code, region in banks}}))
    if args.freeze_only:
        return
    admission_path = args.holdout / "admission.json"
    freeze(args.output / "holdout_snapshot.json", {"admission_sha256": digest(admission_path)})
    admissions = {row["case_id"]: row for row in read_json(admission_path)}
    summary = []
    for case in read_json(RECIPE)["cases"]:
        a = admissions[case["id"]]
        row = {"case_id": case["id"], "admission_status": a["status"], "region": case["region"]}
        if a["status"] == "admitted":
            result = predict(case, a, args.holdout / "cases" / case["id"], args.output, banks, samples, center, scale, scenes)
            path = args.output / case["id"] / "result.json"
            write_json(path, safe_json(result))
            row["href"] = path.relative_to(args.output).as_posix()
        summary.append(row)
        print(row, flush=True)
    write_json(args.output / "index.json", {"contract_version": "1.0", "cases": summary})


if __name__ == "__main__":
    main()
