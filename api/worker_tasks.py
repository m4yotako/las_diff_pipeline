"""RQ ワーカーから呼び出されるタスク関数。

このモジュールはワーカープロセスで import される。
FastAPI の app.py とは独立して動作する。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# las_diff_pipeline ルートを sys.path に追加（ワーカー起動ディレクトリによっては必要）
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from rq import get_current_job

from pipeline.config import config_from_dict
from main import run, setup_logging

logger = logging.getLogger(__name__)


def run_pipeline(job_id: str, cfg_dict: dict, jobs_base: str) -> str:
    """パイプラインを実行するRQタスク。

    Parameters
    ----------
    job_id:    アプリ側のジョブID（RQ の job.id と同一）
    cfg_dict:  API から受け取った設定 dict。
               io.pre_las / io.post_las / io.output_dir は API 側で絶対パスに設定済み。
    jobs_base: ジョブディレクトリの親ディレクトリ（例: /data/jobs）

    Returns
    -------
    出力 GeoJSON の絶対パス文字列
    """
    setup_logging(verbose=False)

    job_dir = Path(jobs_base) / job_id
    rq_job = get_current_job()  # ワーカー内でのみ有効（テスト時は None になる）

    def progress_cb(msg: str) -> None:
        logger.info("[job:%s] %s", job_id, msg)
        if rq_job is not None:
            rq_job.meta["step"] = msg
            rq_job.save_meta()  # Redis に書き込む（ポーリング側が読む）

    cfg = config_from_dict(cfg_dict, base_dir=job_dir)
    run(cfg, progress_cb=progress_cb)

    result_path = Path(cfg_dict["io"]["output_dir"]) / "damage_score.geojson"
    return str(result_path)
