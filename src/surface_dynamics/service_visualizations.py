"""Derived display layers computed on measured GCOV grids."""
from dataclasses import dataclass

import numpy as np

from .analysis import POLARIZATION_PAIRS
from .service_artifacts import display_grid, native_grid, render_layer
from .service_contracts import Visualization, VisualizationComponent


POWER_RANGE = (-30, 0)
RECIPE_DETAILS = {
    "polarization_rgb": "False color combines co-pol return, cross-pol return and their balance. Roughness, structure, moisture and viewing geometry can all influence these channels.",
    "cross_co_ratio": "Cross-pol minus co-pol in dB. A change in this balance can come from either input channel.",
    "cross_co_ratio_change": "Later cross/co log ratio minus earlier cross/co log ratio. Both channels must be supported on both dates.",
    "three_date_rgb": "False color assigns three observed dates to red, green and blue. Equal returns across dates produce gray.",
    "temporal_median": "Cellwise median over the listed observations on complete per-channel support.",
    "temporal_mad": "Scaled median absolute deviation across the listed observations. This is observed variability in dB.",
}


@dataclass
class Frame:
    date: str
    bands: dict[str, np.ndarray]
    x: np.ndarray
    y: np.ndarray
    epsg: int

    def aligned_with(self, other):
        return (self.epsg == other.epsg and np.array_equal(self.x, other.x)
                and np.array_equal(self.y, other.y))


def gcov_tiff_frames(output, observations):
    import rasterio
    from rasterio.transform import xy

    frames = []
    for observation in observations:
        if not observation.get("layer"):
            continue
        day = observation["date"]
        with rasterio.open(output / f"{day}.tif") as source:
            x, _ = xy(source.transform, [0] * source.width, range(source.width))
            _, y = xy(source.transform, range(source.height), [0] * source.height)
            bands = {pol: source.read(index) for index, pol in enumerate(source.descriptions, 1)}
            frames.append(Frame(day, bands, x, y, source.crs.to_epsg()))
    return frames


def gcov_dataset_frames(dataset):
    dates = dataset.time.values.astype("datetime64[D]").astype(str)
    polarizations = dataset.polarization.values.astype(str)
    power = dataset.gamma0_power.transpose("time", "polarization", "y", "x").values
    valid = np.isfinite(power) & (power > 0)
    db = np.where(valid, 10 * np.log10(np.where(valid, power, 1)), np.nan)
    return [Frame(day, dict(zip(polarizations, values)), dataset.x.values, dataset.y.values,
                  int(dataset.attrs["epsg_code"])) for day, values in zip(dates, db)]


def component(band, label, polarizations, display_db):
    return VisualizationComponent(band=band, label=label, polarizations=polarizations,
                                  display_db=display_db)


def render_rgb(output, prefix, layer_id, values, frame, grid, components):
    from PIL import Image
    from .engine import write_cog

    display, bounds = display_grid(values, grid)
    valid = np.isfinite(display).all(axis=0)
    rgba = np.zeros((*valid.shape, 4), dtype="uint8")
    for index, channel in enumerate(components):
        low, high = channel.display_db
        rgba[valid, index] = np.floor(255 * np.clip((display[index, valid] - low) / (high - low), 0, 1))
    rgba[valid, 3] = 255
    Image.fromarray(rgba).save(output / f"{layer_id}.png")
    write_cog(output / f"{layer_id}.tif", values, frame.x, frame.y, frame.epsg, "dB",
              [channel.label for channel in components])
    return {"png_url": f"{prefix}/{layer_id}.png", "cog_url": f"{prefix}/{layer_id}.tif",
            "bounds": bounds, "colormap": "rgb"}


def render_gcov_visualizations(output, prefix, frames, target_date, comparison_dates=()):
    """Render from aligned arrays only. A missing target never borrows a different date."""
    history = sorted((frame for frame in frames if frame.date <= target_date), key=lambda frame: frame.date)
    if not history or history[-1].date != target_date:
        return []
    target = history[-1]
    grid = native_grid(target.x, target.y, target.epsg)
    views = []

    def add(recipe, suffix, title, dates, components, values, colormap="viridis"):
        values = np.asarray(values)
        valid = np.isfinite(values).all(axis=0) if values.ndim == 3 else np.isfinite(values)
        if not valid.any():
            return
        layer_id = f"view_{recipe}_{suffix}"
        values = np.where(valid, values, np.nan)
        if values.ndim == 3:
            asset = render_rgb(output, prefix, layer_id, values, target, grid, components)
        else:
            layer = render_layer(output, prefix, layer_id, values, target.x, target.y, target.epsg,
                                 units="dB", cmap=colormap, stretch=components[0].display_db)
            asset = {key: layer[key] for key in ("bounds", "colormap", "png_url", "cog_url")}
        views.append(Visualization(id=layer_id, recipe=recipe, title=title, dates=dates,
                                   interpretation=RECIPE_DETAILS[recipe], components=components,
                                   supported_cells=int(valid.sum()), total_cells=valid.size,
                                   grid=grid, **asset).model_dump(mode="json"))

    earlier = next((frame for frame in history if comparison_dates and frame.date == comparison_dates[0]), None)
    for co, cross in POLARIZATION_PAIRS:
        if not {co, cross} <= target.bands.keys():
            continue
        co_db, cross_db = target.bands[co], target.bands[cross]
        ratio = cross_db - co_db
        pair = f"{co.lower()}_{cross.lower()}"
        add("polarization_rgb", pair, f"Polarization RGB · {co}/{cross}", [target.date],
            [component("R", f"{co} dB", [co], (-25, 0)),
             component("G", f"{cross} dB", [cross], (-32, -5)),
             component("B", f"{cross} − {co} dB", [cross, co], (-18, -2))],
            [co_db, cross_db, ratio])
        add("cross_co_ratio", pair, f"Polarization balance · {cross}/{co}", [target.date],
            [component("value", f"{cross} − {co} dB", [cross, co], (-18, 0))], ratio)
        if (earlier and comparison_dates[-1] == target.date and target.aligned_with(earlier)
                and {co, cross} <= earlier.bands.keys()):
            delta = ratio - (earlier.bands[cross] - earlier.bands[co])
            add("cross_co_ratio_change", pair, f"Change in polarization balance · {cross}/{co}",
                [earlier.date, target.date],
                [component("value", f"Later minus earlier {cross}/{co} log ratio", [cross, co], (-8, 8))],
                delta, "RdBu_r")

    if len(history) < 3 or not all(target.aligned_with(frame) for frame in history):
        return views
    dates = [frame.date for frame in history]
    selected = [history[0], history[(len(history) - 1) // 2], target]
    for pol in target.bands:
        if not all(pol in frame.bands for frame in history):
            continue
        suffix = pol.lower()
        stack = np.stack([frame.bands[pol] for frame in history])
        valid = np.isfinite(stack).all(axis=0)
        median = np.median(stack, axis=0)
        mad = 1.4826 * np.median(np.abs(stack - median), axis=0)
        add("temporal_median", suffix, f"Temporal median · {pol}", dates,
            [component("value", f"Median {pol} dB", [pol], POWER_RANGE)], np.where(valid, median, np.nan), "gray")
        add("temporal_mad", suffix, f"Temporal variability · {pol}", dates,
            [component("value", f"Scaled {pol} MAD", [pol], (0, 8))], np.where(valid, mad, np.nan), "magma")
        if pol in {"HH", "VV"}:
            add("three_date_rgb", suffix, f"Three-date RGB · {pol}", [frame.date for frame in selected],
                [component(color, f"{pol} dB · {frame.date}", [pol], (-25, 0))
                 for color, frame in zip("RGB", selected)], [frame.bands[pol] for frame in selected])
    return views
