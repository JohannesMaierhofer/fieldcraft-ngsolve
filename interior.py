"""Preserve oriented FieldCraft interior element-face sets in the solver mesh."""
import numpy as np


def append_interfaces(model_path, mesh_path, requested):
    from mesh_exchange import FACES, load_parts, decode_mesh
    model, raw, checksum = load_parts(model_path)
    _, labels, cells, _, tags = decode_mesh(raw)
    mesh_sets = model.get("sets",{})
    positions = {int(label):i for i,label in enumerate(labels)}
    selected = {}
    for index,name in enumerate(requested,1):
        region = mesh_sets[name]
        keys = {(f["element"],f["face"]) for f in region.get("faces",[])}
        for ref in region.get("geometry",[]):
            if set(ref)!={"body","face"}:
                raise ValueError(f"{name}: choose element faces or named geometry faces")
            keys.update(key for key,val in tags.items() if val==(ref["body"],"face",ref["face"]))
        if not keys:
            raise ValueError(f"Empty interface set {name}")
        for label,local in sorted(keys):
            if label not in positions or local not in (1,2,3,4):
                raise ValueError(f"Unknown face in {name}")
            ci = positions[label]
            oriented = tuple(cells[ci,FACES[local-1]].tolist())
            key = tuple(sorted(oriented))
            if key in selected:
                raise ValueError(f"Interface selected twice in {name}; select one owning side")
            selected[key] = [index,oriented,ci,None]
    for ci,tet in enumerate(cells.tolist()):
        for k in range(4):
            key=tuple(sorted(tet[:k]+tet[k+1:]))
            item=selected.get(key)
            if item is not None and item[2]!=ci:
                if item[3] is not None:
                    raise ValueError("Nonmanifold interface")
                item[3]=ci
    if any(v[3] is None for v in selected.values()):
        raise ValueError("Interior faces must have two incident tetrahedra")
    with np.load(mesh_path,allow_pickle=False) as archive:
        arrays={k:archive[k] for k in archive.files}
    if arrays["mesh_sha256"].item()!=checksum:
        raise ValueError("Model changed during interface export")
    group,tri,owner,neighbor=zip(*selected.values())
    arrays.update(interface_names=np.array(requested),interface_ids=np.array(group),
                  interface_triangles=np.array(tri),interface_owner=np.array(owner),
                  interface_neighbor=np.array(neighbor))
    np.savez_compressed(mesh_path,**arrays)


def attach(native, data):
    from netgen.meshing import FaceDescriptor
    region=data["material_ids"]
    offset=len(data["boundary_names"])
    for i,name in enumerate(data.get("interface_names",[]),1):
        selected=data["interface_ids"]==i
        adjacent=np.column_stack([region[data["interface_owner"][selected]], region[data["interface_neighbor"][selected]]])
        for inside,outside in np.unique(adjacent,axis=0):
            pair=np.all(adjacent==[inside,outside],axis=1)
            desc=native.Add(FaceDescriptor(surfnr=offset+i,domin=int(inside),domout=int(outside),bc=offset+i))
            tri=data["interface_triangles"][selected][pair]
            native.AddElements(2,desc,np.ascontiguousarray(tri,dtype=np.int32),base=0)
        native.SetBCName(offset+i-1,str(name))
