"""Exchange a FieldCraft/3 linear tetrahedral mesh with a separately installed FEM solver."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

import numpy as np


# ElementKind::Tet4, its four outward faces in FieldCraft's one-based face numbering.
FACES = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
MAX_BYTES = 256 * 1024**2


def load_parts(path):
    """Check the native manifest and operate on bytes, without extracting any ZIP path."""
    with zipfile.ZipFile(path) as archive:

        def read(name, limit):
            entries = [entry for entry in archive.infolist() if entry.filename == name]
            if len(entries) != 1 or entries[0].file_size > limit:
                raise ValueError(
                    f"Missing, duplicated, or oversized archive entry {name}"
                )
            return archive.read(entries[0])

        manifest = json.loads(read("fieldcraft.json", 1048576))
        if manifest["format"] != "fieldcraft/3":
            raise ValueError(
                "Expected fieldcraft/3 (SI coordinates); migrate older files in FieldCraft"
            )
        data = {}
        for name in ("model.json", "mesh.bin"):
            value = read(name, MAX_BYTES)
            record = manifest["parts"][name]
            if (
                len(value) != record["bytes"]
                or hashlib.sha256(value).hexdigest() != record["sha256"]
            ):
                raise ValueError(f"Manifest checksum/size mismatch: {name}")
            data[name] = value
        return (
            json.loads(data["model.json"]),
            data["mesh.bin"],
            manifest["parts"]["mesh.bin"]["sha256"],
        )


def decode_mesh(data):
    stream = io.BytesIO(data)

    def unpack(fmt):
        reader = struct.Struct("<" + fmt)
        return reader.unpack(stream.read(reader.size))

    def string():
        (length,) = unpack("H")
        content = stream.read(length)
        if len(content) != length:
            raise ValueError("Truncated entity name")
        return content.decode("utf-8")

    if stream.read(8) != b"MCMESH02":
        raise ValueError("Expected MCMESH02 (SI mesh encoding)")
    nn, ne, ng, nte, ntn = unpack("5Q")
    if (
        not 4 <= nn <= len(data) // 28
        or not 1 <= ne <= len(data) // 21
        or 3 * ng + 10 * nte + 8 * ntn > len(data)
    ):
        raise ValueError("Invalid mesh counts")
    node_dtype = np.dtype([("label", "<u4"), ("xyz", "<f8", 3)])
    nodes = np.frombuffer(stream.read(28 * nn), dtype=node_dtype, count=nn)
    if (
        np.any(nodes["label"] == 0)
        or np.any(nodes["label"][1:] <= nodes["label"][:-1])
        or not np.isfinite(nodes["xyz"]).all()
    ):
        raise ValueError(
            "Node labels must be positive and strictly increasing, coordinates finite"
        )
    # The element records have variable width. Refuse an unsupported kind before advancing.
    element_labels = np.empty(ne, dtype=np.uint32)
    connectivity = np.empty((ne, 4), dtype=np.uint32)
    for i in range(ne):
        label, kind = unpack("IB")
        if kind != 7:
            raise ValueError(
                f"Element {label} has kind {kind}; this adapter accepts linear volume tetrahedra only"
            )
        element_labels[i] = label
        connectivity[i] = unpack("4I")
    if np.any(element_labels == 0) or np.any(element_labels[1:] <= element_labels[:-1]):
        raise ValueError("Invalid volume element labels")
    tets = np.searchsorted(nodes["label"], connectivity)
    if np.any(tets >= nn) or not np.array_equal(nodes["label"][tets], connectivity):
        raise ValueError("Element refers to an unknown node label")
    entities = []
    names = ("body", "face", "edge", "vertex")
    for _ in range(ng):
        (kind,) = unpack("B")
        if kind >= len(names):
            raise ValueError("Unknown geometry tag type")
        body = string()
        entities.append((body, names[kind], string() if kind else ""))

    def tag(index):
        if index >= len(entities):
            raise ValueError("Unknown entity index")
        return entities[index]

    volume_tags, face_tags = {}, {}
    for _ in range(nte):
        label, region, count = unpack("IIB")
        if region != 0xFFFFFFFF:
            volume_tags[label] = tag(region)
        for _ in range(count):
            face, ref = unpack("BI")
            face_tags[(label, face)] = tag(ref)
        (count,) = unpack("B")
        for _ in range(count):
            edge, ref = unpack("BI")
            tag(ref)
    for _ in range(ntn):
        label, ref = unpack("II")
        tag(ref)
    if stream.read(1):
        raise ValueError("Unexpected extra bytes after mesh part")
    return nodes, element_labels, tets, volume_tags, face_tags


def group_values(keys, tags, sets, names, dimension):
    """Resolve explicit element/face labels and body/face geometry references into disjoint groups."""
    result = np.zeros(len(keys), dtype=np.int32)
    kind = "elements" if dimension == 3 else "faces"
    known = set(keys)
    key_positions = {k: i for i, k in enumerate(keys)}
    for group, name in enumerate(names, 1):
        if name not in sets:
            raise ValueError(f"No model set {name!r}; available sets: {sorted(sets)}")
        definition = sets[name]
        refs = definition.get("geometry", [])
        members = (
            set(definition.get(kind, []))
            if dimension == 3
            else {
                (item["element"], item["face"]) for item in definition.get("faces", [])
            }
        )
        for ref in refs:
            if dimension == 3 and set(ref) == {"body"}:
                members.update(
                    k for k, v in tags.items() if v == (ref["body"], "body", "")
                )
            elif dimension == 2 and set(ref) == {"body", "face"}:
                members.update(
                    k
                    for k, v in tags.items()
                    if v == (ref["body"], "face", ref["face"])
                )
            elif dimension == 2 and set(ref) == {"body"}:
                members.update(
                    k for k, v in tags.items() if v[0] == ref["body"] and v[1] == "face"
                )
            else:
                raise ValueError(
                    f"Set {name!r} has unsupported geometry reference {ref} for dimension {dimension}"
                )
        if not members or not members <= known:
            raise ValueError(
                f"Set {name!r} is empty or references missing/non-exterior {kind}"
            )
        positions = [key_positions[k] for k in members]
        if np.any(result[positions]):
            raise ValueError(f"Overlapping selected {kind} groups at {name!r}")
        result[positions] = group
    if np.any(result == 0):
        raise ValueError(
            f"{np.count_nonzero(result == 0)} {kind} lack a group; select sets covering all of them"
        )
    return result


def export_mesh(source, destination, boundaries, materials):
    model, data, checksum = load_parts(source)
    nodes, cell_ids, tets, volume_tags, face_tags = decode_mesh(data)
    points = nodes["xyz"]
    corners = points[tets]
    volume6 = np.einsum(
        "ij,ij->i",
        np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
        corners[:, 3] - corners[:, 0],
    )
    if not np.all(np.isfinite(volume6) & (volume6 > 0)):
        raise ValueError("Found a degenerate or negatively oriented tetrahedron")
    faces = tets[:, FACES].reshape(-1, 3)
    _, first, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True
    )
    if np.any(counts > 2):
        raise ValueError("Non-manifold mesh: a face belongs to more than two cells")
    exterior = np.sort(first[counts == 1])
    face_keys = list(zip(cell_ids[exterior // 4].tolist(), (exterior % 4 + 1).tolist()))
    sets = model.get("sets", {})
    boundary_ids = group_values(face_keys, face_tags, sets, boundaries, 2)
    material_ids = group_values(cell_ids.tolist(), volume_tags, sets, materials, 3)
    np.savez_compressed(
        destination,
        points_m=points,
        tetrahedra=tets,
        triangles=faces[exterior],
        node_ids=nodes["label"],
        element_ids=cell_ids,
        boundary_ids=boundary_ids,
        boundary_names=np.array(boundaries),
        material_ids=material_ids,
        material_names=np.array(materials),
        mesh_sha256=np.array(checksum),
    )
    return (
        len(nodes),
        len(tets),
        dict(zip(boundaries, np.bincount(boundary_ids)[1:].tolist())),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--boundaries",
        required=True,
        nargs="+",
        help="Exterior face set names, in desired numerical order starting at 1",
    )
    parser.add_argument(
        "--materials",
        required=True,
        nargs="+",
        help="Volume element set names, in desired numerical order starting at 1",
    )
    args = parser.parse_args()
    print(export_mesh(args.source, args.destination, args.boundaries, args.materials))
