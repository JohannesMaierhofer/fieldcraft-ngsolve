"""Generate the original magnet/armature benchmark using FieldCraft's structured Tet4 mesher."""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from mesh_exchange import FACES, decode_mesh, load_parts

# Coordinates in metres. Material and integration boundaries coincide with every refinement.
DOMAIN = np.array([[-0.012, -0.008, -0.008], [0.012, 0.008, 0.008]])
MAGNET = np.array([[-0.004, -0.002, -0.002], [-0.002, 0.002, 0.002]])
STEEL = np.array([[0.002, -0.002, -0.002], [0.004, 0.002, 0.002]])
ENVELOPE = np.array([[0.0, -0.004, -0.004], [0.006, 0.004, 0.004]])


def contains(xyz, bounds):
    return np.all((xyz >= bounds[0]) & (xyz <= bounds[1]), axis=1)


def command(action, **params):
    return {"command": action, "params": params}


def boundary_faces(cells, region):
    """Return owner and local face for a closed subset; internal pairs cancel."""
    owners = np.flatnonzero(region)
    faces = cells[owners][:, FACES].reshape(-1, 3)
    _, first, counts = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True)
    if np.any(counts > 2):
        raise ValueError("Nonmanifold shell")
    exterior = first[counts == 1]
    return owners[exterior // 4], exterior % 4


def make_model(cli, level, dest, gap_mm=4):
    if level not in (1, 2, 3, 4):
        raise ValueError("Level must be 1, 2, 3 or 4")
    if gap_mm not in (4,6,8):
        raise ValueError("Use 4, 6 or 8 mm to keep the material and integration faces aligned")
    magnet_bounds=MAGNET.copy()
    magnet_bounds[:,0]+=(4-gap_mm)*1e-3
    x0, y0, z0 = DOMAIN[0]
    x1, y1, z1 = DOMAIN[1]
    corners = [[x,y,z] for z in (z0,z1) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        temp = Path(temp)
        def run(recipe):
            path = temp / "commands.json"
            path.write_text(json.dumps(recipe))
            subprocess.run([cli, "run", str(path)], cwd=temp, check=True, stdout=subprocess.DEVNULL)
        base = temp / "block.fieldcraft"
        run([command("model.units", system="m-kg-s-N-Pa"),
             command("mesh.block", body="Domain", kind="tet4", corners=corners,
                     faces={str(i): "outer" for i in range(1, 7)}, divisions=[12*level, 8*level, 8*level]),
             command("file.save", path=str(base))])
        _, data, _ = load_parts(base)
        nodes, ids, cells, _, _ = decode_mesh(data)
        centers = nodes["xyz"][cells].mean(axis=1)
        mag, steel = contains(centers, magnet_bounds), contains(centers, STEEL)
        material_sets = {"magnet": mag, "steel": steel, "air": ~(mag | steel)}
        surface, faces = boundary_faces(cells, contains(centers, ENVELOPE))
        recipe = [command("file.open", path=str(base))]
        recipe.extend(command("model.set.create", name=name, elements=ids[indices].tolist())
                      for name, indices in material_sets.items())
        recipe.append(command("model.set.create", name="outer", geometry=[{"body":"Domain","face":"outer"}]))
        recipe.append(command("model.set.create", name="armature_air_envelope",
                              faces=[{"element": int(ids[k]), "face": int(f+1)} for k,f in zip(surface, faces)]))
        recipe.append(command("file.save", path=str(dest)))
        run(recipe)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cli")
    parser.add_argument("level", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gap-mm", type=int, choices=(4,6,8), default=4)
    args = parser.parse_args()
    make_model(args.cli, args.level, args.output.resolve(), args.gap_mm)
