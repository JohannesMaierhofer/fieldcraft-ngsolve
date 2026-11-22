"""Run a local user-selected formulation on a named FieldCraft mesh."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import numpy as np


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def planar_field(vtu, mesh_path, name, unit):
    from mesh_exchange import MAX_BYTES
    from result_exchange import ascii_array
    if vtu.stat().st_size > MAX_BYTES:
        raise ValueError("Oversize planar field file")
    root = ET.parse(vtu).getroot()
    pieces = root.findall("./UnstructuredGrid/Piece")
    if root.get("type") != "UnstructuredGrid" or len(pieces) != 1:
        raise ValueError("Expected a single UnstructuredGrid piece")
    piece = pieces[0]
    def array(group, label, dtype):
        matches = [a for a in piece.findall(group+"/DataArray") if label is None or a.get("Name") == label]
        if len(matches) != 1:
            raise ValueError(f"Missing or duplicate array {label}")
        return matches[0], ascii_array(matches[0], dtype)
    with np.load(mesh_path, allow_pickle=False) as mesh:
        ids = mesh["node_ids"]
        tris = mesh["triangles"]
        if (int(piece.get("NumberOfPoints", -1)), int(piece.get("NumberOfCells", -1))) != (len(ids),len(tris)):
            raise ValueError("Mesh size mismatch")
        labels = array("PointData","fieldcraft_node_id",int)[1]
        order = np.argsort(labels)
        xyz = array("Points",None,float)[1].reshape(-1,3)
        conn = array("Cells","connectivity",int)[1].reshape(-1,3)
        if (not np.array_equal(labels[order], ids) or xyz.shape != mesh["points_m"].shape
                or not np.allclose(xyz[order], mesh["points_m"], rtol=1e-12, atol=1e-14)
                or not np.array_equal(array("Cells","types",int)[1], np.full(len(tris),5))
                or not np.array_equal(array("Cells","offsets",int)[1], 3*np.arange(1,len(tris)+1))
                or np.any(conn < 0) or np.any(conn >= len(ids))):
            raise ValueError("VTU and FieldCraft vertices or cells differ")
        def topology(cells):
            rows = np.sort(cells, axis=1)
            return rows[np.lexsort(rows.T)]
        if not np.array_equal(topology(labels[conn]), topology(ids[tris])):
            raise ValueError("Different triangle connectivity")
        attr, values = array("PointData",name,float)
        components = int(attr.get("NumberOfComponents",1))
        if components not in (1,3) or not np.isfinite(values).all():
            raise ValueError(f"Invalid components or values in {name}")
        return {"format":"fieldcraft-nodal-field/1","mesh_sha256":str(mesh["mesh_sha256"].item()),
                "name":name,"unit":unit,"components":components,"node_ids":ids.tolist(),
                "values":values.reshape(len(ids),components)[order].ravel().tolist()}


def names(items):
    seen = set()
    for item in items:
        name, unit = item["name"], item["unit"]
        if (not isinstance(name,str) or not name.strip() or name in seen or len(name.encode())>120
                or not isinstance(unit,str) or not unit.strip() or len(unit.encode())>48
                or not (name+unit).isprintable()):
            raise ValueError(f"Invalid result name or unit: {name!r} {unit!r}")
        seen.add(name)


def run(args):
    from mesh_exchange import export_mesh
    from result_exchange import read_field
    if args.dimension == 2:
        from planar_exchange import export_mesh
        read_field = planar_field
    dest = args.out.absolute()
    if dest.exists():
        raise FileExistsError(f"Choose a new output directory: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    model, script = args.model.resolve(strict=True), args.script.resolve(strict=True)
    # Preserve the venv symlink. Resolving it could select the base interpreter.
    interpreter = str(Path(args.python).absolute())
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".fc-run-") as temp:
        stage = Path(temp)/"run"
        stage.mkdir()
        mesh_path = stage/"mesh.npz"
        export_mesh(model, mesh_path, args.boundaries, args.materials)
        if args.interfaces:
            if args.dimension != 3 or len(set(args.boundaries+args.interfaces))!=len(args.boundaries+args.interfaces):
                raise ValueError("Use distinct exterior and interior names on a TET4 mesh")
            from interior import append_interfaces
            append_interfaces(model, mesh_path, args.interfaces)
        with np.load(mesh_path, allow_pickle=False) as arrays:
            mesh_hash = str(arrays["mesh_sha256"].item())
        script_hash = digest(script)
        model_hash = digest(model)
        exchange_hash = digest(mesh_path)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).resolve().parent),str(args.adapter.resolve()),
                                               env.get("PYTHONPATH","")])
        subprocess.run([interpreter,str(script),str(mesh_path),str(stage)], cwd=stage, env=env,
                       check=True, timeout=args.timeout)
        if (stage/"results.json").stat().st_size > 4*1024*1024:
            raise ValueError("Oversize results manifest")
        manifest = json.loads((stage/"results.json").read_text())
        fields, integrals = manifest["fields"], manifest["integrals"]
        if manifest.get("mesh_sha256") != mesh_hash:
            raise ValueError("Missing or incorrect output mesh fingerprint")
        if not isinstance(fields,list) or not isinstance(integrals,list) or not (fields or integrals):
            raise ValueError("Expected a field or integral")
        names(fields)
        names(integrals)
        for entry in integrals:
            v = entry["value"]
            if isinstance(v,list) and len(v)!=3:
                raise ValueError(f"Expected a scalar or 3-vector for {entry['name']}")
            for scalar in v if isinstance(v,list) else [v]:
                if type(scalar) not in (float,int) or not math.isfinite(scalar):
                    raise ValueError(f"Invalid integral {entry['name']}")
        for i, field in enumerate(fields):
            payload = read_field(stage/"fields.vtu", mesh_path, field["name"],field["unit"])
            file = f"field-{i+1}.json"
            dump(stage/file,payload)
            if (stage/file).stat().st_size > 64*1024*1024:
                raise ValueError("Single field exceeds FieldCraft's 64 MiB import limit")
            field["file"] = file
            dump(stage/f"view-{i+1}.json",[
                {"command":"file.open","params":{"path":str(model)}},
                {"command":"results.external.contour","params":{"path":str(dest/file)}}])
        versions = subprocess.check_output([interpreter,"-c",
            "import json,sys,ngsolve,netgen,numpy; print(json.dumps([sys.version,ngsolve.__version__,netgen.__version__,numpy.__version__]))"],
            text=True, env=env, timeout=60)
        runtime = dict(zip(("python","ngsolve","netgen","numpy"),json.loads(versions),strict=True))
        manifest.update(format="fieldcraft-external-run/1", mesh_sha256=mesh_hash, model=str(model),
                        script=str(script), script_sha256=script_hash, interpreter=interpreter,
                        model_sha256=model_hash, exchange_sha256=exchange_hash, runtime=runtime, platform=platform.platform(), materials=args.materials,
                        boundaries=args.boundaries, dimension=args.dimension, interfaces=args.interfaces)
        dump(stage/"results.json",manifest)
        if dest.exists():
            raise FileExistsError(dest)
        stage.rename(dest)
    print(json.dumps({"output":str(dest),"fields":[f["name"] for f in fields],"integrals":integrals},
                     ensure_ascii=False))


def main():
    if sys.argv[1:2] == ["case"]:
        from case_run import main as run_case
        run_case(sys.argv[2:])
        return
    parser = argparse.ArgumentParser(description=__doc__, epilog="Use the case subcommand for a saved project and optional GUI display.")
    parser.add_argument("model",type=Path)
    parser.add_argument("script",type=Path)
    parser.add_argument("out",type=Path)
    parser.add_argument("--materials",nargs="+",required=True)
    parser.add_argument("--boundaries",nargs="+",required=True)
    parser.add_argument("--interfaces",nargs="+",default=[],help="Interior face sets, one oriented side per face")
    parser.add_argument("--dimension",choices=(2,3),type=int,default=3)
    parser.add_argument("--adapter",type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument("--python",default=sys.executable,help="Interpreter with the external solver installed")
    parser.add_argument("--timeout",type=int,default=3600)
    args = parser.parse_args()
    sys.path.insert(0,str(args.adapter.resolve()))
    run(args)


if __name__ == "__main__":
    main()
