"""Verify point IDs, coordinates and tet topology of a solver's VTU before importing a named field."""

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np

from mesh_exchange import MAX_BYTES


def ascii_array(array, dtype):
    if array.get("format", "ascii") != "ascii":
        raise ValueError(
            "Expected inline ASCII VTU DataArrays; export with binary=False"
        )
    if dtype is int and array.get("type", "") not in (
        "Int8",
        "UInt8",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
    ):
        raise ValueError("Connectivity and IDs must use integer VTK arrays")
    text = (array.text or "").split()
    return np.asarray(text, dtype=np.int64 if dtype is int else np.float64)


def read_field(vtu, npz, name, unit):
    if vtu.stat().st_size > MAX_BYTES:
        raise ValueError("VTU exceeds the 256 MiB exchange limit")
    root = ET.parse(vtu).getroot()
    pieces = root.findall("./UnstructuredGrid/Piece")
    if root.get("type") != "UnstructuredGrid" or len(pieces) != 1:
        raise ValueError("Expected one UnstructuredGrid Piece")
    piece = pieces[0]

    def one(path):
        items = piece.findall(path)
        if len(items) != 1:
            raise ValueError(f"Need exactly one VTU array at {path}")
        return items[0]

    def named(parent, key):
        arrays = [
            a for a in piece.findall(f"{parent}/DataArray") if a.get("Name") == key
        ]
        if len(arrays) != 1:
            raise ValueError(f"Need exactly one {parent} array named {key!r}")
        return arrays[0]

    with np.load(npz, allow_pickle=False) as mesh:
        ids = mesh["node_ids"]
        n, t = len(ids), len(mesh["tetrahedra"])
        if (
            int(piece.get("NumberOfPoints", -1)) != n
            or int(piece.get("NumberOfCells", -1)) != t
        ):
            raise ValueError("Result has a different mesh size")
        labels = ascii_array(named("PointData", "fieldcraft_node_id"), int)
        order = np.argsort(labels)
        if not np.array_equal(labels[order], ids):
            raise ValueError(
                "VTU point IDs do not match the stored FieldCraft node labels"
            )
        coords = ascii_array(one("Points/DataArray"), float).reshape(n, 3)
        if not np.allclose(coords[order], mesh["points_m"], rtol=1e-12, atol=1e-14):
            raise ValueError(
                "VTU point coordinates differ from the original FieldCraft mesh in metres"
            )
        types = ascii_array(named("Cells", "types"), int)
        offsets = ascii_array(named("Cells", "offsets"), int)
        conn = ascii_array(named("Cells", "connectivity"), int).reshape(t, 4)
        if (
            types.shape != (t,)
            or not np.all(types == 10)
            or not np.array_equal(offsets, np.arange(1, t + 1) * 4)
            or np.any(conn < 0)
            or np.any(conn >= n)
        ):
            raise ValueError(
                "Expected linear tetrahedra referencing valid result vertices"
            )

        # Compare the point IDs for each cell independent of cell order and local vertex numbering.
        def canonical(cells):
            cells = np.sort(cells, axis=1)
            return cells[np.lexsort(cells.T)]

        if not np.array_equal(
            canonical(labels[conn]), canonical(ids[mesh["tetrahedra"]])
        ):
            raise ValueError("VTU tetrahedra differ from the original FieldCraft mesh")
        array = named("PointData", name)
        components = int(array.get("NumberOfComponents", "1"))
        if (
            components not in (1, 3)
            or not name.strip()
            or len(name) > 120
            or len(unit) > 48
        ):
            raise ValueError("Choose a named scalar or 3-vector with a concise unit")
        values = ascii_array(array, float).reshape(n, components)[order]
        if not np.isfinite(values).all():
            raise ValueError("VTU field contains non-finite values")
        return {
            "format": "fieldcraft-nodal-field/1",
            "mesh_sha256": str(mesh["mesh_sha256"].item()),
            "name": name,
            "unit": unit,
            "components": components,
            "node_ids": ids.tolist(),
            "values": values.ravel().tolist(),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vtu", type=Path)
    parser.add_argument("mesh", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--field", required=True)
    parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.vtu.resolve(), args.mesh.resolve()}:
        parser.error("The output must be a different file from the input VTU and mesh")
    field = read_field(args.vtu, args.mesh, args.field, args.unit)
    args.output.write_text(
        json.dumps(field, separators=(",", ":"), allow_nan=False) + "\n"
    )
    print(
        f"{field['name']} [{field['unit']}]: {len(field['node_ids'])} FieldCraft nodes, {field['components']} component(s)"
    )
