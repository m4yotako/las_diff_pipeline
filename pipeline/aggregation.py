"""Aggregation: grid or building polygon."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .scoring import score_by_thresholds, building_score_from_stats

logger = logging.getLogger(__name__)


# ───────────────────────────────────────
# GRID aggregation
# ───────────────────────────────────────
def aggregate_grid(xyz, dz, cell_size, bbox=None, significant=None,
                    min_points: int = 10, thresholds=None):
    try:
        import geopandas as gpd
        from shapely.geometry import box
    except ImportError as e:
        raise ImportError("geopandas/shapely required") from e

    thresholds = thresholds or [0.3, 1.0, 3.0]
    x, y = xyz[:, 0], xyz[:, 1]
    if bbox is None:
        minx, miny, maxx, maxy = float(x.min()), float(y.min()), float(x.max()), float(y.max())
    else:
        minx, miny, maxx, maxy = bbox
    nx = int(np.ceil((maxx - minx) / cell_size))
    ny = int(np.ceil((maxy - miny) / cell_size))
    if nx <= 0 or ny <= 0:
        raise ValueError("Invalid bbox / cell_size")

    ix = np.floor((x - minx) / cell_size).astype(np.int64)
    iy = np.floor((y - miny) / cell_size).astype(np.int64)
    valid = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny) & np.isfinite(dz)
    ix, iy, dz_v = ix[valid], iy[valid], dz[valid]
    sig_v = significant[valid] if significant is not None else None

    flat = iy * nx + ix
    order = np.argsort(flat)
    flat_s = flat[order]; dz_s = dz_v[order]
    sig_s = sig_v[order] if sig_v is not None else None

    unique, first = np.unique(flat_s, return_index=True)
    counts = np.diff(np.append(first, len(flat_s)))

    records = []
    for k, key in enumerate(unique):
        n = int(counts[k])
        if n < min_points:
            continue
        seg = slice(first[k], first[k] + n)
        dz_seg = dz_s[seg]
        dz_mean = float(np.mean(dz_seg))
        dz_p95 = float(np.percentile(np.abs(dz_seg), 95))
        sig_ratio = float(np.mean(sig_s[seg])) if sig_s is not None else 1.0

        cy = key // nx; cx = key % nx
        x0 = minx + cx * cell_size
        y0 = miny + cy * cell_size
        geom = box(x0, y0, x0 + cell_size, y0 + cell_size)

        score = int(score_by_thresholds(np.array([dz_p95]), thresholds)[0])
        if sig_ratio < 0.5:
            score = max(score - 1, 0)

        records.append({
            "geometry": geom, "id": f"g_{int(key):07d}",
            "damage_score": score,
            "dz_mean": round(dz_mean, 3), "dz_p95": round(dz_p95, 3),
            "n_points": n, "significant_ratio": round(sig_ratio, 3),
            "method": "grid",
        })

    gdf = gpd.GeoDataFrame(records, geometry="geometry")
    logger.info("Grid aggregation: %d cells", len(gdf))
    return gdf


# ───────────────────────────────────────
# BUILDING polygons
# ───────────────────────────────────────
def load_buildings(cfg: dict, work_epsg: int):
    """建物ポリゴンを取得 (file or osm)."""
    try:
        import geopandas as gpd
    except ImportError as e:
        raise ImportError("geopandas required") from e

    source = cfg.get("source", "file")
    if source == "file":
        path = cfg.get("file")
        if not path:
            raise ValueError("aggregation.building.file is required when source='file'")
        gdf = gpd.read_file(path)
    elif source == "osm":
        try:
            import osmnx as ox
        except ImportError as e:
            raise ImportError("osmnx required for source='osm'") from e
        bbox = cfg.get("bbox")
        if bbox is None:
            raise ValueError("aggregation.building.bbox is required when source='osm'")
        from pyproj import Transformer
        tr = Transformer.from_crs(work_epsg, 4326, always_xy=True)
        minx, miny, maxx, maxy = bbox
        lon_min, lat_min = tr.transform(minx, miny)
        lon_max, lat_max = tr.transform(maxx, maxy)
        gdf = ox.features.features_from_bbox(
            north=lat_max, south=lat_min, east=lon_max, west=lon_min,
            tags={"building": True},
        )
        gdf = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    else:
        raise ValueError(f"Unknown building source: {source}")

    if gdf.crs is None:
        logger.warning("Buildings have no CRS; assuming EPSG:4326")
        gdf = gdf.set_crs(epsg=4326)
    gdf = gdf.to_crs(epsg=work_epsg)
    logger.info("Loaded %d buildings", len(gdf))
    return gdf


def aggregate_buildings(xyz_pre, xyz_post, dz, buildings_gdf, rules,
                         buffer: float = 0.5, significant=None):
    """建物ポリゴン毎に dz / 点喪失率を集計しスコア付与 (sjoin ベース)."""
    try:
        import geopandas as gpd
    except ImportError as e:
        raise ImportError("geopandas required") from e

    bgdf = buildings_gdf.reset_index(drop=True).copy()
    bgdf["_bidx"] = bgdf.index.astype(np.int64)
    if buffer and buffer > 0:
        bgdf["geometry"] = bgdf.geometry.buffer(buffer)
    crs = bgdf.crs

    pre_pts = gpd.GeoDataFrame(
        {"_pi": np.arange(len(xyz_pre), dtype=np.int64)},
        geometry=gpd.points_from_xy(xyz_pre[:, 0], xyz_pre[:, 1]),
        crs=crs,
    )
    sig_arr = significant if significant is not None else np.ones(len(dz), dtype=bool)
    post_pts = gpd.GeoDataFrame(
        {"_pi": np.arange(len(xyz_post), dtype=np.int64),
         "_dz": dz.astype(np.float64),
         "_sig": sig_arr.astype(np.float64)},
        geometry=gpd.points_from_xy(xyz_post[:, 0], xyz_post[:, 1]),
        crs=crs,
    )

    pre_join = gpd.sjoin(pre_pts, bgdf[["_bidx", "geometry"]], predicate="within", how="inner")
    post_join = gpd.sjoin(post_pts, bgdf[["_bidx", "geometry"]], predicate="within", how="inner")
    pre_counts = pre_join.groupby("_bidx").size().to_dict()

    records = []
    for bi, geom in enumerate(bgdf.geometry):
        post_in = post_join[post_join["_bidx"] == bi] if len(post_join) else post_join.iloc[:0]
        n_post = int(len(post_in))
        n_pre = int(pre_counts.get(bi, 0))
        if n_pre == 0 and n_post == 0:
            continue

        if n_post > 0:
            dz_seg = post_in["_dz"].to_numpy()
            dz_seg = dz_seg[np.isfinite(dz_seg)]
            dz_mean = float(np.mean(dz_seg)) if len(dz_seg) else float("nan")
            dz_p95 = float(np.percentile(np.abs(dz_seg), 95)) if len(dz_seg) else float("nan")
            sig_ratio = float(post_in["_sig"].mean()) if significant is not None else 1.0
        else:
            dz_mean = float("nan"); dz_p95 = float("nan"); sig_ratio = 0.0

        loss_ratio = 1.0 - (n_post / n_pre) if n_pre > 0 else 0.0
        loss_ratio = max(0.0, min(1.0, loss_ratio))

        stats = {
            "loss_ratio": loss_ratio,
            "dz_mean": dz_mean, "dz_p95": dz_p95,
            "n_points_pre": n_pre, "n_points_post": n_post,
        }
        merged_rules = dict(rules)
        merged_rules.setdefault("thresholds", [0.3, 1.0, 3.0])
        score = building_score_from_stats(stats, merged_rules)

        records.append({
            "geometry": geom, "id": f"b_{bi:07d}",
            "damage_score": score,
            "dz_mean": None if not np.isfinite(dz_mean) else round(dz_mean, 3),
            "dz_p95": None if not np.isfinite(dz_p95) else round(dz_p95, 3),
            "n_points_pre": n_pre, "n_points_post": n_post,
            "loss_ratio": round(loss_ratio, 3),
            "significant_ratio": round(sig_ratio, 3),
            "method": "building",
        })

    gdf = gpd.GeoDataFrame(records, geometry="geometry", crs=buildings_gdf.crs)
    logger.info("Building aggregation: %d buildings scored", len(gdf))
    return gdf
