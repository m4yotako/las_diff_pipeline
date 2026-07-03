# Webサービス化 設計書

> 調査日: 2026-06-23  
> 調査対象: FastAPI公式ドキュメント, RQ公式ドキュメント, PyPI(open3d 0.19.0), PDAL 2.8.4ドキュメント  
> ハルシネーション排除のため、すべての技術的主張はドキュメント参照で裏付け済み

---

## 1. 調査で判明した重要事実（実装前に必ず確認）

### ⚠️ 重大発見 1: `pdal filters.m3c2` は存在しない

PDAL 2.8.4 の全フィルター一覧を確認した結果、**`filters.m3c2` は存在しない**。
現在の `difference.py` の M3C2 スタブが呼び出している `pdal.Pipeline(...)` は、
どのバージョンの PDAL でも動作しない可能性が高い。

**選択肢（実装時に決定が必要）:**
- A) DSM差分のみで進める（現状のコードで動く）
- B) `py4dgeo` ライブラリを使う（M3C2の純Python実装がある）
- C) PDAL の `filters.icp` を使って差分を自作する

### ⚠️ 重大発見 2: open3d Linux ホイールは 447.7 MB

`open3d-0.19.0-...-manylinux_2_31_x86_64.whl` のサイズは **447.7 MB**。
Dockerイメージが 1GB を超える原因になる。

**選択肢:**
- A) `registration.py` の `_icp_scipy` フォールバックを使い、open3d を依存から外す
  - `_icp_scipy` は既にコードに存在し、open3d なしで動作する
  - open3d なしなら Docker イメージが大幅に軽量化
- B) open3d を使い続ける（精度は上がるが Docker が重い）

### ⚠️ 重大発見 3: FastAPI でファイル+設定を同時送信できない

HTTP プロトコルの仕様上、**multipart/form-data と JSON body を同一リクエストに混在できない**。
ファイルアップロードのエンドポイントでは、設定(config)も Form フィールドとして送る必要がある。

```python
# ❌ これはできない
@app.post("/jobs")
async def create_job(pre: UploadFile, post: UploadFile, config: ConfigModel):
    ...

# ✅ こうする
@app.post("/jobs")
async def create_job(
    pre: UploadFile,
    post: UploadFile,
    config: str = Form(...),  # JSON文字列として受け取る
):
    config_dict = json.loads(config)
    ...
```

### ⚠️ 重大発見 4: RQ のデフォルトタイムアウトは 180 秒

LAS ファイルの処理は 180 秒を超える可能性がある。
**`job_timeout` を明示的に設定しなければジョブが強制終了される。**

```python
q.enqueue(run_pipeline, cfg_dict, job_dir, job_timeout='2h', result_ttl=86400)
#                                                              ^^^^^^^^^^^
#                       デフォルト 500 秒では結果がすぐ消える。1日以上に設定する。
```

### ⚠️ 重大発見 5: RQ Workers は Windows では動作しない

RQ は `fork()` に依存するため、**Linux / macOS のみ**で動作する。
本番サーバーが Linux なら問題なし。開発環境が Windows の場合は WSL2 が必要。

---

## 2. 最終アーキテクチャ

```
ブラウザ
  │  POST /jobs (multipart: pre.las, post.las, config=JSON文字列)
  ▼
FastAPI (api/app.py)
  │  1. ファイルを jobs/{job_id}/ に保存
  │  2. q.enqueue(run_pipeline, job_id, cfg_dict, job_timeout='2h', result_ttl=86400)
  │  3. { job_id } を返す
  │
  │  GET /jobs/{job_id}
  │    └ Job.fetch(job_id).get_status() + job.meta["step"] を返す
  │
  │  GET /jobs/{job_id}/result
  │    └ jobs/{job_id}/damage_score.geojson をそのまま返す
  ▼
Redis (ジョブキュー)
  ▼
RQ Worker (api/worker.py が起動)
  │  run_pipeline(job_id, cfg_dict) を実行
  │  └ 既存の main.run(cfg) をほぼそのまま呼ぶ
  │    途中で job.meta["step"] = "3/7: Registration" など更新
  ▼
ローカルストレージ (jobs/{job_id}/)
  ├── pre.las
  ├── post.las
  ├── config.yaml
  └── outputs/damage_score.geojson  ← ビューアが取得
```

---

## 3. ディレクトリ構成（変更後）

```
las_diff_pipeline/
├── api/                          # 新規
│   ├── app.py                    # FastAPI アプリ
│   ├── worker_tasks.py           # RQ から呼ばれる関数
│   └── storage.py                # ファイル保存先の抽象化
├── pipeline/                     # 既存（変更少）
│   ├── config.py                 # MOD: from_dict() 追加
│   ├── io_las.py                 # 変更なし（ローカルパスのみ対応で十分）
│   ├── preprocess.py             # 変更なし
│   ├── registration.py           # 変更なし
│   ├── difference.py             # MOD: M3C2スタブのコメント整理
│   ├── scoring.py                # 変更なし
│   ├── aggregation.py            # 変更なし
│   └── export.py                 # 変更なし
├── viewer/
│   └── index_osm.html            # MOD: APIポーリングUI追加
├── main.py                       # MOD: progress_callback 引数追加
├── requirements.txt              # MOD: fastapi, uvicorn, rq, redis 追加
├── Dockerfile                    # 新規
├── docker-compose.yml            # 新規
└── config.example.yaml           # 変更なし
```

---

## 4. API仕様

### POST /jobs — ジョブ投入

**リクエスト:** `multipart/form-data`

| フィールド | 型 | 説明 |
|---|---|---|
| `pre` | UploadFile | 被災前の LAS/LAZ/CSV |
| `post` | UploadFile | 被災後の LAS/LAZ/CSV |
| `config` | string (Form) | YAML または JSON 文字列。省略時はデフォルト設定を使用 |

**レスポンス:** `202 Accepted`
```json
{ "job_id": "abc123def456" }
```

**実装上の注意:**
- `python-multipart` が必要 (`pip install python-multipart`)
- UploadFile のサイズ制限はデフォルトで無制限。nginx 等でサーバー側に設定する

---

### GET /jobs/{job_id} — ステータス確認

**レスポンス例:**
```json
{
  "status": "started",
  "step": "3/7: Registration",
  "enqueued_at": "2026-06-23T10:00:00",
  "started_at": "2026-06-23T10:00:05"
}
```

**statusの値（RQドキュメントより正確に列挙）:**
- `queued` — キュー待ち
- `started` — 実行中
- `finished` — 完了
- `failed` — 失敗
- `deferred` — 依存ジョブ待ち
- `scheduled` — 予約済み
- `stopped` — 停止
- `canceled` — キャンセル済み

---

### GET /jobs/{job_id}/result — 結果取得

**レスポンス:** GeoJSON ファイル (`Content-Type: application/geo+json`)

ステータスが `finished` でない場合は `404` または `202` を返す。

---

## 5. 各ファイルの具体的な変更内容

### 5.1 `pipeline/config.py` — 変更小

`from_dict()` クラスメソッドを追加するだけ。既存コードは無変更。

```python
@classmethod
def from_dict(cls, raw: dict, base_dir: Path) -> "Config":
    """辞書（APIから受け取ったJSON/YAML）から Config を生成."""
    return cls(raw=raw, config_path=base_dir / "_api_config.yaml")
```

---

### 5.2 `main.py` — 変更小

`run()` に `progress_cb` 引数を追加。None のときは無視するので後方互換性あり。

```python
def run(cfg: Config, only_step: str | None = None,
        progress_cb: Callable[[str], None] | None = None) -> None:
    def _progress(msg: str):
        if progress_cb:
            progress_cb(msg)
        logging.info(msg)

    _progress("=== Step 1: Read LAS ===")
    # ... 各ステップの冒頭で _progress() を呼ぶ
```

---

### 5.3 `api/worker_tasks.py` — 新規（主要ロジック）

```python
from rq import get_current_job
from pathlib import Path
from pipeline.config import Config
from main import run

def run_pipeline(job_id: str, cfg_dict: dict, jobs_base: str) -> str:
    """RQ ワーカーから呼ばれる関数."""
    job = get_current_job()  # job.meta で進捗更新するため
    job_dir = Path(jobs_base) / job_id

    def progress_cb(msg: str):
        job.meta["step"] = msg
        job.save_meta()  # Redis に書く（ポーリング側が読む）

    cfg = Config.from_dict(cfg_dict, base_dir=job_dir)
    run(cfg, progress_cb=progress_cb)
    return str(job_dir / "outputs" / "damage_score.geojson")
```

**`get_current_job()` について:**
- RQ ドキュメントで確認済み。ワーカー内でのみ有効（それ以外では None を返す）
- `job.save_meta()` を呼ばないと Redis に書き込まれない（重要）

---

### 5.4 `api/app.py` — 新規

```python
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from redis import Redis
from rq import Queue
from rq.job import Job
import uuid, json, shutil
from pathlib import Path
from .worker_tasks import run_pipeline

app = FastAPI()
redis_conn = Redis(host="redis", port=6379)  # docker-compose のサービス名
q = Queue(connection=redis_conn)

JOBS_BASE = Path("/data/jobs")
JOBS_BASE.mkdir(parents=True, exist_ok=True)

@app.post("/jobs", status_code=202)
async def create_job(
    pre: UploadFile = File(...),
    post: UploadFile = File(...),
    config: str = Form(default="{}"),  # JSON文字列。空なら空dictを受け取る
):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_BASE / job_id
    job_dir.mkdir()

    # ファイル保存（awaitが必要）
    pre_path = job_dir / "pre.las"
    post_path = job_dir / "post.las"
    with open(pre_path, "wb") as f:
        shutil.copyfileobj(pre.file, f)
    with open(post_path, "wb") as f:
        shutil.copyfileobj(post.file, f)

    # config を dict に変換してキューに積む
    try:
        cfg_dict = json.loads(config) if config else {}
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"config JSON parse error: {e}")

    # io フィールドをジョブディレクトリのパスに上書き
    cfg_dict.setdefault("io", {})
    cfg_dict["io"]["pre_las"] = str(pre_path)
    cfg_dict["io"]["post_las"] = str(post_path)
    cfg_dict["io"]["output_dir"] = str(job_dir / "outputs")

    # キューに積む（job_timeout と result_ttl を明示）
    rq_job = q.enqueue(
        run_pipeline,
        job_id, cfg_dict, str(JOBS_BASE),
        job_id=job_id,          # RQ の job_id = アプリの job_id に統一
        job_timeout="2h",       # デフォルト180秒を上書き（必須）
        result_ttl=86400,       # 1日間結果を保持（デフォルト500秒は短すぎる）
        failure_ttl=86400,
    )
    return {"job_id": rq_job.id}


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(404, "job not found")
    return {
        "status": job.get_status().value,  # .value で文字列化（enumのため）
        "step": job.meta.get("step", ""),
        "enqueued_at": job.enqueued_at,
        "started_at": job.started_at,
        "ended_at": job.ended_at,
    }


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        raise HTTPException(404, "job not found")
    if job.get_status().value != "finished":
        raise HTTPException(202, "not finished yet")
    result_path = JOBS_BASE / job_id / "outputs" / "damage_score.geojson"
    if not result_path.exists():
        raise HTTPException(404, "result file not found")
    return FileResponse(str(result_path), media_type="application/geo+json")
```

---

### 5.5 `Dockerfile` — 新規

open3d をどう扱うかによって大きく変わる。

**オプション A（open3d なし・推奨・軽量）:**

`registration.py` の `_icp_scipy` フォールバックは open3d がなくても動作する。
config で `registration.enabled: false` にすれば ICP ごとスキップも可能。

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 地理空間ライブラリに必要なシステムパッケージ
RUN apt-get update && apt-get install -y \
    libgeos-dev libproj-dev libgdal-dev gdal-bin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**オプション B（open3d あり・重い）:**

`manylinux_2_31_x86_64` ホイールが要求する glibc >= 2.31 は
Ubuntu 22.04 (glibc 2.35) で満たされる。Ubuntu 20.04 (glibc 2.31) でも可。

```dockerfile
FROM ubuntu:22.04

RUN apt-get update && apt-get install -y python3.11 python3-pip \
    libgeos-dev libproj-dev libgdal-dev gdal-bin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
# open3d のインストールで数分かかる（447MB）

COPY . .
CMD ["python3", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 5.6 `docker-compose.yml` — 新規

```yaml
version: "3.9"
services:
  api:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - job_data:/data/jobs
    depends_on:
      - redis
    environment:
      - REDIS_URL=redis://redis:6379

  worker:
    build: .
    command: rq worker --url redis://redis:6379
    volumes:
      - job_data:/data/jobs
    depends_on:
      - redis
    # ワーカーを複数起動する場合は replicas: N（Docker Swarm）または scale で

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

volumes:
  job_data:
```

**`rq worker` コマンドについて:**
- RQ ドキュメントで確認済みのコマンド
- `--url` でRedis接続先を指定（docker-compose のサービス名 `redis` を使う）

---

### 5.7 `requirements.txt` — 追加分

```
# 既存はそのまま。以下を追加:
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
python-multipart>=0.0.9   # FastAPIでUploadFileを使うために必須
rq>=2.0
redis>=5.0.0              # RQの対応バージョン: Redis >= 5
```

---

### 5.8 `viewer/index_osm.html` — 改修方針

現状: ローカルファイル選択で GeoJSON を読む  
変更後: ジョブ投入 → ポーリング → 結果取得 の3段階UIに変更

```
[画面1] アップロード
  - pre.las ファイル選択
  - post.las ファイル選択
  - 設定（EPSGコード、差分方法、etc）
  - 実行ボタン → POST /jobs → job_id を LocalStorage に保存

[画面2] 進捗表示（自動ポーリング）
  - 3秒ごとに GET /jobs/{job_id} を呼ぶ
  - job.step を表示（例: "3/7: Registration"）
  - status=finished になったら画面3へ

[画面3] 地図表示（既存UIをほぼ流用）
  - GET /jobs/{job_id}/result でGeoJSONを取得
  - Leaflet の dataLayer に読み込む
```

---

## 6. CORS設定（必要な場合）

ビューアと API が異なるオリジンで動く場合（例: ビューアを GitHub Pages に置く）は
FastAPI の CORS ミドルウェアが必要。

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番では具体的なオリジンを指定
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

---

## 7. 未解決事項（実装前に決定が必要）

| 項目 | 選択肢 | 推奨 |
|------|--------|------|
| open3d をDockerに含めるか | 含める(精度↑・重い) / 除外(scipy fallback) | scipy fallbackで軽量化 |
| M3C2 の実装 | DSM固定 / py4dgeo 導入 / 将来実装 | 当面DSM固定 |
| ファイル保存先 | ローカルdisk(簡単) / S3(スケール可) | まずローカルdisk |
| 認証 | なし / APIキー | まずなし |
| ファイルサイズ上限 | nginx/uvicorn で設定 | 実データサイズに応じて |
| 処理タイムアウト | 2h(現在案) | LASファイルサイズ次第 |

---

## 8. 実装順序（推奨）

1. `pipeline/config.py` に `from_dict()` 追加（テスト済み小変更）
2. `main.py` に `progress_cb` 引数追加
3. `api/worker_tasks.py` 作成 → ローカルで `rq worker` を起動してテスト
4. `api/app.py` 作成 → `uvicorn api.app:app` でローカル起動してテスト
5. `docker-compose.yml` + `Dockerfile` でコンテナ化
6. `viewer/index_osm.html` を改修（ポーリングUI追加）
