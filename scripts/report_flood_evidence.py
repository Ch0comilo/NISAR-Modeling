"""Compare frozen radar diagnostics with independent optical transitions."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
import numpy as np
from surface_dynamics.analysis import measured_db
from surface_dynamics.engine import safe_json, write_cog
from surface_dynamics.flood_signatures import POLICY, match_controls, reference_groups, spatial_contrast
from surface_dynamics.io import load_cube
from surface_dynamics.surface_features import from_db

from prepare_flood_evidence import CONFIG, DEFAULT_OUT, ROOT, block_count, digest, freeze, read_json, write_json

GROUPS = {0: "stable_nonwater", 1: "new_open_water", 2: "stable_open_water",
          3: "open_water_loss", 4: "mixed_or_partial", 255: "optical_unsupported"}
SCORED_GROUPS = (0, 1, 2, 3)
GROUP_COLORS = {0: "#b49b72", 1: "#0072b2", 2: "#4ab8df", 3: "#d55e00", 4: "#b9709d", 255: "#ededed"}


def diagnostics(db, dates):
    """Run existing thresholds on a single measured target and fixed support."""
    features = from_db(db, dates, np.arange(len(dates) - 1), [len(dates) - 1])
    maps = features["maps"]
    delta = maps["first_change_db"]
    support = maps["common_support"]
    return maps, {
        "recent_mad_departure": maps["assessment_departure_mask"][0],
        "recent_dual_darkening": support & (delta[0] <= -POLICY["co_pol_change_db"]) &
                                (delta[1] <= -POLICY["cross_pol_darkening_db"]),
    }


def groups_table(groups, support, flags, minimum_block_size):
    table = {}
    for code in SCORED_GROUPS:
        eligible = support & (groups == code)
        cells = int(eligible.sum())
        flagged = int((flags & eligible).sum())
        table[GROUPS[code]] = {
            "eligible_cells": cells, "flagged_cells": flagged,
            "flag_fraction": flagged / cells if cells else None,
            "occupied_spatial_blocks": block_count(eligible, minimum_block_size),
        }
    return table


def measured_group(db, maps, mask, dates, pols):
    change = maps["first_change_db"]
    result = {"cells": int(mask.sum()), "series": [], "target_change_db_by_channel": {},
              "target_cross_minus_co_change_db": None, "local_co_change_contrast_db": None}
    if not mask.any():
        return result
    for index, pol in enumerate(pols):
        values = db[:, index][:, mask]
        quartiles = np.quantile(values, [.25, .5, .75], axis=1).T
        result["series"].extend(
            {"date": day, "polarization": pol, "p25_db": p25, "median_db": center, "p75_db": p75}
            for day, (p25, center, p75) in zip(dates, quartiles)
        )
        result["target_change_db_by_channel"][pol] = float(np.median(change[index][mask]))
    result["target_cross_minus_co_change_db"] = float(np.median(maps["first_ratio_change_db"][mask]))
    local = maps["local_co_contrast_db"][mask]
    if np.isfinite(local).any():
        result["local_co_change_contrast_db"] = float(np.nanmedian(local))
    return result


def render(path, report, cube, arrays):
    x, y = cube["x"] / 1000, cube["y"] / 1000
    half = (x[1] - x[0]) / 2
    extent = (x[0] - half, x[-1] + half, y[-1] - half, y[0] + half)
    opts = dict(extent=extent, origin="upper", interpolation="nearest")
    fig, axes = plt.subplots(2, 3, figsize=(13, 8), layout="constrained")
    pols = report["observations"]["polarizations"]
    for index, ax in enumerate(axes[0, :2]):
        im = ax.imshow(arrays["target_change_db"][index], cmap="RdBu_r", vmin=-8, vmax=8, **opts)
        ax.set_title(f'{pols[index]} at target minus recent median')
        fig.colorbar(im, ax=ax, shrink=.7, extend="both", label="dB")
    group_axis = axes[0, 2]
    codes = list(GROUPS)
    colors = [GROUP_COLORS[code] for code in codes]
    group_index = np.searchsorted(codes, arrays["reference_groups"])
    group_axis.imshow(group_index, cmap=ListedColormap(colors),
                      norm=BoundaryNorm(np.arange(len(codes) + 1) - .5, len(codes)), **opts)
    group_axis.set_title("Optical water-class transitions")
    group_axis.legend(handles=[Patch(color=color, label=GROUPS[code].replace("_", " "))
                               for code, color in zip(codes, colors)], fontsize=7, loc="upper left")

    agree_ax = axes[1, 0]
    agreement_labels = ["New water and radar flag", "New water without radar flag",
                        "Radar flag in other resolved group", "Other resolved group without flag", "Unscored"]
    agreement_colors = ["#0072b2", "#65c8e8", "#e69f00", "#ded7c5", "#f2f2f2"]
    agree_ax.imshow(arrays["departure_reference_agreement"], cmap=ListedColormap(agreement_colors),
                    norm=BoundaryNorm(np.arange(6) - .5, 5), **opts)
    agree_ax.set_title("Recent baseline flag vs optical map")
    agree_ax.legend(handles=[Patch(color=color, label=label) for color, label in zip(agreement_colors, agreement_labels)],
                    fontsize=6.5, loc="upper left")
    for ax in (*axes[0], agree_ax):
        ax.set(xlabel="Easting (km)", ylabel="Northing (km)")
        ax.tick_params(labelsize=8)
        ax.ticklabel_format(useOffset=False, style="plain")

    for ax, pol in zip(axes[1, 1:], pols):
        for code in (1, 0, 2):
            name = GROUPS[code]
            group = report["observed_groups"][name]
            rows = [r for r in group["series"] if r["polarization"] == pol]
            if not rows:
                continue
            dates = np.array([r["date"] for r in rows], dtype="datetime64[D]")
            ax.scatter(dates, [r["median_db"] for r in rows], s=22, color=GROUP_COLORS[code],
                       label=f'{name.replace("_", " ")} (n={group["cells"]:,})', zorder=3)
            ax.vlines(dates, [r["p25_db"] for r in rows], [r["p75_db"] for r in rows],
                      lw=1.1, color=GROUP_COLORS[code], alpha=.7)
        ax.axvline(np.datetime64(report["observations"]["target_date"]), lw=.8, ls="--", color=".45")
        ax.set(title=f"Measured {pol} by optical group", ylabel="Median and interquartile range (dB)")
        ax.tick_params(axis="x", labelrotation=35, labelsize=8)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="best")

    date = report["observations"]["target_date"]
    pre, post = (report["optical_dates"][phase] for phase in ("pre", "post"))
    fig.suptitle(f'{report["place"]}\nTarget radar {date}  ·  Optical {pre} and {post}  ·  '
                 f'{report["admission_status"].replace("_", " ")}', fontsize=14)
    fig.savefig(path, dpi=140)
    plt.close(fig)


def report_case(case, admission, recipe, folder):
    status = admission["status"]
    report = {"contract_version": "1.0", "case_id": case["id"], "place": case["place"], "point": case["point"],
              "region": case["region"], "event_source": case["event_source"], "admission_status": status,
              "phenomenon_context": "documented_flood_event", "event_attribution": None,
              "admission": admission, "observations": None, "optical_dates": None,
              "observed_groups": None, "matched_contrasts": None, "comparison": None, "artifacts": []}
    inputs = admission.get("input_sha256", {})
    if not inputs:
        return report
    for name, expected in inputs.items():
        if digest(folder / name) != expected:
            raise ValueError(f"Measured input hash mismatch for {case['id']}/{name}")
    cube = load_cube(folder / "cube.npz")
    with np.load(folder / "reference.npz", allow_pickle=False) as data:
        reference = {name: data[name] for name in data.files}
    pols = ["HH", "HV"] if {"HH", "HV"} <= set(cube["polarizations"]) else ["VV", "VH"]
    indices = [list(cube["polarizations"]).index(pol) for pol in pols]
    db, dates = measured_db(cube)[:, indices], cube["dates"].astype(str).tolist()
    maps, flags = diagnostics(db, dates)
    support = maps["common_support"]
    groups = np.where(support, reference_groups(reference), 255).astype("uint8")
    new_water = groups == 1
    controls, matched, matching = match_controls(maps["baseline_db"], new_water, groups == 0,
                                                 POLICY["control_match_tolerance_db"])
    contrasts = {pol: spatial_contrast(maps["first_change_db"][index], matched, controls, POLICY)
                 for index, pol in enumerate(pols)}
    groups_data = {name: measured_group(db, maps, groups == code, dates, pols)
                   for code, name in GROUPS.items() if code != 255}
    report.update(
        observations={"baseline_dates": dates[:-1], "target_date": dates[-1], "polarizations": pols,
                      "epsg": int(cube["epsg"]), "posting_m": float(cube["x"][1] - cube["x"][0]),
                      "shape": list(support.shape), "common_radar_cells": int(support.sum()),
                      "target_bracket_dates": dates[-2:]},
        optical_dates={phase: admission["reference_epochs"][phase]["source"]["observation_time"][:10]
                       for phase in ("pre", "post")},
        observed_groups=groups_data,
        matched_contrasts={"matching": matching, "mean_new_minus_matched_nonwater_db": contrasts},
    )
    recent_flag = flags["recent_mad_departure"]
    resolved = np.isin(groups, SCORED_GROUPS)
    agreement = np.full(groups.shape, 4, dtype="uint8")
    agreement[resolved & new_water & recent_flag] = 0
    agreement[resolved & new_water & ~recent_flag] = 1
    agreement[resolved & ~new_water & recent_flag] = 2
    agreement[resolved & ~new_water & ~recent_flag] = 3
    arrays = {"target_change_db": maps["first_change_db"], "reference_groups": groups,
              "departure_reference_agreement": agreement, "radar_common_support": support,
              "matched_positive": matched, "matched_control": controls,
              **flags, "x": cube["x"], "y": cube["y"], "epsg": cube["epsg"]}
    if status == "admitted":
        size = recipe["spatial_block_pixels"]
        comparison = {"recent": {name: groups_table(groups, support, mask, size) for name, mask in flags.items()},
                      "seasonal": None, "unit": "analysis_cell",
                      "comparison_scope": "optical_product_agreement_at_one_matched_target_per_case"}
        if admission["seasonal"]["status"] == "supported":
            seasonal = load_cube(folder / "seasonal_cube.npz")
            if not np.array_equal(cube["x"], seasonal["x"]) or not np.array_equal(cube["y"], seasonal["y"]) or cube["epsg"] != seasonal["epsg"]:
                raise ValueError("Seasonal and recent cubes must share the exact projected grid")
            seasonal_db = measured_db(seasonal)[:, [list(seasonal["polarizations"]).index(pol) for pol in pols]]
            if str(seasonal["dates"][-1]) != dates[-1] or not np.array_equal(seasonal_db[-1], db[-1], equal_nan=True):
                raise ValueError("Seasonal comparison must use the identical measured target")
            prior, prior_flags = diagnostics(seasonal_db, seasonal["dates"])
            shared = support & prior["common_support"]
            comparison["seasonal"] = {"shared_radar_cells": int(shared.sum()),
                                      "recent_on_shared_support": groups_table(groups, shared, recent_flag, size),
                                      "prior_season_on_shared_support": groups_table(groups, shared,
                                                                                   prior_flags["recent_mad_departure"], size)}
            arrays.update(seasonal_shared_support=shared, seasonal_mad_departure=prior_flags["recent_mad_departure"])
        report["comparison"] = comparison

    output = folder / "report"
    output.mkdir(exist_ok=True)
    np.savez_compressed(output / "maps.npz", **arrays)
    for key, units, bands, categorical in (
        ("target_change_db", "dB", pols, False),
        ("reference_groups", "see optical group codes", ["optical_group"], True),
        ("departure_reference_agreement", "see agreement codes", ["departure_vs_optical"], True),
    ):
        write_cog(output / f"{key}.tif", arrays[key], cube["x"], cube["y"], cube["epsg"], units, bands, categorical)
    report = safe_json(report)
    render(output / "evidence.png", report, cube, arrays)
    report["artifacts"] = [{"href": path.name, "sha256": digest(path), "bytes": path.stat().st_size}
                           for path in sorted(output.iterdir()) if path.suffix in (".png", ".tif", ".npz")]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--recipe", type=Path, default=CONFIG)
    args = parser.parse_args()
    recipe = read_json(args.recipe)
    admissions = read_json(args.output / "admission.json")
    protocol = read_json(args.output / "protocol_freeze.json")
    if protocol["protocol_id"] != recipe["protocol_id"] or protocol["recipe_sha256"] != digest(args.recipe) or any(
        digest(ROOT / name) != checksum for name, checksum in protocol["files"].items()
    ):
        raise ValueError("Admission protocol or dependencies changed")
    freeze(args.output / "analysis_freeze.json", {"protocol": digest(args.output / "protocol_freeze.json"),
           "admissions": digest(args.output / "admission.json"), "scorer": digest(Path(__file__))})
    if [case["id"] for case in recipe["cases"]] != [entry["case_id"] for entry in admissions]:
        raise ValueError("Admission manifest must retain the entire declared cohort in order")
    summary = []
    for case, admission in zip(recipe["cases"], admissions):
        folder = args.output / "cases" / case["id"]
        result = report_case(case, admission, recipe, folder)
        path = folder / "report" / "result.json"
        write_json(path, result)
        summary.append({"case_id": case["id"], "region": case["region"], "status": result["admission_status"],
                        "href": path.relative_to(args.output).as_posix(), "sha256": digest(path)})
    write_json(args.output / "index.json", {"contract_version": "1.0", "protocol_id": recipe["protocol_id"],
                                          "cases": summary})


if __name__ == "__main__":
    main()
