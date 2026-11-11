"""Prepare and serve bounded, georeferenced display assets."""
from hashlib import file_digest
import json


ASSET_LIMIT = 24 * 1024 * 1024
BUNDLE_LIMIT = 96 * 1024 * 1024


def checksum(path):
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")


def render_layer(out, prefix, layer_id, values, x, y, epsg, *, units, cmap, stretch, legend=None):
    import numpy as np
    import rasterio
    from matplotlib import colormaps
    from matplotlib.colors import to_rgba
    from PIL import Image
    from rasterio.transform import from_bounds, from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds
    from .engine import write_cog

    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    transform = from_origin(x[0] - dx / 2, y[0] + dy / 2, dx, dy)
    height, width = values.shape
    crs = f"EPSG:{epsg}"
    native_bounds = rasterio.transform.array_bounds(height, width, transform)
    bounds = transform_bounds(crs, "EPSG:4326", *native_bounds, densify_pts=21)
    if bounds[0] >= bounds[2]:
        raise ValueError("A single PNG cannot represent an antimeridian crossing.")
    values = np.asarray(values, dtype="float32")
    display = np.full((height, width), np.nan, dtype="float32")
    reproject(values, display, src_transform=transform, src_crs=crs, src_nodata=np.nan,
              dst_transform=from_bounds(*bounds, width, height), dst_crs="EPSG:4326",
              dst_nodata=np.nan, resampling=Resampling.nearest)
    valid = np.isfinite(display)
    low, high = stretch
    scaled = np.where(valid, np.clip((display - low) / (high - low), 0, 1), 0)
    rgba = colormaps[cmap](scaled, bytes=True)
    if legend:
        palette = ("#cbbf9d", "#0072b2", "#56b4e9", "#e69f00", "#cc79a7")
        for code, color in enumerate(palette):
            rgba[display == code] = np.asarray(to_rgba(color)) * 255
    rgba[~valid, 3] = 0
    Image.fromarray(rgba).save(out / f"{layer_id}.png")
    write_cog(out / f"{layer_id}.tif", values, x, y, epsg, units, [layer_id], angular=bool(legend) or units in {"rad", "diagnostic mask"})
    return {"id": layer_id, "png_url": f"{prefix}/{layer_id}.png", "cog_url": f"{prefix}/{layer_id}.tif",
            "bounds": list(bounds), "crs": "EPSG:4326", "north_up": True,
            "units": units, "colormap": "optical_reference_groups" if legend else cmap,
            "range": list(stretch), "legend": legend}


def publish_files(output, prefix):
    """Expose only top-level produced files with bounded transfer sizes."""
    allowed = {".tif": "image/tiff", ".png": "image/png", ".nc": "application/x-netcdf",
               ".csv": "text/csv", ".geojson": "application/geo+json"}
    assets = []
    total = 0
    for path in sorted(output.iterdir()):
        if not path.is_file() or path.is_symlink() or path.suffix not in allowed:
            continue
        size = path.stat().st_size
        if size > ASSET_LIMIT or total + size > BUNDLE_LIMIT:
            continue
        total += size
        assets.append({"name": path.name, "href": f"{prefix}/{path.name}", "media_type": allowed[path.suffix],
                       "bytes": size, "sha256": checksum(path)})
    return assets


def data_layers(output, prefix, *, coherence_index=0):
    import numpy as np
    import xarray as xr
    from .flood import GROUP_LEGEND

    arrays = output / "characterization_arrays.nc"
    if arrays.is_file():
        layers = []
        with xr.open_dataset(arrays, engine="h5netcdf") as ds:
            def add(layer_id, values, units, cmap, bounds, legend=None):
                layers.append(render_layer(output, prefix, layer_id, values, ds.x.values, ds.y.values,
                                           ds.attrs["epsg_code"], units=units, cmap=cmap,
                                           stretch=bounds, legend=legend))
            for pol in ds.polarization.values:
                suffix = str(pol).lower()
                add("change_db_" + suffix, ds.target_change_db.sel(polarization=pol).values,
                    "dB", "RdBu_r", (-8, 8))
                add("baseline_median_" + suffix, ds.baseline_db.sel(polarization=pol).values,
                    "dB", "gray", (-30, 0))
                power = ds.gamma0_power.sel(polarization=pol, time=ds.attrs["target_date"]).values
                with np.errstate(divide="ignore", invalid="ignore"):
                    target = np.where(power > 0, 10 * np.log10(power), np.nan)
                add("target_" + suffix, target, "dB", "gray", (-30, 0))
            groups = ds.reference_group.values
            add("reference_groups", np.where(groups == 255, np.nan, groups),
                "optical group code", "viridis", (0, 4), GROUP_LEGEND)
            add("signature_mask", np.where(ds.comparison_support.values, ds.candidate_darkening.values, np.nan),
                "diagnostic mask", "Blues", (0, 1))
        return layers

    for name in ("interferometric_interval_dynamics.nc", "correction_readiness_arrays.nc"):
        path = output / name
        if path.is_file():
            with xr.open_dataset(path, engine="h5netcdf") as ds:
                return [render_layer(output, prefix, "coherence", ds.coherence.values[coherence_index],
                                     ds.x.values, ds.y.values, ds.attrs["epsg_code"], units="unitless",
                                     cmap="cividis", stretch=(0, 1))]
    return []
