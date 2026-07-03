"""Export to GeoJSON."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def export_geojson(gdf, path, target_epsg: int = 4326):
    """GeoDataFrame を WGS84 (or 指定 EPSG) に再投影して GeoJSON で書き出す."""
    import geopandas as gpd  # noqa: F401

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if gdf.crs is None:
        logger.warning("GeoDataFrame has no CRS; cannot reproject")
        out = gdf
    else:
        src_epsg = gdf.crs.to_epsg()
        if src_epsg != target_epsg:
            logger.info("Reprojecting %d features: EPSG:%s -> EPSG:%s",
                        len(gdf), src_epsg, target_epsg)
            out = gdf.to_crs(epsg=target_epsg)
        else:
            out = gdf

    out.to_file(str(path), driver="GeoJSON")
    logger.info("Wrote GeoJSON: %s (N=%d)", path, len(out))
