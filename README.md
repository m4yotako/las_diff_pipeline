# LAS → GeoJSON 被害スコアリング パイプライン（雛形）

被災前後の点群（LAS / LAZ / CSV）を比較し、被害スコア付きの GeoJSON（WGS84）を出力するための Python パイプラインの雛形です。
出力 GeoJSON はそのまま Google Maps JavaScript API の `google.maps.Data` レイヤーに読み込めます。

## ワークフロー

```
[pre.las] ┐
          ├─► 前処理 ─► 位置合わせ ─► 差分計算 ─► スコアリング ─► 集約 ─► GeoJSON (WGS84)
[post.las]┘  (CRS統一、ノイズ除去、    (M3C2 / DSM差分)             (grid / building)
              地盤分類、ダウンサンプル)
```

## モジュール構成

| ファイル | 役割 |
|---|---|
| `main.py` | CLIエントリポイント。設定読込→各ステップ呼び出し |
| `pipeline/config.py` | YAML 設定の読み込み |
| `pipeline/io_las.py` | LAS/LAZ/CSV の読み書き（laspy） |
| `pipeline/preprocess.py` | ノイズ除去、地盤分類、CRS統一、ダウンサンプル |
| `pipeline/registration.py` | 安定領域マスク付き ICP |
| `pipeline/difference.py` | M3C2（PDAL経由）と DSM差分 |
| `pipeline/scoring.py` | 差分値→被害スコア（0〜3）の変換 |
| `pipeline/aggregation.py` | グリッド／建物ポリゴンへの集約 |
| `pipeline/export.py` | WGS84 への投影変換と GeoJSON 出力 |

## セットアップ

```bash
# 推奨: Python 3.10+
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

# M3C2 を使う場合は PDAL も別途インストール（conda 推奨）
# conda install -c conda-forge pdal python-pdal
```

## 使い方

```bash
# 1. 設定ファイルを編集
cp config.example.yaml config.yaml
# config.yaml の input_crs、ファイルパス、スコア閾値などを編集

# 2. 実行
python main.py --config config.yaml

# 個別ステップだけ実行
python main.py --config config.yaml --step preprocess
python main.py --config config.yaml --step difference
python main.py --config config.yaml --step export
```

## 出力

- `outputs/diff_points.las` — 差分値（dz）付き点群
- `outputs/dsm_diff.tif` — DSM差分ラスタ（オプション）
- `outputs/damage_score.geojson` — **Google Maps 投入用の最終成果物**

## GeoJSON の属性スキーマ

```json
{
  "type": "Feature",
  "geometry": { "type": "Polygon", "coordinates": [...] },
  "properties": {
    "id": "g_00134",
    "damage_score": 2,
    "dz_mean": -1.85,
    "dz_p95": -3.12,
    "n_points_pre": 412,
    "n_points_post": 87,
    "loss_ratio": 0.79,
    "method": "m3c2",
    "significant": true
  }
}
```

## 注意事項

- M3C2 は PDAL の `filters.m3c2` を呼び出します。CloudCompare の独自実装とは挙動差があります。
- 入力 CRS は `config.yaml` で **必ず指定**してください。LAS ヘッダの SRS が信頼できる場合はそれを優先します。
- ICP は被災領域を含めて適用すると変位が平均化されるため、`stable_mask` で安定領域のポリゴンを与えることを推奨します。
- 各モジュールには `TODO` コメントを置いています。研究対象（土砂／建物／地盤変動）に応じてチューニングしてください。
