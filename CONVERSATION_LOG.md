# 会話ログ：LAS差分パイプライン Web化プロジェクト

> 作成日: 2026-06-25  
> プロジェクト: `las_diff_pipeline/`  
> 目的: ローカルで動いていたPython点群処理パイプラインをWeb上で動かせるように改修する

---

## 目次

1. [プロジェクトの概要と目標](#1-プロジェクトの概要と目標)
2. [調査フェーズ：設計の前提確認](#2-調査フェーズ設計の前提確認)
3. [設計の方針決定](#3-設計の方針決定)
4. [実装：変更・追加ファイルの詳細](#4-実装変更追加ファイルの詳細)
5. [変更の理由と効果](#5-変更の理由と効果)
6. [動作確認とトラブルシュート](#6-動作確認とトラブルシュート)
7. [ユーザー操作手順（Web UI）](#7-ユーザー操作手順web-ui)
8. [UI改善案の提案](#8-ui改善案の提案)

---

## 1. プロジェクトの概要と目標

### 元のシステム

ローカルのPythonスクリプトとして動作するLAS点群被害スコアリングパイプライン。

```
入力: 被災前LASファイル + 被災後LASファイル + config.yaml
処理: 読み込み → 前処理 → 位置合わせ → DSM差分 → スコアリング → 集約
出力: 被害スコア GeoJSON（Leafletマップで可視化）
```

### 目標

- ブラウザだけで操作できるようにする（ターミナル・Python不要）
- 将来的にはサーバー上で動かし、ユーザーは結果を待つだけにする
- 抜本的なアーキテクチャ変更を許容する

---

## 2. 調査フェーズ：設計の前提確認

設計ハルシネーションを防ぐために、実装前にドキュメントを参照して以下を確認した。

### 発見1：PDAL 2.8.4 に `filters.m3c2` は存在しない

- PDALの公式ドキュメントを確認し、M3C2フィルターはPDAL 2.8.xに含まれていないことを確認
- 元コードの `difference.py` にあったM3C2スタブは動作しない
- **決定：デフォルト差分方式をDSM差分に変更。M3C2は将来対応とする**

### 発見2：open3d Linux ホイールは 447MB

- open3d の Linux用バイナリは約447MB
- Dockerビルドのたびにダウンロードが発生し非現実的
- `registration.py` に `_icp_scipy` フォールバックが既実装済みのため open3d なしで動作可能
- **決定：`requirements.txt` でコメントアウト**

### 発見3：FastAPI でファイル + JSON を同時送信できない

- HTTP プロトコルの制約により、`multipart/form-data`（ファイル）と `application/json`（ボディ）は同一リクエストに混在できない
- **決定：configはJSON文字列をFormフィールドとして送る** (`fd.append("config", JSON.stringify(...))`)
- FastAPI 側: `config: str = Form(default="{}")` → `json.loads(config)`

### 発見4：RQ のデフォルトタイムアウトは 180 秒

- RQ（Redis Queue）のジョブタイムアウトデフォルトは180秒
- LAS処理は数分かかる場合があり、デフォルトでは強制終了される
- **決定：`job_timeout="2h"` を明示的に設定**

---

## 3. 設計の方針決定

### アーキテクチャ

```
ブラウザ (viewer/index_osm.html)
    ↕ HTTP (multipart/form-data)
FastAPI サーバー (api/app.py) :8000
    ↕ Redis Queue (RQ)
RQ ワーカー (api/worker_tasks.py)
    ↓
パイプライン (pipeline/ + main.py)
    ↓
GeoJSON → ブラウザに返却
```

### サービス構成（Docker）

| サービス | 役割 |
|---|---|
| `api` | FastAPI。ファイル受け取り・ジョブ登録・結果返却 |
| `worker` | RQワーカー。パイプラインの実際の処理 |
| `redis` | ジョブキューとメタデータのストレージ |

### APIエンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/jobs` | LASファイルをアップロードしてジョブ投入（202返却） |
| GET | `/jobs/{id}` | ジョブのステータスと進捗取得 |
| GET | `/jobs/{id}/result` | 完了済みジョブのGeoJSONダウンロード |
| GET | `/health` | サーバーとRedisの死活確認 |

---

## 4. 実装：変更・追加ファイルの詳細

### 変更したファイル（4件）

#### `pipeline/config.py`

末尾に `config_from_dict()` 関数を追加。

```python
def config_from_dict(raw: dict, base_dir: str | Path) -> Config:
    """辞書（APIから受け取ったJSON）から Config を生成する。"""
    if not isinstance(raw, dict):
        raise ValueError("config must be a dict")
    return Config(raw=raw, config_path=Path(base_dir) / "_api_config.yaml")
```

#### `main.py`

- `progress_cb: Callable[[str], None] | None = None` 引数を追加
- 全ステップのログを `_step("1/7: Read LAS")` 形式に統一
- デフォルト差分方式を `"m3c2"` → **`"dsm"`** に変更（99行目）

```python
def run(cfg: Config, only_step=None, progress_cb=None):
    def _step(msg):
        logging.info("=== %s ===", msg)
        if progress_cb is not None:
            progress_cb(msg)
    # ...
    method = diff_cfg.get("method", "dsm")  # デフォルトを dsm に変更
```

#### `requirements.txt`

```
# 追加（Webサービス化）
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
python-multipart>=0.0.9    # FastAPI UploadFile に必須
rq>=2.0                    # ジョブキュー
redis>=5.0.0               # RQ 対応バージョン

# コメントアウト
# open3d>=0.19              ← Linux 447MB のためオフ
# osmnx>=1.9               ← ビルド時間削減のためオフ（建物集約時のみ必要）
```

#### `viewer/index_osm.html`

3画面UIに全面改修（既存の地図機能は全て保持）。

- **画面1（アップロード）**: LASファイル選択・config設定フォーム
- **画面2（進捗）**: プログレスバー・ステップテキスト（3秒ポーリング）
- **画面3（地図）**: 既存の全機能（フィルター・凡例・不透明度スライダー等）

```javascript
// ステップ文字列 → プログレスバー%
const STEP_PROGRESS = {
  "1/7": 14, "2/7": 28, "3/7": 43,
  "4/7": 57, "5/7": 71, "6/7": 86, "7/7": 100
};

// 3秒ごとにジョブステータスをポーリング
pollTimer = setInterval(async () => {
  const r = await fetch(`${API_URL}/jobs/${jobId}`);
  const data = await r.json();
  if (data.status === "finished") { /* 地図画面へ */ }
  if (data.status === "failed")   { /* エラー表示 */ }
}, 3000);
```

---

### 新規追加したファイル（5件）

#### `api/__init__.py`

空のパッケージ初期化ファイル。

#### `api/app.py`

FastAPIアプリ本体。主要な設定：

```python
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
JOBS_BASE = Path(os.getenv("JOBS_BASE", "/data/jobs"))

# ジョブ投入
rq_job = q.enqueue(
    run_pipeline,
    job_id, cfg_dict, str(JOBS_BASE),
    job_id=job_id,
    job_timeout="2h",      # デフォルト180秒では足りないため必須
    result_ttl=86400,      # 1日間結果を保持
    failure_ttl=86400,
)
```

#### `api/worker_tasks.py`

RQワーカータスク。進捗をRedisに書き込む。

```python
def run_pipeline(job_id: str, cfg_dict: dict, jobs_base: str) -> str:
    rq_job = get_current_job()

    def progress_cb(msg: str) -> None:
        if rq_job is not None:
            rq_job.meta["step"] = msg
            rq_job.save_meta()  # ブラウザのポーリングが読む

    cfg = config_from_dict(cfg_dict, base_dir=job_dir)
    run(cfg, progress_cb=progress_cb)
    return str(result_path)
```

#### `Dockerfile`

```dockerfile
FROM python:3.11-slim

# Apple Silicon (aarch64) では fiona がソースビルドになるため libgdal-dev が必要
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libgdal-dev gdal-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### `docker-compose.yml`

```yaml
services:
  api:
    build: .
    ports: ["8000:8000"]
    volumes: [job_data:/data/jobs]
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      redis: {condition: service_healthy}

  worker:
    build: .
    command: python -m rq worker --url redis://redis:6379 --with-scheduler
    volumes: [job_data:/data/jobs]
    depends_on:
      redis: {condition: service_healthy}

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]

volumes:
  job_data:  # api と worker がジョブファイルを共有する名前付きボリューム
```

### 削除したファイル

**なし。** 既存のパイプラインコード（`pipeline/` 以下全ファイル）は破壊的変更なし。

---

## 5. 変更の理由と効果

### `config_from_dict()` を追加した理由

元の `Config` はYAMLファイルパスを受け取る設計だった。APIはJSONを受け取るため、YAMLを経由せず直接Configを生成できる関数が必要だった。

### `progress_cb` を追加した理由

ログは `logging.info()` でコンソールに流れるだけで、ブラウザ側はパイプラインの進捗を知る術がなかった。コールバックを介してRedisに進捗を書き込み、ブラウザのポーリングで読み取れるようにした。`None` の場合は従来通りCLI動作するため後方互換性は保たれる。

### デフォルトを `dsm` に変更した理由

PDAL 2.8.4に `filters.m3c2` が存在しないことをドキュメントで確認。元の `"m3c2"` デフォルトのままではジョブが必ずエラーになるため変更した。

### RQワーカーを別サービスに分離した理由

LAS処理は数分かかる重い処理。APIサーバーがリクエストをブロックして処理するとHTTPタイムアウトが発生し、その間他のリクエストを受け付けられない。RQで非同期化することで、APIは即座にジョブIDを返し、重い処理はワーカーが担う。ワーカーを `docker compose up --scale worker=3` で増やすだけで並列処理できる拡張性も得られた。

---

## 6. 動作確認とトラブルシュート

### 起動手順

```bash
cd ~/Downloads/projects-main/gensai/las_diff_pipeline
docker compose up --build
```

### ヘルスチェック

```bash
curl http://localhost:8000/health
# 期待値: {"api":"ok","redis":"ok"}
```

### Swagger UI

`http://localhost:8000/docs` でAPIを手動テスト可能。

### 発生したエラーと解決策

#### エラー1: Docker Daemonが起動していない

```
Cannot connect to the Docker daemon at unix:///Users/.../.docker/run/docker.sock
```

**原因**: Docker Desktopアプリが起動していない  
**対処**: LaunchpadからDocker Desktopを開き、メニューバーのクジラアイコンが「running」になるまで待つ

#### エラー2: `docker-compose.yml` の `version` 警告

```
WARN: the attribute `version` is obsolete
```

**対処**: `docker-compose.yml` の先頭行 `version: "3.9"` を削除（実施済み）

#### エラー3: fionaのビルドエラー（Apple Silicon）

```
CRITICAL: A GDAL API version must be specified.
Failed to get options via gdal-config: No such file or directory
```

**原因**: Apple Silicon (aarch64) 環境では fiona 1.10.x のaarch64バイナリホイールが存在せず、ソースコンパイルが必要。コンパイルには `gdal-config` が必要だが、初期のDockerfileでは未インストールだった。

**対処**: `Dockerfile` に `libgdal-dev` と `gdal-bin` を追加

```dockerfile
RUN apt-get install -y build-essential libgdal-dev gdal-bin
```

### ログ確認コマンド

```bash
# ワーカーのログ
docker compose logs -f worker

# 特定ジョブのステータス確認
curl http://localhost:8000/jobs/<job_id>

# 停止
docker compose down

# ボリュームも含めて完全削除
docker compose down -v
```

---

## 7. ユーザー操作手順（Web UI）

### 準備

Docker起動中の状態で：

```bash
open ~/Downloads/projects-main/gensai/las_diff_pipeline/viewer/index_osm.html
```

### Step 1: ファイルと設定を入力

| 項目 | 説明 |
|---|---|
| Pre LAS | 被災前のLAS/LAZファイル |
| Post LAS | 被災後のLAS/LAZファイル |
| EPSG | 座標系コード（例: `6677` = 平面直角9系） |
| 差分方式 | `dsm`（デフォルト） |
| 解像度(m) | DSMグリッドサイズ（デフォルト `0.5`） |
| 集約単位 | `grid`（グリッド）または `building`（建物単位） |
| API URL | `http://localhost:8000`（ローカル動作時は変更不要） |

「ジョブを投入」ボタンをクリック。

### Step 2: 処理を待つ

進捗画面でプログレスバーが進む。

```
1/7: Read LAS → 2/7: Preprocess → 3/7: Register → 4/7: DSM Diff
→ 5/7: Scoring → 6/7: Aggregate → 7/7: Export GeoJSON
```

完了すると自動で地図画面に移動。

### Step 3: 地図で結果確認

- フィルターボタン: 被害レベルで絞り込み
- 不透明度スライダー: 重ね合わせ調整
- セルクリック: スコア詳細表示
- 「新しいジョブを投入」ボタン: Step 1に戻る

---

## 8. UI改善案の提案

現行UIの課題：configの意味がわかりにくい・ジョブ投入後のフィードバックが薄い。

### 案A: ウィザード形式（ステップ分割 + リアルタイムJSONプレビュー）

入力を「ファイル → 設定 → 確認&投入」の3ステップに分割。設定フォームの右側に、実際に送信されるconfig JSONをリアルタイム表示することで、何が設定されているか一目でわかる。

**向いているケース**: 初回ユーザー・設定項目を理解しながら進めたいケース

### 案B: 左右分割パネル（プリセット選択 + ステップ進捗ダッシュボード）

左パネルでプリセット（標準・建物単位・高精度）を選択し、右パネルでジョブの7ステップ進捗をリスト形式で表示。今どのステップで処理中かがひと目でわかる。

**向いているケース**: リピートユーザー・設定の手間を省きたいケース

### 案C: チャット風インターフェース（Q&A形式 + 結果サマリーカード）

「座標系は6677でよいですか？」のように選択肢を提示しながら設定を収集。完了後は「深刻な被害: 87セル / 中程度: 213セル」のようにサマリーカードで結果を表示。技術知識がないユーザーでも操作できる。

**向いているケース**: 非技術職のユーザー・専門用語を見せたくないケース

---

## ファイル変更サマリー

| ファイル | 種別 | 内容 |
|---|---|---|
| `pipeline/config.py` | 変更 | `config_from_dict()` 追加 |
| `main.py` | 変更 | `progress_cb` 引数追加、デフォルト差分方式を `dsm` に変更 |
| `requirements.txt` | 変更 | Webパッケージ追加、open3d/osmnxをコメントアウト |
| `viewer/index_osm.html` | 変更 | 3画面UI（アップロード/進捗/地図）に改修 |
| `api/__init__.py` | 新規 | パッケージ初期化 |
| `api/app.py` | 新規 | FastAPI本体（4エンドポイント） |
| `api/worker_tasks.py` | 新規 | RQワーカータスク |
| `Dockerfile` | 新規 | Ubuntu/python:3.11-slim + GDALビルド環境 |
| `docker-compose.yml` | 新規 | api/worker/redisの3サービス構成 |
| `DESIGN.md` | 新規 | 設計ドキュメント（調査結果・API仕様） |

削除したファイル: **なし**
