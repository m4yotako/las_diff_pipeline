"""Real-data one-shot: PRE 07FD2032 vs POST 07fd203_grd (ground filtered).

Vectorized; runs in ~20s. Paths are absolute - edit for your environment.
"""

from __future__ import annotations

import json
import os
import time

import laspy
import numpy as np

PRE = r"D:\notowest14\07FD2032.las"
POST = r"D:\ground_data_07fd1_2025\07fd203_grd.las"
OUT_DIR = r"D:\projects\gensai\las_diff_pipeline\examples\outputs\realdata"
EPSG_WORK = 6675
DSM_RES = 1.0
GRID_CELL = 5.0
THRESHOLDS = [0.3, 1.0, 3.0]

os.makedirs(OUT_DIR, exist_ok=True)
t0 = time.time()


def step(m):
    print(f"[{time.time() - t0:5.1f}s] {m}", flush=True)


step("Reading PRE ...")
l = laspy.read(PRE)
px = np.asarray(l.x); py = np.asarray(l.y); pz = np.asarray(l.z)
step(f"  PRE: {len(px):,} pts")
del l

step("Reading POST ...")
l = laspy.read(POST)
qx = np.asarray(l.x); qy = np.asarray(l.y); qz = np.asarray(l.z)
del l
step(f"  POST raw: {len(qx):,} pts")

minx, maxx = px.min() - 10, px.max() + 10
miny, maxy = py.min() - 10, py.max() + 10
m = (qx >= minx) & (qx <= maxx) & (qy >= miny) & (qy <= maxy)
qx, qy, qz = qx[m], qy[m], qz[m]
step(f"  POST in PRE bbox: {len(qx):,} pts")

gxmin, gymin = float(px.min()), float(py.min())
gxmax, gymax = float(px.max()), float(py.max())
nx = int(np.ceil((gxmax - gxmin) / DSM_RES))
ny = int(np.ceil((gymax - gymin) / DSM_RES))
step(f"DSM grid: {nx} x {ny} @ {DSM_RES}m")


def dsm_minmax(x, y, z, agg="min"):
    ix = np.clip(((x - gxmin) / DSM_RES).astype(np.int64), 0, nx - 1)
    iy = np.clip(((y - gymin) / DSM_RES).astype(np.int64), 0, ny - 1)
    flat = iy * nx + ix
    order = np.argsort(flat)
    flat_s = flat[order]; z_s = z[order]
    u, first = np.unique(flat_s, return_index=True)
    vals = (np.minimum.reduceat(z_s, first) if agg == "min"
            else np.maximum.reduceat(z_s, first))
    out = np.full(nx * ny, np.nan, dtype=np.float64)
    out[u] = vals
    return out.reshape(ny, nx)


step("DSM min for PRE ..."); pre_dsm = dsm_minmax(px, py, pz, "min")
step(f"  filled {(~np.isnan(pre_dsm)).sum()}/{nx*ny}")
step("DSM min for POST ..."); post_dsm = dsm_minmax(qx, qy, qz, "min")
step(f"  filled {(~np.isnan(post_dsm)).sum()}/{nx*ny}")

diff = post_dsm - pre_dsm
step(f"diff stats: med={np.nanmedian(diff):.2f} p5={np.nanpercentile(diff,5):.2f} "
     f"p95={np.nanpercentile(diff,95):.2f} valid={np.isfinite(diff).sum()}")

z_offset = float(np.nanmedian(diff))
step(f"Z offset (geoid-like) applied: {-z_offset:.3f}m")
diff = diff - z_offset

step(f"Aggregating to {GRID_CELL}m grid ...")
cells_x = int(np.ceil((gxmax - gxmin) / GRID_CELL))
cells_y = int(np.ceil((gymax - gymin) / GRID_CELL))
xs = gxmin + (np.arange(nx) + 0.5) * DSM_RES
ys = gymin + (np.arange(ny) + 0.5) * DSM_RES
ix = np.clip(((xs - gxmin) / GRID_CELL).astype(np.int64), 0, cells_x - 1)
iy = np.clip(((ys - gymin) / GRID_CELL).astype(np.int64), 0, cells_y - 1)
GX, GY = np.meshgrid(ix, iy)
flat = (GY * cells_x + GX).ravel()
df = diff.ravel()
valid = np.isfinite(df)
fl = flat[valid]; dv = df[valid]
order = np.argsort(fl)
fl_s = fl[order]; dv_s = dv[order]
u, first = np.unique(fl_s, return_index=True)
counts = np.diff(np.append(first, len(fl_s)))
sum_dz = np.add.reduceat(dv_s, first)
mean_dz = sum_dz / counts
max_abs = np.maximum.reduceat(np.abs(dv_s), first)
scores = np.zeros(len(u), dtype=int)
for th in THRESHOLDS:
    scores += (np.abs(mean_dz) >= th).astype(int)
cx = u % cells_x; cy = u // cells_x
x0 = gxmin + cx * GRID_CELL; y0 = gymin + cy * GRID_CELL
keep = counts >= 4
u = u[keep]; scores = scores[keep]; mean_dz = mean_dz[keep]
max_abs = max_abs[keep]; counts = counts[keep]
x0 = x0[keep]; y0 = y0[keep]
step(f"  cells: {len(u)}")

import pyproj
tr = pyproj.Transformer.from_crs(EPSG_WORK, 4326, always_xy=True)
features = []
s = GRID_CELL
for i in range(len(u)):
    X0 = float(x0[i]); Y0 = float(y0[i])
    corners = [(X0, Y0), (X0 + s, Y0), (X0 + s, Y0 + s), (X0, Y0 + s), (X0, Y0)]
    ring = [list(tr.transform(X, Y)) for X, Y in corners]
    features.append({
        "type": "Feature",
        "properties": {
            "id": f"g_{i:06d}",
            "damage_score": int(scores[i]),
            "dz_mean": float(round(mean_dz[i], 3)),
            "dz_p95": float(round(max_abs[i], 3)),
            "n_points": int(counts[i]),
            "method": "grid_dsm_min",
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    })
fc = {"type": "FeatureCollection", "features": features}
out_path = os.path.join(OUT_DIR, "damage_score.geojson")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(fc, f, ensure_ascii=False)
step(f"  wrote {out_path}")

hist = np.bincount(scores, minlength=4)[:4]
total = int(hist.sum())
print(f"\n=== Score distribution ({total} cells) ===")
for i, c in enumerate(hist):
    print(f"  score {i}: {int(c):>6} ({100*int(c)/max(total,1):.1f}%)")
step("DONE")
