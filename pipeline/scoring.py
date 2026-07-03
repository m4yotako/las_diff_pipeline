"""Damage scoring."""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def score_by_thresholds(values, thresholds):
    """|values| を thresholds でビン分けして 0..len(thresholds) に変換."""
    abs_v = np.abs(values)
    score = np.zeros_like(abs_v, dtype=np.int8)
    for t in thresholds:
        score = score + (abs_v >= t).astype(np.int8)
    return score


def apply_significance(dz, lod95, score):
    """M3C2 LoD95 で非有意点のスコアを 0 にする."""
    if lod95 is None:
        sig = np.ones_like(dz, dtype=bool)
        return score, sig
    sig = np.abs(dz) > lod95
    score_adj = np.where(sig, score, 0).astype(np.int8)
    logger.info("Significance: %d / %d (%.1f%%) significant",
                int(sig.sum()), len(sig), 100.0 * sig.sum() / len(sig))
    return score_adj, sig


def building_score_from_stats(stats, rules):
    """建物単位の集計値から 0..3 のスコアを決定.

    判定順:
      1) 完全消失 (n_pre>=min_pre_points かつ n_post==0) -> 3
      2) |dz_mean| を thresholds でビン分け (fallback |dz_p95|)
      3) loss_ratio>=loss_ratio_collapse かつ |dz_mean|>=loss_ratio_min_dz かつ
         十分な n_pre -> 3 に底上げ

    DSM mode で pre(生 LAS)/post(DSM ピクセル中心)の点数の意味が異なるため
    loss_ratio_min_dz による暴走防止ガードを設けている.
    """
    dz_mean = stats.get("dz_mean")
    dz_p95 = stats.get("dz_p95")
    loss_ratio = float(stats.get("loss_ratio", 0.0) or 0.0)
    n_pre = int(stats.get("n_points_pre", 0) or 0)
    n_post = int(stats.get("n_points_post", 0) or 0)

    thresholds = rules.get("thresholds", [0.3, 1.0, 3.0])
    min_pre = int(rules.get("min_pre_points", 30))
    collapse_th = float(rules.get("loss_ratio_collapse", 0.6))
    loss_min_dz = float(rules.get("loss_ratio_min_dz", 1.5))

    if n_pre >= min_pre and n_post == 0:
        return 3

    if dz_mean is not None and np.isfinite(dz_mean):
        base = int(score_by_thresholds(np.array([dz_mean]), thresholds)[0])
    elif dz_p95 is not None and np.isfinite(dz_p95):
        base = int(score_by_thresholds(np.array([dz_p95]), thresholds)[0])
    else:
        base = 0

    if (loss_ratio >= collapse_th and n_pre >= min_pre
            and dz_mean is not None and np.isfinite(dz_mean)
            and abs(dz_mean) >= loss_min_dz):
        base = max(base, 3)

    return int(min(base, 3))
