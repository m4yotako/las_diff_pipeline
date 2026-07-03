"""Synthetic LAS generator for pipeline testing.

Scenario:
  Ground 200m x 200m (gentle relief)
  5 buildings B0-B4 at fixed positions
  Damage:
    B0 (40,40)   no damage
    B1 (80,50)   half-collapse  h:10 -> 5
    B2 (130,60)  total collapse + debris
    B3 (60,130)  minor damage   h:12 -> 11
    B4 (140,140) no damage
  Ground damage:
    landslide x[100,160] y[10,80]  -3.5m
    deposit   x[20,60]  y[70,110]  +2.0m
  POST disturbance:
    +5cm gaussian noise + (+0.15, -0.10, +0.05) m systematic offset
"""

from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np
import pyproj

ORIGIN_X = -30000.0
ORIGIN_Y = -7000.0
EPSG = 6677
EXTENT = 200.0
GROUND_DENSITY = 8
BUILDING_DENSITY = 12
GROUND_BASE_Z = 10.0


def make_ground(extent, density, base_z=10.0, noise=0.1):
    n = int(extent * extent * density)
    x = np.random.uniform(0, extent, n)
    y = np.random.uniform(0, extent, n)
    z = (base_z
         + 2.0 * np.sin(x / 50.0) * np.cos(y / 60.0)
         + np.random.normal(0, noise, n))
    return np.column_stack([x, y, z])


def make_building(cx, cy, w, h, height,
                   density=BUILDING_DENSITY, base_z=GROUND_BASE_Z):
    parts = []
    n_roof = int(w * h * density)
    rx = np.random.uniform(cx - w / 2, cx + w / 2, n_roof)
    ry = np.random.uniform(cy - h / 2, cy + h / 2, n_roof)
    rz = np.full(n_roof, base_z + height) + np.random.normal(0, 0.04, n_roof)
    parts.append(np.column_stack([rx, ry, rz]))

    n_wall = max(50, int(w * height * density / 2))
    for sign in (-1, 1):
        wx = np.random.uniform(cx - w / 2, cx + w / 2, n_wall)
        wy = np.full(n_wall, cy + sign * h / 2) + np.random.normal(0, 0.02, n_wall)
        wz = np.random.uniform(base_z, base_z + height, n_wall)
        parts.append(np.column_stack([wx, wy, wz]))

    n_wall = max(50, int(h * height * density / 2))
    for sign in (-1, 1):
        wx = np.full(n_wall, cx + sign * w / 2) + np.random.normal(0, 0.02, n_wall)
        wy = np.random.uniform(cy - h / 2, cy + h / 2, n_wall)
        wz = np.random.uniform(base_z, base_z + height, n_wall)
        parts.append(np.column_stack([wx, wy, wz]))
    return np.vstack(parts)


def make_debris(cx, cy, w, h, n=800):
    x = np.random.uniform(cx - w / 2, cx + w / 2, n)
    y = np.random.uniform(cy - h / 2, cy + h / 2, n)
    z = GROUND_BASE_Z + np.random.uniform(0.2, 2.5, n)
    return np.column_stack([x, y, z])


def write_las(xyz_local, path, epsg=EPSG):
    xyz = xyz_local.copy()
    xyz[:, 0] += ORIGIN_X
    xyz[:, 1] += ORIGIN_Y
    header = laspy.LasHeader(point_format=3, version="1.4")
    header.offsets = xyz.min(axis=0)
    header.scales = np.array([0.001, 0.001, 0.001])
    try:
        header.add_crs(pyproj.CRS.from_epsg(epsg))
    except Exception:
        pass
    las = laspy.LasData(header)
    las.x = xyz[:, 0]; las.y = xyz[:, 1]; las.z = xyz[:, 2]
    path.parent.mkdir(parents=True, exist_ok=True)
    las.write(str(path))
    print(f"  -> {path.name}: {len(xyz):,} pts  "
          f"x=[{xyz[:,0].min():.1f},{xyz[:,0].max():.1f}] "
          f"y=[{xyz[:,1].min():.1f},{xyz[:,1].max():.1f}] "
          f"z=[{xyz[:,2].min():.1f},{xyz[:,2].max():.1f}]")


def main():
    out_dir = Path(__file__).resolve().parent / "data"

    print("\n=== Pre-disaster ===")
    np.random.seed(42)
    pre_parts = [make_ground(EXTENT, GROUND_DENSITY, noise=0.08)]
    pre_buildings = [
        (40, 40, 12, 10, 8),
        (80, 50, 15, 12, 10),
        (130, 60, 8, 8, 6),
        (60, 130, 20, 14, 12),
        (140, 140, 10, 10, 7),
    ]
    for b in pre_buildings:
        pre_parts.append(make_building(*b))
    write_las(np.vstack(pre_parts), out_dir / "pre_synthetic.las")

    print("\n=== Post-disaster ===")
    np.random.seed(123)
    post_ground = make_ground(EXTENT, GROUND_DENSITY * 0.85, noise=0.12)

    mask_landslide = ((post_ground[:, 0] > 100) & (post_ground[:, 0] < 160)
                      & (post_ground[:, 1] > 10) & (post_ground[:, 1] < 80))
    post_ground[mask_landslide, 2] -= 3.5

    mask_deposit = ((post_ground[:, 0] > 20) & (post_ground[:, 0] < 60)
                    & (post_ground[:, 1] > 70) & (post_ground[:, 1] < 110))
    post_ground[mask_deposit, 2] += 2.0

    post_parts = [post_ground]
    post_buildings = [
        (40, 40, 12, 10, 8),     # B0 no damage
        (80, 50, 15, 12, 5),     # B1 half-collapse
        # B2 skipped (totally collapsed)
        (60, 130, 20, 14, 11),   # B3 minor
        (140, 140, 10, 10, 7),   # B4 no damage
    ]
    for b in post_buildings:
        post_parts.append(make_building(*b))
    post_parts.append(make_debris(130, 60, 12, 12, n=900))

    post_xyz = np.vstack(post_parts)
    post_xyz = post_xyz + np.array([0.15, -0.10, 0.05])
    post_xyz = post_xyz + np.random.normal(0, 0.05, post_xyz.shape)
    write_las(post_xyz, out_dir / "post_synthetic.las")

    print("\n=== Damage scenario ===")
    for k, v in [
        ("B0 (40,40)",   "no damage"),
        ("B1 (80,50)",   "half-collapse  h:10->5"),
        ("B2 (130,60)",  "TOTAL collapse -> debris"),
        ("B3 (60,130)",  "minor damage   h:12->11"),
        ("B4 (140,140)", "no damage"),
        ("Landslide x[100,160] y[10,80]",  "-3.5m"),
        ("Deposit   x[20,60]  y[70,110]",  "+2.0m"),
    ]:
        print(f"  {k:<35s} {v}")
    print("\nDone.")


if __name__ == "__main__":
    main()
