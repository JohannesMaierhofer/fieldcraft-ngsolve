"""Check Maxwell surface force and torque against an independent analytic dipole in a gradient."""
import json
import sys
from pathlib import Path

import numpy as np

from magnet import CENTER, MU0, PIVOT, integrate_field


def check(source):
    with np.load(source, allow_pickle=False) as data:
        points, cells = data["points_m"], data["tetrahedra"]
        labels = data["material_names"][data["material_ids"]-1]
    m = np.array([0.001, 0.0, 0.0])  # A m²
    b0 = np.array([0.0, 0.0, 0.1])   # tesla
    G = np.diag([50.0,-50.0,0.0])     # symmetric with zero trace, T/m
    r = points-CENTER
    d = np.linalg.norm(r,axis=1)
    # Only air-surface samples enter the quadrature. Omit the singular point dipole itself.
    use = d > 1e-12
    field = b0 + r @ G.T
    rh = r[use]/d[use,None]
    field[use] += MU0/(4*np.pi*d[use,None]**3)*(3*(rh@m)[:,None]*rh - m)
    force, torque, intrinsic, _, _ = integrate_field(points, cells, field[cells], labels=="steel", labels=="magnet")
    exact_force = G.T@m
    exact_intrinsic = np.cross(m, b0)
    exact_torque = exact_intrinsic + np.cross(CENTER-PIVOT, exact_force)
    return {
        "tetrahedra":len(cells),
        "force_N":force.tolist(), "exact_force_N":exact_force.tolist(),
        "torque_about_side_point_Nm":torque.tolist(), "exact_torque_about_side_point_Nm":exact_torque.tolist(),
        "relative_force_error":np.linalg.norm(force-exact_force)/np.linalg.norm(exact_force),
        "relative_torque_error":np.linalg.norm(torque-exact_torque)/np.linalg.norm(exact_torque),
    }


if __name__ == "__main__":
    rows = [check(Path(path)) for path in sys.argv[1:]]
    for previous, current in zip(rows, rows[1:]):
        if current["tetrahedra"] <= previous["tetrahedra"]:
            raise ValueError("Supply meshes in coarse-to-fine order")
        for key in ("relative_force_error", "relative_torque_error"):
            if not current[key] < previous[key]:
                raise ValueError(f"{key} did not decrease at {current['tetrahedra']} tetrahedra")
    if not rows:
        raise ValueError("Supply at least one NPZ mesh")
    print(json.dumps(rows, indent=2))
