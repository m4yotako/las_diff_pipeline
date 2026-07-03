# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## よく使うコマンド

### ローカル実行（CLI）

```bash
# 仮想環境のセットアップ
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# パイプライン全体を実行
python main.py --config config.yaml

# 個別ステップのみ実行（preprocess / registration / difference）
python main.py --config config.yaml --step preprocess
python main.py --config config.yaml --step difference

# 詳細ログ付き
python main.py --config config.yaml -v
```

### Web サービス（Docker）

```bash
# 起動（初回は fiona ソースビルドで 5〜10 分かかる）
docker compose up --build

# ヘルスチェック
curl http://localhost:8000/health

# ワーカーを複数起動（並列処理）
docker compose up --scale worker=3

# 停止 / 完全削除
docker compose down
docker compose down -v   # job_data ボリュームも削除

# ワーカーのログ確認
docker compose logs -f worker

# ジョブのステータス確認
curl http://localhost:8000/jobs/<job_id>
```

### 合成データで動作確認

```bash
# テスト用 LAS ファイルを生成してパイプラインを通す
cd examples
python generate_synthetic.py
python main.py --config config_synthetic.yaml
```

## アーキテクチャ

### 全体構成

このリポジトリは **CLI パイプライン** と **Web サービス** の 2 つのエントリポイントを持つ。

```
CLI: python main.py --config config.yaml
                    ↓
Web: ブラウザ (viewer/index_osm.html)
     → POST /jobs (FastAPI: api/app.py)
     → Redis Queue
     → RQ ワーカー (api/worker_tasks.py)
     → main.run() を呼び出す ← どちらも同じパイプライン本体
```

**パイプラインの本体は `main.run()` のみ。** CLI も Web も最終的にこの関数を呼ぶ。

### パイプラインのデータフロー（7 ステップ）

| ステップ | 関数 | 入出力 |
|---|---|---|
| 1. Read LAS | `io_las.read_auto()` | LAS/LAZ/CSV → `PointCloud` |
| 2. Preprocess | `preprocess.preprocess()` | `PointCloud` → `PointCloud`（SOR・ダウンサンプル・CRS統一） |
| 3. Registration | `registration.icp_align()` | post を pre に位置合わせ。open3d がなければ scipy fallback |
| 4. Difference | `difference.dsm_diff()` | pre/post → `diff_raster (ndarray)` + bbox。DSM 差分が主方式 |
| 5. Scoring | `scoring.apply_significance()` | `dz` → `damage_score (0〜3)` |
| 6. Aggregation | `aggregation.aggregate_grid()` または `aggregate_buildings()` | 点群 → GeoDataFrame |
| 7. Export | `export.export_geojson()` | GeoDataFrame → WGS84 GeoJSON |

### 中心的なデータ構造

- **`PointCloud`** (`pipeline/io_las.py`): `xyz: np.ndarray (N, 3)`, `classification`, `intensity`, `crs_epsg` を持つ dataclass。パイプライン内をすべてこの型が流れる。
- **`Config`** (`pipeline/config.py`): YAML または dict から生成。`resolve_path()` で config ファイルからの相対パスを解決する。`config_from_dict()` は API 経由（YAML ファイルなし）で Config を生成するためのもの。

### Web サービスの仕組み

- `api/app.py`: FastAPI。`POST /jobs` でファイルを受け取り RQ に積む。config は **JSON 文字列の Form フィールド** として受け取る（HTTP の制約：multipart と JSON body は混在不可）。
- `api/worker_tasks.py`: RQ ワーカーから呼ばれる。`job.meta["step"]` に進捗文字列を書き込み `job.save_meta()` → ブラウザが 3 秒ポーリングで読む。
- Docker: `api` / `worker` / `redis:7-alpine` の 3 サービス。`job_data` 名前付きボリュームで LAS ファイルと GeoJSON を共有。

### 差分計算の制約

- **DSM 差分 (`method: "dsm"`) がデフォルトかつ唯一の動作実装。**
- M3C2 (`method: "m3c2"`) は `difference.py` にスタブがあるが `NotImplementedError` を送出する。PDAL 2.8.4 には `filters.m3c2` が存在しないため実装不可。
- ICP は open3d がなければ `_icp_scipy`（Umeyama 法）にフォールバックする。精度は落ちるが動作する。

### 設定ファイル

すべての挙動は YAML（または同構造の dict）で制御される。`config.example.yaml` が全キーのリファレンス。Web 経由では `io.pre_las` / `io.post_las` / `io.output_dir` は API が自動設定するため送信不要。

## 依存パッケージの注意点

- `open3d`: Docker・CI ではコメントアウト（Linux ホイール 447MB）。`registration.py` の scipy fallback で代替。
- `osmnx`: `aggregation.unit: building` かつ `source: osm` のときのみ必要。Docker ではデフォルトでコメントアウト。
- `fiona`: Apple Silicon (aarch64) では aarch64 バイナリホイールが存在せずソースビルドになる。`Dockerfile` に `libgdal-dev` が必要な理由はこれ。

## API エンドポイント

| メソッド | パス | 説明 |
|---|---|---|
| POST | `/jobs` | LAS アップロード + ジョブ投入（202） |
| GET | `/jobs/{id}` | ステータス・進捗取得（step フィールドで "3/7: Registration" のように返る） |
| GET | `/jobs/{id}/result` | 完了済み GeoJSON ダウンロード |
| GET | `/health` | API + Redis 死活確認 |

## 出力 GeoJSON のスキーマ

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon" },
  "properties": {
    "id": "g_00134",
    "damage_score": 2,
    "dz_mean": -1.85,
    "dz_p95": 3.12,
    "n_points_pre": 412,
    "n_points_post": 87,
    "loss_ratio": 0.79,
    "method": "dsm",
    "significant": true
  }
}
```

`damage_score` は 0（変化なし）〜 3（甚大）。閾値は `scoring.thresholds` で設定（デフォルト: 0.3 / 1.0 / 3.0 m）。
