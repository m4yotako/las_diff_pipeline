"""Generate synthetic building polygons as GeoJSON.

5 buildings matching generate_synthetic.py positions, written in EPSG:6677.
"""

from __future__ import annotations

import json
from pathlib import Path

ORIGIN_X = -30000.0
ORIGIN_Y = -7000.0
EPSG = 6677

BUILDINGS = [
    ("B0",  40.0,  40.0, 12.0, 10.0, "no_damage"),
    ("B1",  80.0,  50.0, 15.0, 12.0, "half_collapse"),
    ("B2", 130.0,  60.0,  8.0,  8.0, "total_collapse"),
    ("B3",  60.0, 130.0, 20.0, 14.0, "minor_damage"),
    ("B4", 140.0, 140.0, 10.0, 10.0, "no_damage"),
]


def rect(cx, cy, w, h):
    x0, x1 = cx - w / 2, cx + w / 2
    y0, y1 = cy - h / 2, cy + h / 2
    return [
        [x0 + ORIGIN_X, y0 + ORIGIN_Y],
        [x1 + ORIGIN_X, y0 + ORIGIN_Y],
        [x1 + ORIGIN_X, y1 + ORIGIN_Y],
        [x0 + ORIGIN_X, y1 + ORIGIN_Y],
        [x0 + ORIGIN_X, y0 + ORIGIN_Y],
    ]


def main():
    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "buildings_synthetic.geojson"

    features = []
    for bid, cx, cy, w, h, label in BUILDINGS:
        features.append({
            "type": "Feature",
            "properties": {"building_id": bid, "label": label,
                            "cx": cx, "cy": cy, "w": w, "h": h},
            "geometry": {"type": "Polygon", "coordinates": [rect(cx, cy, w, h)]},
        })
    fc = {
        "type": "FeatureCollection",
        "name": "buildings_synthetic",
        "crs": {"type": "name",
                "properties": {"name": f"urn:ogc:def:crs:EPSG::{EPSG}"}},
        "features": features,
    }
    out_path.write_text(json.dumps(fc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {out_path}: {len(features)} buildings (EPSG:{EPSG})")
    for f in features:
        p = f["properties"]
        print(f"    {p['building_id']:>3s}  ({p['cx']:5.1f}, {p['cy']:5.1f})  "
              f"{p['w']:4.1f}x{p['h']:4.1f}  {p['label']}")


if __name__ == "__main__":
    main()
