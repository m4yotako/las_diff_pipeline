"""ICP registration (Open3D primary, scipy fallback)."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .io_las import PointCloud

logger = logging.getLogger(__name__)


def _load_stable_mask(path):
    try:
        import geopandas as gpd
    except ImportError as e:
        raise ImportError("geopandas is required for stable_mask") from e
    return gpd.read_file(path)


def _mask_points_in_polygons(xyz, gdf, epsg):
    from shapely.geometry import Point
    if gdf.crs is None:
        logger.warning("stable_mask has no CRS; assuming EPSG:%d", epsg)
        gdf = gdf.set_crs(epsg=epsg)
    elif gdf.crs.to_epsg() != epsg:
        gdf = gdf.to_crs(epsg=epsg)
    union = gdf.unary_union
    mask = np.array([union.contains(Point(p[0], p[1])) for p in xyz], dtype=bool)
    logger.info("Stable mask: %d / %d points inside", int(mask.sum()), len(xyz))
    return mask


def icp_align(source: PointCloud, target: PointCloud,
              stable_mask_path=None, max_iterations: int = 50, threshold: float = 0.5):
    if source.crs_epsg != target.crs_epsg:
        raise ValueError("CRS must match before ICP")

    if stable_mask_path is not None:
        gdf = _load_stable_mask(stable_mask_path)
        m_src = _mask_points_in_polygons(source.xyz, gdf, source.crs_epsg)
        m_tgt = _mask_points_in_polygons(target.xyz, gdf, target.crs_epsg)
        src_xyz = source.xyz[m_src]
        tgt_xyz = target.xyz[m_tgt]
    else:
        logger.warning("stable_mask not provided. Using all points; displaced regions may bias.")
        src_xyz = source.xyz
        tgt_xyz = target.xyz

    if len(src_xyz) < 100 or len(tgt_xyz) < 100:
        raise RuntimeError("Too few points for ICP")

    try:
        import open3d  # noqa: F401
        T = _icp_open3d(src_xyz, tgt_xyz, max_iterations, threshold)
    except ImportError:
        logger.info("open3d not available; using scipy-based ICP fallback")
        T = _icp_scipy(src_xyz, tgt_xyz, max_iterations, threshold)

    homo = np.column_stack([source.xyz, np.ones(source.n)])
    new_xyz = (homo @ T.T)[:, :3]
    aligned = PointCloud(xyz=new_xyz, classification=source.classification,
                         intensity=source.intensity, crs_epsg=source.crs_epsg, extras=source.extras)
    return aligned, T


def _icp_open3d(src_xyz, tgt_xyz, max_iter, threshold):
    import open3d as o3d
    src = o3d.geometry.PointCloud(); src.points = o3d.utility.Vector3dVector(src_xyz)
    tgt = o3d.geometry.PointCloud(); tgt.points = o3d.utility.Vector3dVector(tgt_xyz)
    result = o3d.pipelines.registration.registration_icp(
        src, tgt, max_correspondence_distance=threshold, init=np.eye(4),
        estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPoint(),
        criteria=o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=max_iter),
    )
    logger.info("ICP(open3d) fitness=%.4f rmse=%.4f", result.fitness, result.inlier_rmse)
    return np.asarray(result.transformation)


def _icp_scipy(src_xyz, tgt_xyz, max_iter, threshold):
    """scipy.cKDTree ベースの自前 point-to-point ICP (Umeyama 法)."""
    from scipy.spatial import cKDTree
    max_pts = 200_000
    rs = np.random.RandomState(42)
    if len(src_xyz) > max_pts:
        idx = rs.choice(len(src_xyz), max_pts, replace=False)
        src = src_xyz[idx].astype(np.float64)
    else:
        src = src_xyz.astype(np.float64)
    tgt = tgt_xyz.astype(np.float64)
    tree = cKDTree(tgt)

    T = np.eye(4); prev_rmse = np.inf; last_it = 0
    for it in range(max_iter):
        src_h = np.column_stack([src, np.ones(len(src))])
        cur = (src_h @ T.T)[:, :3]
        d, idx = tree.query(cur, distance_upper_bound=threshold * 5.0)
        good = np.isfinite(d) & (d < threshold * 5.0)
        if good.sum() < 100:
            break
        s = cur[good]; t = tgt[idx[good]]
        sm = s.mean(axis=0); tm = t.mean(axis=0)
        U, _, Vt = np.linalg.svd((s - sm).T @ (t - tm))
        D = np.eye(3)
        if np.linalg.det(Vt.T @ U.T) < 0:
            D[2, 2] = -1
        R = Vt.T @ D @ U.T
        tr = tm - R @ sm
        dT = np.eye(4); dT[:3, :3] = R; dT[:3, 3] = tr
        T = dT @ T
        rmse = float(np.sqrt((d[good] ** 2).mean()))
        last_it = it
        if abs(prev_rmse - rmse) < 1e-4:
            break
        prev_rmse = rmse
    logger.info("ICP(scipy) iters=%d rmse=%.4f", last_it + 1, prev_rmse)
    return T
