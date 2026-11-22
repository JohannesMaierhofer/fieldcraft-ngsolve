"""Attach a signed external resultant to a named FieldCraft structural step."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import numpy as np

from mesh_exchange import load_parts
from workflow import digest


def command(command_id, **params):
    return {"command": command_id, "params": params}


def run_recipe(cli, path, recipe):
    path.write_text(json.dumps(recipe, allow_nan=False, indent=2), encoding="utf-8")
    result = subprocess.run([cli, "run", str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"FieldCraft exited {result.returncode}"
        )


def vector(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be a signed three-component vector")
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in value):
        raise ValueError(f"{name} contains invalid components")
    return list(map(float, value))


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def signed_integral(run, name, units):
    matches = [item for item in run["integrals"] if item["name"] == name]
    if len(matches) != 1 or matches[0]["unit"] not in units:
        raise ValueError(f"Expected exactly one {name!r} integral in {units[0]}")
    return vector(matches[0]["value"], name)


def verify_run(args):
    path = args.manifest.resolve(strict=True)
    with path.open("rb") as stream:
        raw = stream.read(4 * 1024**2 + 1)
    if (
        len(raw) > 4 * 1024**2
        or hashlib.sha256(raw).hexdigest() != args.manifest_sha256.lower()
    ):
        raise ValueError("Original run manifest size or SHA-256 mismatch")
    run = json.loads(raw, object_pairs_hook=unique_keys)
    if run["format"] != "fieldcraft-external-run/1":
        raise ValueError("Unsupported external run manifest")
    model = args.source_model or path.parent / run["model"]
    script = args.solver_script or path.parent / run["script"]
    exchange = args.exchange or path.with_name("mesh.npz")
    _, _, native_hash = load_parts(model)
    if native_hash != run["mesh_sha256"] or digest(script) != run["script_sha256"]:
        raise ValueError("Source mesh or solver script SHA-256 mismatch")
    for key, artifact in (("model_sha256", model), ("exchange_sha256", exchange)):
        if key in run and digest(artifact) != run[key]:
            raise ValueError(f"{key} mismatch")
    with zipfile.ZipFile(exchange) as archive:
        entries = [i for i in archive.infolist() if i.filename == "mesh_sha256.npy"]
        if len(entries) != 1 or entries[0].file_size > 4096:
            raise ValueError("Missing, duplicated or oversized exchange identity")
        with archive.open(entries[0]) as stream:
            version = np.lib.format.read_magic(stream)
            if version not in ((1, 0), (2, 0)):
                raise ValueError("Unsupported NumPy exchange identity")
            read_header = (
                np.lib.format.read_array_header_1_0
                if version == (1, 0)
                else np.lib.format.read_array_header_2_0
            )
            shape, _, dtype = read_header(stream)
            if shape != () or dtype.kind != "U" or dtype.itemsize != 256:
                raise ValueError("Expected a 64-character SHA-256 exchange identity")
            raw_identity = stream.read(257)
            if (
                len(raw_identity) != 256
                or np.frombuffer(raw_identity, dtype=dtype).item() != native_hash
            ):
                raise ValueError(
                    "Exchange identity does not match the native source mesh"
                )
    return run


def transfer(args):
    target = args.target.resolve(strict=True)
    output = args.output.absolute()
    if os.path.lexists(output):
        raise ValueError(f"Destination already exists: {output}")
    run = verify_run(args)
    force = signed_integral(run, args.force, ("N",))
    moment = signed_integral(run, args.moment, ("N m", "N·m", "N*m"))
    declared = run.get("physics", {}).get(f"{args.moment}_origin_m")
    if args.moment_origin is None and declared is None:
        raise ValueError("Specify --moment-origin in metres for this integral")
    origin = vector(
        declared if declared is not None else args.moment_origin, "moment origin"
    )
    if args.moment_origin is not None and origin != vector(
        args.moment_origin, "moment origin"
    ):
        raise ValueError("Supplied moment origin conflicts with the original manifest")
    point = vector(args.reference_point, "reference point")
    with np.errstate(over="raise", invalid="raise"):
        arm = np.subtract(origin, point)
        shifted = vector(
            (moment + np.cross(arm, force)).tolist(), "moment at reference point"
        )
    names = {k: f"{args.prefix}_{k}" for k in ("RP", "coupling", "force", "moment")}

    def quantities(values, unit):
        return [f"{v:.17g} {unit}" for v in values]

    before = digest(target)
    model, _, _ = load_parts(target)
    if args.region not in model.get("sets", {}):
        raise ValueError(f"Unknown attachment set: {args.region!r}")
    procedures = [
        step["procedure"]["type"]
        for step in model.get("steps", [])
        if step["name"] == args.step
    ]
    native_solvers = {
        "static": "solve.static",
        "nonlinear_static": "solve.nonlinear",
        "coupled_temperature_displacement": "solve.static",
    }
    if len(procedures) != 1 or procedures[0] not in native_solvers:
        raise ValueError(f"Expected a named static structural step: {args.step!r}")
    with tempfile.TemporaryDirectory(
        dir=output.parent, prefix=".fieldcraft-transfer-"
    ) as temp:
        folder = Path(temp)
        staged = folder / "result.fieldcraft"
        recipe = [
            command("file.open", path=str(target)),
            command(
                "model.reference_point.create",
                name=names["RP"],
                xyz=quantities(point, "m"),
            ),
            command(
                "model.coupling.create",
                name=names["coupling"],
                reference_point=names["RP"],
                set=args.region,
                kind="distributing",
                dofs=[1, 2, 3, 4, 5, 6],
            ),
        ]
        for name, values, unit in (("force", force, "N"), ("moment", shifted, "N m")):
            recipe.append(
                command(
                    "model.load.create",
                    name=names[name],
                    type=name,
                    target={"reference_point": names["RP"]},
                    vector=quantities(values, unit),
                )
            )
        recipe.append(
            command(
                "model.step.activate",
                step=args.step,
                loads=[names["force"], names["moment"]],
                active=True,
            )
        )
        if args.solve:
            recipe.append(command(native_solvers[procedures[0]], step=args.step))
        recipe.append(
            command("file.save", path=str(staged), results=True, geometry="embed")
        )
        run_recipe(args.cli, folder / "recipe.json", recipe)
        if digest(target) != before:
            raise ValueError("Target model changed during transfer")
        checksum = digest(staged)
        os.link(
            staged, output
        )  # Atomic publication, including concurrent writers. Never replaces a file.
    return {
        "format": "fieldcraft-structural-load-transfer/1",
        "manifest_sha256": args.manifest_sha256.lower(),
        "source_mesh_sha256": run["mesh_sha256"],
        "target_before_sha256": before,
        "output_sha256": checksum,
        "verified_full_source_model_hash": "model_sha256" in run,
        "verified_full_exchange_hash": "exchange_sha256" in run,
        "force_integral": args.force,
        "moment_integral": args.moment,
        "frame": "shared global right-handed Cartesian axes",
        "moment_origin_m": origin,
        "reference_point_m": point,
        "force_N": force,
        "moment_at_origin_Nm": moment,
        "moment_at_reference_point_Nm": shifted,
        "step": args.step,
        "region": args.region,
        "names": names,
        "solved": args.solve,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument(
        "target", type=Path, help="Native structural model to open read-only"
    )
    p.add_argument(
        "output", type=Path, help="New native model; the destination must not exist"
    )
    p.add_argument(
        "--manifest-sha256",
        required=True,
        help="SHA-256 of the trusted original run manifest",
    )
    p.add_argument("--force", required=True, help="Signed vector integral in N")
    p.add_argument("--moment", required=True, help="Signed vector integral in N m")
    p.add_argument(
        "--region",
        required=True,
        help="Named structural attachment face or noncollinear node set",
    )
    p.add_argument("--step", required=True, help="Name of the structural load step")
    p.add_argument(
        "--reference-point",
        required=True,
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="Attachment point in global metres",
    )
    p.add_argument(
        "--moment-origin",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="Global metres; optional when supplied by manifest",
    )
    p.add_argument(
        "--source-model", type=Path, help="Override location of original magnetic model"
    )
    p.add_argument(
        "--solver-script",
        type=Path,
        help="Override location of original external script",
    )
    p.add_argument(
        "--exchange", type=Path, help="Override location of original mesh.npz"
    )
    p.add_argument("--cli", default="fieldcraft-cli")
    p.add_argument(
        "--solve",
        action="store_true",
        help="Solve the selected native step and save its result in the output model",
    )
    p.add_argument(
        "--prefix",
        default="external",
        help="Unique native reference point, coupling and load name prefix",
    )
    try:
        receipt = transfer(p.parse_args())
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ArithmeticError,
        zipfile.BadZipFile,
    ) as exc:
        p.exit(1, f"Transfer failed: {exc}\n")
    print(json.dumps(receipt, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
