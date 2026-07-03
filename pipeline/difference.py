"""Difference computation: DSM diff (primary), M3C2 (PDAL, optional)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .io_las import PointCloud

logger = logging.getLogger(__name__)


# ───────────────────────────────────────
# DSM diff
# ───────────────────────────────────────
def rasterize_dsm(xyz: np.ndarray, resolution: float, bbox, method: str = "max"):
    """xyz をグリッドに集約し DSM ラスタ (h, w) を返す.

    method: "max" / "min" / "mean".
    """
    minx, miny, maxx, maxy = bbox
    nx = int(np.ceil((maxx - minx) / resolution))
    ny = int(np.ceil((maxy - miny) / resolution))
    x = xyz[:, 0]; y = xyz[:, 1]; z = xyz[:, 2]
    ix = np.clip(((x - minx) / resolution).astype(np.int64), 0, nx - 1)
    iy = np.clip(((y - miny) / resolution).astype(np.int64), 0, ny - 1)
    flat = iy * nx + ix
    order = np.argsort(flat)
    flat_s = flat[order]; z_s = z[order]
    u, first = np.unique(flat_s, return_index=True)

    if method == "max":
        agg = np.maximum.reduceat(z_s, first)
    elif method == "min":
        agg = np.minimum.reduceat(z_s, first)
    elif method == "mean":
        sum_z = np.add.reduceat(z_s, first)
        counts = np.diff(np.append(first, len(flat_s)))
        agg = sum_z / counts
    else:
        raise ValueError(f"Unknown DSM method: {method}")

    raster = np.full(nx * ny, np.nan, dtype=np.float64)
    raster[u] = agg
    # 慣例: row 0 が上 → 上下反転
    raster = raster.reshape(ny, nx)[::-1, :]
    return raster


def dsm_diff(pre: PointCloud, post: PointCloud, resolution: float = 0.5, method: str = "max"):
    """pre / post 両方をラスタ化して差 (post - pre) を返す."""
    # 共通 bbox: 両者の和集合
    minx = min(pre.xyz[:, 0].min(), post.xyz[:, 0].min())
    miny = min(pre.xyz[:, 1].min(), post.xyz[:, 1].min())
    maxx = max(pre.xyz[:, 0].max(), post.xyz[:, 0].max())
    maxy = max(pre.xyz[:, 1].max(), post.xyz[:, 1].max())
    bbox = (minx, miny, maxx, maxy)

    pre_r = rasterize_dsm(pre.xyz, resolution, bbox, method=method)
    post_r = rasterize_dsm(post.xyz, resolution, bbox, method=method)
    diff = post_r - pre_r
    n_valid = int(np.isfinite(diff).sum())
    logger.info("DSM diff: shape=%s, valid=%d", diff.shape, n_valid)
    return diff, bbox


def write_geotiff(raster: np.ndarray, bbox, path, epsg: int):
    """ラスタを GeoTIFF で書き出す."""
    try:
        import rasterio
        from rasterio.transform import from_bounds
    except ImportError as e:
        raise ImportError("rasterio required") from e

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    minx, miny, maxx, maxy = bbox
    h, w = raster.shape
    transform = from_bounds(minx, miny, maxx, maxy, w, h)
    with rasterio.open(
        str(path), "w",
        driver="GTiff", height=h, width=w, count=1,
        dtype=raster.dtype, crs=f"EPSG:{epsg}", transform=transform,
        nodata=np.nan,
    ) as ds:
        ds.write(raster, 1)
    logger.info("Wrote GeoTIFF: %s", path)


# ───────────────────────────────────────
# M3C2 (PDAL) — オプション
# ───────────────────────────────────────
def m3c2(pre, post, work_dir, normal_scale=2.0, projection_scale=1.0, max_depth=10.0):
    """PDAL の filters.m3c2 を呼ぶ.

    PDAL がインストールされている環境でのみ動作。
    Returns (dz, lod95) — post の各点に対応する差分と LoD95.
    """
    try:
        import pdal
    except ImportError as e:
        raise ImportError("pdal (python-pdal) is required for M3C2") from e

    raise NotImplementedError(
        "M3C2 is a stub. Install PDAL and implement the pipeline JSON here. "
        "For now use difference.method='dsm' in your config."
    )
