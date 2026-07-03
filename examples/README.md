# Examples

合成データでパイプラインを試したり、能登半島の実データで動作確認したりするためのサンプル集です。

## 0. 共通の前提

```bash
cd D:\projects\gensai\las_diff_pipeline
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Open3D が入らない場合は requirements.txt の `open3d>=0.18` をコメントアウトしてください。ICP は scipy 自前実装が自動でフォールバックします。

## 1. 合成データでパイプラインを試す

```bash
python examples\generate_synthetic.py            # 合成 LAS
python examples\generate_synthetic_buildings.py  # 合成建物ポリゴン
python main.py --config examples\config_synthetic_no_open3d.yaml -v
```

出力: `examples\outputs\synthetic\damage_score.geojson`

### 期待される結果（グリッド集約）

| 領域 | 期待スコア |
|---|---|
| B0, B4 周辺 | 0（変化なし） |
| B1 屋根近傍 | 2〜3（半壊で -5m） |
| B2 跡地 | 3（全壊、6m差） |
| B3 屋根近傍 | 1（軽微、1m差） |
| 土砂崩壊エリア | 3（-3.5m） |
| 土砂堆積エリア | 2（+2.0m） |

## 2. 建物単位スコアリング

```bash
python main.py --config examples\config_synthetic_building.yaml -v
```

出力: `examples\outputs\synthetic_building\damage_score.geojson`

期待される結果（5 建物）:

| ID | シナリオ | 期待 score |
|---|---|---|
| B0 | no_damage | 0 |
| B1 | half_collapse | 3 |
| B2 | total_collapse | 3 |
| B3 | minor_damage | 1 |
| B4 | no_damage | 0 |

### スコアリングルール

`scoring.building_rules` の主要設定:

| キー | 既定 | 意味 |
|---|---|---|
| `thresholds` | `[0.3, 1.0, 3.0]` | `|dz_mean|` 段階閾値 (m) |
| `loss_ratio_collapse` | `0.6` | 点喪失率がこの値以上で「全壊」候補 |
| `loss_ratio_min_dz` | `1.5` | 全壊判定の最低限 `|dz_mean|` |
| `min_pre_points` | `30` | 全消失判定の最低 pre 点数 |

判定優先順位（`scoring.py::building_score_from_stats`）:
1. `n_pre >= min_pre_points` かつ `n_post == 0` → **score 3**
2. `|dz_mean|` を `thresholds` でビン分け（dz_mean 無効なら dz_p95）
3. `loss_ratio >= loss_ratio_collapse` かつ `|dz_mean| >= loss_ratio_min_dz` → **score 3** に底上げ

## 3. OSM 建物を使う

`config_realdata_osm.yaml` がテンプレート。`osmnx` 必須。

```yaml
aggregation:
  unit: "building"
  building:
    source: "osm"
    bbox: [minx, miny, maxx, maxy]   # work_epsg のメートル系
    buffer: 0.5
```

## 4. 能登半島 実データ用ワンショット

`run_realdata.py` は PRE/POST のパスがハードコードされた即時実行スクリプト。
入力データの場所に合わせて先頭を編集してください:

```python
PRE = r"D:\notowest14\07FD2032.las"
POST = r"D:\ground_data_07fd1_2025\07fd203_grd.las"
OUT_DIR = r"D:\projects\gensai\las_diff_pipeline\examples\outputs\realdata"
EPSG_WORK = 6675
```

実行:

```bash
python examples\run_realdata.py
```

約 20 秒で `damage_score.geojson` が生成され、輪島市門前町付近の 28,000 セル
規模で被害スコアが書き出されます。
