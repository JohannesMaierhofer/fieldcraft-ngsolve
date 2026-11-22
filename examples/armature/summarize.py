"""Compare forces with the next finer grid, preserving all directional components in SI."""
import argparse
import json
from pathlib import Path

import numpy as np


def run(sources):
    rows = []
    previous = None
    for source in sources:
        data = json.loads(source.read_text())
        q = {e["name"]: e["value"] for e in data["integrals"]}
        f = np.array(q["force_armature"])
        tau = np.array(q["torque_about_side_point"])
        row = {"mesh_sha256": data["mesh_sha256"], "dofs": data["physics"]["dofs"],
               "script_sha256": data["script_sha256"], "runtime": data["runtime"], **q}
        if previous is not None:
            pf, pt = previous
            row["next_grid_vector_force_change_over_current"] = float(np.linalg.norm(f-pf)/np.linalg.norm(f))
            row["next_grid_axial_force_change_over_current"] = float(abs(f[0]-pf[0])/abs(f[0]))
            row["next_grid_torque_change_over_current"] = float(np.linalg.norm(tau-pt)/np.linalg.norm(tau))
        rows.append(row)
        previous = f, tau
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    print(json.dumps(run(args.files), indent=2))
