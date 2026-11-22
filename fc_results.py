"""Transfer SI FieldCraft mesh arrays into the installed NGSolve and back to ASCII VTU."""
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np


def load_mesh(path):
    import netgen.meshing as nm
    import ngsolve as ng

    with np.load(path, allow_pickle=False) as archive:
        data = {name: archive[name] for name in archive.files}
    if "tetrahedra" not in data:
        from planar_exchange import ngsolve_mesh
        return ngsolve_mesh(data), data
    points = data["points_m"]
    cells = data["tetrahedra"]
    tris = data["triangles"]
    native = nm.Mesh(dim=3)
    native.AddPoints(np.ascontiguousarray(points, dtype=float))
    for tag, name in enumerate(data["material_names"], 1):
        native.SetMaterial(tag, str(name))
        group = cells[data["material_ids"] == tag]
        # FieldCraft TET4 has positive orientation, Netgen uses the opposite order.
        native.AddElements(3, tag, np.ascontiguousarray(group[:, [1, 0, 2, 3]], dtype=np.int32), base=0)

    # One named exterior can border several materials. Each pair gets the proper domin.
    # Keep only exterior triangles while matching them to their incident tetrahedra.
    owners = {tuple(sorted(t)): None for t in tris.tolist()}
    for tet, region in zip(cells.tolist(), data["material_ids"].tolist(), strict=True):
        for omitted in range(4):
            face = tuple(sorted(tet[:omitted] + tet[omitted+1:]))
            if face in owners:
                if owners[face] is not None:
                    raise ValueError("An exterior triangle has two incident cells")
                owners[face] = region
    incident = np.array([owners[tuple(sorted(t))] for t in tris.tolist()])
    if None in incident:
        raise ValueError("Exterior triangle without incident volume")
    region_number = 0
    for tag, name in enumerate(data["boundary_names"], 1):
        selected = data["boundary_ids"] == tag
        for region in np.unique(incident[selected]):
            region_number += 1
            desc = native.Add(nm.FaceDescriptor(surfnr=region_number, domin=int(region), domout=0, bc=region_number))
            native.SetBCName(desc-1, str(name))
            native.AddElements(2, desc, np.ascontiguousarray(tris[selected & (incident == region)], dtype=np.int32), base=0)

    from interior import attach
    attach(native, data, region_number)
    native.UpdateTopology()
    mesh = ng.Mesh(native)
    if mesh.ne != len(cells) or mesh.nv != len(points):
        raise ValueError("NGSolve changed the vertex or element count")
    return mesh, data


def write_results(output, data, fields, integrals, *, series=None):
    """Write original topology and labelled real fields. Scalars and vectors carry SI units."""
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    points = data["points_m"]
    is_volume = "tetrahedra" in data
    cells = data["tetrahedra" if is_volume else "triangles"]
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian")
    piece = ET.SubElement(ET.SubElement(root, "UnstructuredGrid"), "Piece",
                          NumberOfPoints=str(len(points)), NumberOfCells=str(len(cells)))

    def add(parent, name, vtk_type, values, components=1):
        array = ET.SubElement(parent, "DataArray", Name=name, type=vtk_type,
                              format="ascii", NumberOfComponents=str(components))
        spec = ".17g" if vtk_type == "Float64" else "d"
        array.text = " ".join(format(v, spec) for v in np.asarray(values).flat)

    point_data = ET.SubElement(piece, "PointData")
    add(point_data, "fieldcraft_node_id", "Int64", data["node_ids"])
    catalog = []
    for name, (unit, values) in fields.items():
        values = np.asarray(values)
        if (not name.strip() or name == "fieldcraft_node_id" or not unit.strip()
                or values.shape not in ((len(points),), (len(points), 3))
                or np.iscomplexobj(values) or not np.isfinite(values).all()):
            raise ValueError(f"Invalid real scalar or 3-vector {name!r}")
        add(point_data, name, "Float64", values, 1 if values.ndim == 1 else 3)
        catalog.append({"name": name, "unit": unit})
    add(ET.SubElement(piece, "Points"), "coordinates_m", "Float64", points, 3)
    cell_data = ET.SubElement(piece, "Cells")
    width = cells.shape[1]
    add(cell_data, "connectivity", "Int64", cells)
    add(cell_data, "offsets", "Int64", width*np.arange(1, len(cells)+1))
    add(cell_data, "types", "UInt8", np.full(len(cells), 10 if is_volume else 5))
    ET.ElementTree(root).write(out/"fields.vtu", encoding="utf-8", xml_declaration=True)
    manifest = {"fields": catalog, "integrals": integrals, "mesh_sha256": str(data["mesh_sha256"].item())}
    if series:
        from time_series import write
        manifest["series"] = write(out, data, series)
    (out/"results.json").write_text(json.dumps(manifest, allow_nan=False, indent=2)+"\n")
