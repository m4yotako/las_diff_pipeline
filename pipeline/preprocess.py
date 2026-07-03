"""Preprocessing: reprojection, bbox clip, SOR, voxel downsample, ground filter."""

from __future__ import annotations

import logging

import numpy as np

from .io_las import PointCloud

logger = logging.getLogger(__name__)


def reproject(pc: PointCloud, target_epsg: int) -> PointCloud:
    if pc.crs_epsg == target_epsg or pc.crs_epsg is None or target_epsg is None:
        return pc
    try:
        import pyproj
    except ImportError as e:
        raise ImportError("pyproj required for reprojection") from e
    logger.info("Reprojecting EPSG:%d -> EPSG:%d", pc.crs_epsg, target_epsg)
    tr = pyproj.Transformer.from_crs(pc.crs_epsg, target_epsg, always_xy=True)
    x, y = tr.transform(pc.xyz[:, 0], pc.xyz[:, 1])
    new_xyz = np.column_stack([x, y, pc.xyz[:, 2]])
    return PointCloud(xyz=new_xyz, classification=pc.classification,
                      intensity=pc.intensity, crs_epsg=target_epsg, extras=pc.extras)


def clip_bbox(pc: PointCloud, bbox) -> PointCloud:
    minx, miny, maxx, maxy = bbox
    x, y = pc.xyz[:, 0], pc.xyz[:, 1]
    m = (x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy)
    n_in = int(m.sum())
    logger.info("Clip bbox: %d / %d points kept", n_in, len(pc.xyz))
    return PointCloud(xyz=pc.xyz[m],
                      classification=pc.classification[m] if pc.classification is not None else None,
                      intensity=pc.intensity[m] if pc.intensity is not None else None,
                      crs_epsg=pc.crs_epsg, extras=pc.extras)


def sor_filter(pc: PointCloud, k: int = 16, z_thresh: float = 2.0) -> PointCloud:
    """Statistical Outlier Removal (近傍距離が mean+z*std を超えた点を除去)."""
    try:
        from scipy.spatial import cKDTree
    except ImportError as e:
        raise ImportError("scipy required for SOR") from e
    if len(pc.xyz) < k + 1:
        return pc
    logger.info("SOR filter: k=%d, z=%.1f", k, z_thresh)
    tree = cKDTree(pc.xyz)
    d, _ = tree.query(pc.xyz, k=k + 1)
    md = d[:, 1:].mean(axis=1)
    th = md.mean() + z_thresh * md.std()
    keep = md <= th
    n_in = int(keep.sum())
    logger.info("  %d / %d kept (thresh=%.3f)", n_in, len(pc.xyz), th)
    return PointCloud(xyz=pc.xyz[keep],
                      classification=pc.classification[keep] if pc.classification is not None else None,
                      intensity=pc.intensity[keep] if pc.intensity is not None else None,
                      crs_epsg=pc.crs_epsg, extras=pc.extras)


def voxel_downsample(pc: PointCloud, voxel_size: float) -> PointCloud:
    """ボクセル間引き (各 voxel の重心を代表点)."""
    if voxel_size is None or voxel_size <= 0:
        return pc
    logger.info("Voxel downsample: %.3fm", voxel_size)
    xyz = pc.xyz
    ix = np.floor(xyz / voxel_size).astype(np.int64)
    # 一意なキーで集約
    key = ix[:, 0].astype(np.int64) * 73856093 ^ ix[:, 1] * 19349663 ^ ix[:, 2] * 83492791
    order = np.argsort(key)
    ks = key[order]
    xs = xyz[order]
    u, first = np.unique(ks, return_index=True)
    sums = np.add.reduceat(xs, first, axis=0)
    counts = np.diff(np.append(first, len(ks)))
    centroids = sums / counts[:, None]
    logger.info("  %d -> %d points", len(xyz), len(centroids))
    return PointCloud(xyz=centroids, crs_epsg=pc.crs_epsg, extras=pc.extras)


def classify_ground(pc: PointCloud, cell_size: float = 1.0) -> PointCloud:
    """簡易地表抽出: pixel ごとの min-Z 点だけ残す.

    本来は CSF (Cloth Simulation Filter) を使うが, ここではセル毎 min-Z で代用.
    """
    logger.info("Classify ground (min-Z per %.2fm cell)", cell_size)
    xyz = pc.xyz
    ix = np.floor(xyz[:, 0] / cell_size).astype(np.int64)
    iy = np.floor(xyz[:, 1] / cell_size).astype(np.int64)
    key = ix * 73856093 ^ iy * 19349663
    order = np.argsort(key)
    ks = key[order]; zs = xyz[order, 2]; idxs = order  # idxs[k] = original index
    u, first = np.unique(ks, return_index=True)
    # min-Z per cell: argmin via reduceat-like
    counts = np.diff(np.append(first, len(ks)))
    keep = np.zeros(len(xyz), dtype=bool)
    for k in range(len(u)):
        s, n = first[k], counts[k]
        local = zs[s:s+n]
        keep[idxs[s + int(np.argmin(local))]] = True
    n_in = int(keep.sum())
    logger.info("  %d / %d ground points", n_in, len(xyz))
    return PointCloud(xyz=pc.xyz[keep],
                      classification=pc.classification[keep] if pc.classification is not None else None,
                      intensity=pc.intensity[keep] if pc.intensity is not None else None,
                      crs_epsg=pc.crs_epsg, extras=pc.extras)


def preprocess(pc: PointCloud, cfg: dict, target_epsg: int | None = None) -> PointCloud:
    """前処理パイプラインを config に従って適用."""
    if target_epsg is not None and pc.crs_epsg is not None:
        pc = reproject(pc, target_epsg)

    bbox = cfg.get("bbox")
    if bbox:
        pc = clip_bbox(pc, bbox)

    sor_cfg = cfg.get("sor", {})
    if sor_cfg.get("enabled"):
        pc = sor_filter(pc, k=sor_cfg.get("k", 16), z_thresh=sor_cfg.get("z", 2.0))

    voxel = cfg.get("voxel_size")
    if voxel:
        pc = voxel_downsample(pc, voxel)

    gf = cfg.get("ground_filter", {})
    if gf.get("enabled"):
        pc = classify_ground(pc, cell_size=gf.get("cell_size", 1.0))

    return pc
