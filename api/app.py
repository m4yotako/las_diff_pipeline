"""FastAPI アプリケーション。

起動方法:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload

または docker-compose up で起動する。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from redis import Redis
from rq import Queue
from rq.job import Job, JobStatus

from .worker_tasks import run_pipeline

# ─── 設定 ───────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JOBS_BASE = Path(os.getenv("JOBS_BASE", "/data/jobs"))
JOBS_BASE.mkdir(parents=True, exist_ok=True)

redis_conn = Redis.from_url(REDIS_URL)
q = Queue(connection=redis_conn)

# ─── アプリ ─────────────────────────────────────────────────
app = FastAPI(
    title="LAS 差分パイプライン API",
    description="LAS/LAZ ファイルをアップロードして被害スコア GeoJSON を生成する",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 本番環境では具体的なオリジンに絞ること
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ─── エンドポイント ──────────────────────────────────────────

def _job_status(job: Job) -> str:
    """job.get_status() は JobStatus(str, Enum) を返すが、__str__ は上書きされておらず
    str() では 'JobStatus.FINISHED' のような形式になってしまう（rq 2.x で確認）。
    ドキュメント上の値（'finished' 等）と一致させるため .value を使う。
    """
    status = job.get_status()
    if isinstance(status, JobStatus):
        return status.value
    return str(status)


async def _save_uploads(files: list[UploadFile], job_dir: Path, prefix: str) -> list[str]:
    """アップロードされたファイル群をジョブディレクトリに保存する。

    1MB チャンクで読み書き → 大きな LAS でもメモリを使いすぎない。
    複数ファイル（タイル分割）の場合は連番を振って区別する。
    """
    multi = len(files) > 1
    paths: list[str] = []
    for i, upload in enumerate(files):
        suffix = Path(upload.filename or f"{prefix}.las").suffix or ".las"
        dest = job_dir / (f"{prefix}{i}{suffix}" if multi else f"{prefix}{suffix}")
        with open(dest, "wb") as f:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        paths.append(str(dest))
    return paths


@app.post("/jobs", status_code=202, summary="ジョブを投入する")
async def create_job(
    pre: list[UploadFile] = File(..., description="被災前の LAS/LAZ/CSV ファイル（複数選択可。タイル分割データは結合して1現場として扱う）"),
    post: list[UploadFile] = File(..., description="被災後の LAS/LAZ/CSV ファイル（複数選択可。タイル分割データは結合して1現場として扱う）"),
    config: str = Form(
        default="{}",
        description=(
            "パイプライン設定の JSON 文字列。"
            "省略時はデフォルト設定（DSM差分, グリッド集約）を使用。"
            "io.pre_las / io.post_las / io.output_dir は自動設定されるため不要。"
        ),
    ),
):
    """
    LAS ファイルをアップロードしてパイプラインジョブを投入する。

    レスポンス: `{ "job_id": "..." }`

    config の例（最低限の設定）:
    ```json
    {
      "crs": { "input_epsg": 6677 },
      "difference": { "method": "dsm" }
    }
    ```
    """
    # config を dict に変換
    try:
        cfg_dict: dict = json.loads(config) if config.strip() else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"config JSON parse error: {exc}") from exc

    if not isinstance(cfg_dict, dict):
        raise HTTPException(status_code=400, detail="config must be a JSON object")

    if not pre or not post:
        raise HTTPException(status_code=400, detail="pre and post each require at least one file")

    # ジョブディレクトリを作成
    job_id = str(uuid.uuid4())
    job_dir = JOBS_BASE / job_id
    job_dir.mkdir(parents=True)

    # ファイルを保存。複数ファイル（タイル分割）の場合はリストのまま Config に渡す。
    pre_paths = await _save_uploads(pre, job_dir, "pre")
    post_paths = await _save_uploads(post, job_dir, "post")

    # io フィールドをジョブディレクトリの絶対パスに上書き
    cfg_dict.setdefault("io", {})
    cfg_dict["io"]["pre_las"] = pre_paths if len(pre_paths) > 1 else pre_paths[0]
    cfg_dict["io"]["post_las"] = post_paths if len(post_paths) > 1 else post_paths[0]
    cfg_dict["io"]["output_dir"] = str(job_dir / "outputs")

    # デフォルト設定の補完（ユーザーが省略した場合）
    cfg_dict.setdefault("difference", {})
    cfg_dict["difference"].setdefault("method", "dsm")

    # RQ キューに積む
    # job_timeout='2h': デフォルト 180 秒だと LAS 処理で強制終了されるため必須
    # result_ttl=86400: デフォルト 500 秒では GeoJSON がすぐ消えるため 1 日に設定
    rq_job = q.enqueue(
        run_pipeline,
        job_id,
        cfg_dict,
        str(JOBS_BASE),
        job_id=job_id,
        job_timeout="2h",
        result_ttl=86400,
        failure_ttl=86400,
    )

    return {"job_id": rq_job.id}


@app.get("/jobs/{job_id}", summary="ジョブのステータスを取得する")
def get_job_status(job_id: str):
    """
    ジョブのステータスと進捗を返す。

    status の値:
    - `queued`    : キュー待ち
    - `started`   : 実行中
    - `finished`  : 完了（結果を取得可能）
    - `failed`    : 失敗
    - `canceled`  : キャンセル済み
    """
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")

    status = _job_status(job)

    return {
        "status": status,
        "step": job.meta.get("step", ""),
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
        "error": (
            job.latest_result().exc_string
            if status == "failed" and job.latest_result() is not None
            else None
        ),
    }


@app.get("/jobs/{job_id}/result", summary="結果 GeoJSON を取得する")
def get_result(job_id: str):
    """
    完了済みジョブの被害スコア GeoJSON を返す。

    ジョブが未完了の場合は 202、ファイルが見つからない場合は 404 を返す。
    """
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(status_code=404, detail="job not found")

    status = _job_status(job)
    if status != "finished":
        raise HTTPException(status_code=202, detail=f"job is not finished yet (status: {status})")

    # worker_tasks.run_pipeline() が io.output_dir に出力する
    result_path = JOBS_BASE / job_id / "outputs" / "damage_score.geojson"
    if not result_path.exists():
        raise HTTPException(status_code=404, detail="result file not found on disk")

    return FileResponse(
        str(result_path),
        media_type="application/geo+json",
        filename=f"damage_score_{job_id[:8]}.geojson",
    )


@app.get("/health", summary="ヘルスチェック")
def health():
    """サーバーと Redis の接続確認。"""
    try:
        redis_conn.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"api": "ok", "redis": "ok" if redis_ok else "error"}
