"""LAS → GeoJSON 被害スコアリング パイプライン CLI.

使い方:
    python main.py --config config.yaml
    python main.py --config config.yaml --step preprocess
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

import numpy as np

from pipeline.config import Config, load_config
from pipeline.io_las import read_multi, write_las
from pipeline.preprocess import preprocess
from pipeline.registration import icp_align
from pipeline.difference import m3c2, dsm_diff, write_geotiff
from pipeline.aggregation import aggregate_grid, aggregate_buildings, load_buildings
from pipeline.scoring import apply_significance
from pipeline.export import export_geojson


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run(
    cfg: Config,
    only_step: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> None:
    """パイプライン実行。

    progress_cb: 各ステップ開始時に呼ばれるコールバック。
                 引数は "1/7: Read LAS" 形式の文字列。
                 None のときは無視（後方互換）。
    """
    def _step(msg: str) -> None:
        logging.info("=== %s ===", msg)
        if progress_cb is not None:
            progress_cb(msg)

    out_dir = cfg.resolve_path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_epsg = cfg.crs.get("input_epsg")
    work_epsg = cfg.crs.get("work_epsg", input_epsg)
    output_epsg = cfg.crs.get("output_epsg", 4326)

    # ─── 1. 読み込み ───
    # io.pre_las / io.post_las は単一パスまたはリスト（タイル分割データ）を許容する。
    def _resolve_las_paths(key: str) -> list:
        value = cfg.io[key]
        values = value if isinstance(value, list) else [value]
        return [cfg.resolve_path(v) for v in values]

    _step("1/7: Read LAS")
    pre = read_multi(_resolve_las_paths("pre_las"), fallback_epsg=input_epsg)
    post = read_multi(_resolve_las_paths("post_las"), fallback_epsg=input_epsg)
    if pre.crs_epsg is None:
        pre.crs_epsg = input_epsg
    if post.crs_epsg is None:
        post.crs_epsg = input_epsg

    # ─── 2. 前処理 ───
    _step("2/7: Preprocess")
    pre = preprocess(pre, cfg.preprocess, target_epsg=work_epsg)
    post = preprocess(post, cfg.preprocess, target_epsg=work_epsg)
    if only_step == "preprocess":
        write_las(pre, out_dir / "pre_pre.las")
        write_las(post, out_dir / "post_pre.las")
        return

    # ─── 3. 位置合わせ ───
    _step("3/7: Registration")
    reg_cfg = cfg.registration
    if reg_cfg.get("enabled", True):
        post, T = icp_align(
            post,
            pre,
            stable_mask_path=(
                cfg.resolve_path(reg_cfg["stable_mask"]) if reg_cfg.get("stable_mask") else None
            ),
            max_iterations=reg_cfg.get("max_iterations", 50),
            threshold=reg_cfg.get("threshold", 0.5),
        )
        np.savetxt(out_dir / "icp_transform.txt", T, fmt="%.6f")
    if only_step == "registration":
        write_las(post, out_dir / "post_aligned.las")
        return

    # ─── 4. 差分計算 ───
    _step("4/7: Difference")
    diff_cfg = cfg.difference
    method = diff_cfg.get("method", "dsm")  # デフォルトを dsm に変更（m3c2はスタブのため）

    dz = None
    lod95 = None
    dsm_path = None

    if method == "m3c2":
        m3c2_cfg = diff_cfg.get("m3c2", {})
        dz, lod95 = m3c2(
            pre,
            post,
            work_dir=out_dir / "_m3c2_tmp",
            normal_scale=m3c2_cfg.get("normal_scale", 2.0),
            projection_scale=m3c2_cfg.get("projection_scale", 1.0),
            max_depth=m3c2_cfg.get("max_depth", 10.0),
        )
        # dz は post の各点に対応
        write_las(post, out_dir / "diff_points.las", extra_dims={"dz": dz})

    elif method == "dsm":
        dsm_cfg = diff_cfg.get("dsm", {})
        diff_raster, bbox = dsm_diff(
            pre,
            post,
            resolution=dsm_cfg.get("resolution", 0.5),
            method=dsm_cfg.get("method", "max"),
        )
        dsm_path = out_dir / "dsm_diff.tif"
        write_geotiff(diff_raster, bbox, dsm_path, epsg=work_epsg)
        # DSM 差分の場合, 各ピクセル中心を「点」として扱う
        h, w = diff_raster.shape
        minx, miny, maxx, maxy = bbox
        xs = np.linspace(minx, maxx, w, endpoint=False) + (maxx - minx) / (2 * w)
        ys = np.linspace(maxy, miny, h, endpoint=False) - (maxy - miny) / (2 * h)
        gx, gy = np.meshgrid(xs, ys)
        valid = np.isfinite(diff_raster)
        post = type(post)(
            xyz=np.column_stack([gx[valid], gy[valid], np.zeros(valid.sum())]),
            crs_epsg=work_epsg,
        )
        dz = diff_raster[valid].astype(np.float32)
        lod95 = None

    if only_step == "difference":
        return

    # ─── 5. 有意性判定 + スコアリング ───
    _step("5/7: Scoring")
    sc_cfg = cfg.scoring
    thresholds = sc_cfg.get("thresholds", [0.3, 1.0, 3.0])
    use_sig = sc_cfg.get("use_significance", True)

    if use_sig and lod95 is not None:
        from pipeline.scoring import score_by_thresholds

        prelim_score = score_by_thresholds(dz, thresholds)
        prelim_score, significant = apply_significance(dz, lod95, prelim_score)
    else:
        significant = None

    # ─── 6. 集約 ───
    _step("6/7: Aggregation")
    agg_cfg = cfg.aggregation
    unit = agg_cfg.get("unit", "grid")

    if unit == "grid":
        grid_cfg = agg_cfg.get("grid", {})
        gdf = aggregate_grid(
            xyz=post.xyz,
            dz=dz,
            cell_size=grid_cfg.get("cell_size", 5.0),
            significant=significant,
            min_points=grid_cfg.get("min_points", 10),
            thresholds=thresholds,
        )
        gdf = gdf.set_crs(epsg=work_epsg)

    elif unit == "building":
        b_cfg = dict(agg_cfg.get("building", {}))
        if b_cfg.get("source", "file") == "file" and b_cfg.get("file"):
            b_cfg["file"] = str(cfg.resolve_path(b_cfg["file"]))
        buildings = load_buildings(b_cfg, work_epsg=work_epsg)
        rules = dict(sc_cfg.get("building_rules", {}))
        rules["thresholds"] = thresholds
        gdf = aggregate_buildings(
            xyz_pre=pre.xyz,
            xyz_post=post.xyz,
            dz=dz,
            buildings_gdf=buildings,
            rules=rules,
            buffer=b_cfg.get("buffer", 0.5),
            significant=significant,
        )

    else:
        raise ValueError(f"Unknown aggregation.unit: {unit}")

    # ─── 7. エクスポート ───
    _step("7/7: Export GeoJSON")
    out_path = out_dir / "damage_score.geojson"
    export_geojson(gdf, out_path, target_epsg=output_epsg)
    logging.info("Done. → %s", out_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="LAS → GeoJSON damage scoring pipeline")
    parser.add_argument("--config", "-c", required=True, help="YAML config path")
    parser.add_argument(
        "--step",
        choices=["preprocess", "registration", "difference", "all"],
        default="all",
        help="Run a specific step only",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        cfg = load_config(args.config)
    except Exception as e:  # noqa: BLE001
        logging.error("Config error: %s", e)
        return 2

    try:
        run(cfg, only_step=None if args.step == "all" else args.step)
    except Exception as e:  # noqa: BLE001
        logging.exception("Pipeline failed: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
