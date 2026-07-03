# Viewer

`damage_score.geojson` をブラウザで色分け表示するシングルページビューア。

## 推奨: `index_osm.html` (API キー不要)

Leaflet + OpenStreetMap / 地理院タイルで動きます。**完全無料・登録不要**。

```bash
cd D:\projects\gensai\las_diff_pipeline
python -m http.server 8000
```

ブラウザで:

```
http://localhost:8000/viewer/index_osm.html
```

3 つのベース地図切替可:
- OpenStreetMap
- 地理院標準
- 地理院シームレス空中写真（**能登半島の被害確認に推奨**）

## オプション: `index.html` (Google Maps API)

Google Maps API キーが必要（月 $200 無料枠、要クレカ登録）。

```
http://localhost:8000/viewer/index.html?key=YOUR_GMAPS_KEY
```

## ビューアーが期待する GeoJSON 属性

```jsonc
{
  "properties": {
    "id": "string",
    "damage_score": 0-3,         // 必須
    "dz_mean": -1.85,            // 任意 (m)
    "dz_p95": 3.12,              // 任意 (m)
    "n_points": 412,             // 任意 (grid用)
    "n_points_post": 87,         // 任意 (building用)
    "significant_ratio": 0.87,   // 任意 (0-1)
    "method": "grid" | "building"
  }
}
```

`damage_score` だけ必須。他は無ければ「—」表示。
