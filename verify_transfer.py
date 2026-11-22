"""Native FieldCraft equilibrium check on a preloaded Hex8 cantilever (SI)."""

import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

import numpy as np

from transfer_load import command, run_recipe
from mesh_exchange import load_parts
from workflow import digest


def build_target(cli, work):
    # FieldCraft mesh.block builds the finite-element mesh directly. Dimensions below
    # are explicitly in mm to test transfer to a non-SI display system.
    bounds = ((-17, 3), (-2, 2), (-2, 2))
    corners = [
        [bounds[0][x], bounds[1][y], bounds[2][z]]
        for x, y, z in (
            (0, 0, 0),
            (1, 0, 0),
            (1, 1, 0),
            (0, 1, 0),
            (0, 0, 1),
            (1, 0, 1),
            (1, 1, 1),
            (0, 1, 1),
        )
    ]
    native = work / "preloaded.fieldcraft"
    recipe = [
        command("model.units", system="mm-t-s-N-MPa"),
        command(
            "mesh.block",
            body="Flexure",
            kind="hex8",
            corners=corners,
            divisions=[4, 2, 2],
            faces={"6": "base", "4": "pad"},
        ),
        command("model.set.create", name="solid", geometry=[{"body": "Flexure"}]),
        command(
            "model.set.create",
            name="support",
            geometry=[{"body": "Flexure", "face": "base"}],
        ),
        command(
            "model.set.create",
            name="armature_pad",
            geometry=[{"body": "Flexure", "face": "pad"}],
        ),
        command(
            "model.material.create",
            name="steel",
            elastic={"type": "isotropic", "young": 210000, "poisson": 0.3},
        ),
        command(
            "model.section.create",
            name="section",
            set="solid",
            material="steel",
            kind={"type": "solid"},
        ),
        command(
            "model.boundary_condition.create",
            name="fixed",
            type="fixed",
            target={"set": "support"},
        ),
        command(
            "model.load.create",
            name="axial_preload",
            type="force",
            target={"set": "armature_pad"},
            vector=[0.010, 0, 0],
        ),
        command(
            "model.step.create",
            name="Preload",
            procedure={"type": "static"},
            boundary_conditions=["fixed"],
            loads=["axial_preload"],
        ),
        command(
            "model.step.create",
            name="Operating",
            procedure={"type": "static"},
            boundary_conditions=["fixed"],
            loads=["axial_preload"],
        ),
        command("solve.static", step="Preload"),
        command("file.save", path=str(native), results=True),
    ]
    run_recipe(cli, work / "baseline-recipe.json", recipe)
    return native


def reactions(cli, model, step, work):
    csv_file = work / (step + "-reactions.csv")
    recipe = [
        command("file.open", path=str(model)),
        command("model.units", system="m-kg-s-N-Pa"),
        command(
            "results.export.csv",
            what="reactions",
            step=step,
            sets=["support"],
            about=[-0.017, 0, 0],
            path=str(csv_file),
        ),
        command(
            "results.export.csv",
            what="probe",
            step=step,
            set="armature_pad",
            path=str(work / (step + "-probe.csv")),
        ),
    ]
    run_recipe(cli, work / (step + "-recipe.json"), recipe)
    with csv_file.open() as stream:
        return list(csv.DictReader(stream))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--cli", required=True, type=Path)
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--force", required=True)
    p.add_argument("--moment", required=True)
    p.add_argument(
        "--moment-at-pad",
        required=True,
        help="Independent integral about pad center (comparison)",
    )
    a = p.parse_args()
    w = a.work.resolve()
    w.mkdir(parents=True)
    target = build_target(a.cli, w)
    out = w / "loaded.fieldcraft"
    transfer = [
        sys.executable,
        str(Path(__file__).with_name("transfer_load.py")),
        str(a.manifest),
        str(target),
        str(out),
        "--manifest-sha256",
        a.manifest_sha256,
        "--force",
        a.force,
        "--moment",
        a.moment,
        "--region",
        "armature_pad",
        "--step",
        "Operating",
        "--reference-point",
        "0.003",
        "0",
        "0",
        "--cli",
        str(a.cli),
        "--solve",
    ]
    original_hash = digest(target)
    transfer_run = subprocess.run(transfer, check=True, capture_output=True, text=True)
    receipt = json.loads(transfer_run.stdout)
    (w / "transfer-receipt.json").write_text(transfer_run.stdout)
    if digest(target) != original_hash:
        raise ValueError("Input model changed")
    baseline = reactions(a.cli, target, "Preload", w)
    combined = reactions(a.cli, out, "Operating", w)

    def reaction(rows, label, columns):
        row = next(row for row in rows if row["set"] == label)
        return np.array([float(row[col]) for col in columns])

    force_cols = ["fx [N]", "fy [N]", "fz [N]"]
    moment_cols = ["mx [N·m]", "my [N·m]", "mz [N·m]"]

    def native_values(rows, label):
        return np.r_[
            reaction(rows, label, force_cols), reaction(rows, label, moment_cols)
        ]

    source = json.loads(a.manifest.read_text())
    integrals = {i["name"]: np.array(i["value"]) for i in source["integrals"]}
    preload = np.array([0.010, 0, 0])
    base = np.array([-0.017, 0, 0])
    pad = np.array([0.003, 0, 0])
    origin = source["physics"][a.moment + "_origin_m"]
    magnetic_force = integrals[a.force]
    # Take the moment directly from the independent Maxwell integral at its own origin.
    # This balance is independent of the transfer code's chosen attachment point.
    magnetic_moment_at_base = integrals[a.moment] + np.cross(
        origin - base, magnetic_force
    )
    preload_moment_at_base = np.cross(pad - base, preload)
    expected_preload = -np.r_[preload, preload_moment_at_base]
    expected_increment = -np.r_[magnetic_force, magnetic_moment_at_base]
    expected_total = expected_preload + expected_increment
    for name in ("support", "total"):
        np.testing.assert_allclose(
            native_values(baseline, name), expected_preload, rtol=2e-10, atol=1e-14
        )
        np.testing.assert_allclose(
            native_values(combined, name), expected_total, rtol=2e-10, atol=1e-14
        )
        np.testing.assert_allclose(
            native_values(combined, name) - native_values(baseline, name),
            expected_increment,
            rtol=2e-10,
            atol=1e-14,
        )
    np.testing.assert_allclose(
        reaction(combined, "applied", force_cols),
        -expected_total[:3],
        rtol=2e-10,
        atol=1e-14,
    )
    np.testing.assert_allclose(
        receipt["moment_at_reference_point_Nm"],
        integrals[a.moment_at_pad],
        rtol=2e-10,
        atol=1e-18,
    )
    original_model, _, _ = load_parts(target)
    transferred_model, _, _ = load_parts(out)
    if original_model["display_units"] != transferred_model["display_units"]:
        raise ValueError("Display units changed")
    for step in transferred_model["steps"]:
        expected = (
            ["axial_preload", "external_force", "external_moment"]
            if step["name"] == "Operating"
            else ["axial_preload"]
        )
        if step["loads"] != expected or step["boundary_conditions"] != ["fixed"]:
            raise ValueError("Unexpected load or boundary-condition activation")
    with (w / "Preload-probe.csv").open() as stream:
        preload_stress = [float(row["s_xx [Pa]"]) for row in csv.DictReader(stream)]
    if min(preload_stress) <= 0:
        raise ValueError("Expected tensile prestress on the pad")

    def refused(command_line, dest, expected_message):
        result = subprocess.run(command_line, capture_output=True, text=True)
        if result.returncode == 0 or expected_message not in result.stderr:
            raise ValueError(
                f"Expected the transfer to reject this input: {result.stderr}"
            )
        if dest is not None and dest.exists():
            raise ValueError("Rejected transfer published a file")

    new_dest = w / "should-not-exist.fieldcraft"

    def with_option(option, value):
        cmd = transfer.copy()
        cmd[4] = str(new_dest)
        cmd[cmd.index(option) + 1] = value
        return cmd

    refused(transfer, None, "Destination already exists")
    refused(
        with_option("--manifest-sha256", "0" * 64),
        new_dest,
        "manifest size or SHA-256 mismatch",
    )
    refused(with_option("--force", a.moment), new_dest, "integral in N")
    refused(with_option("--step", "missing"), new_dest, "structural step")
    refused(with_option("--region", "missing"), new_dest, "Unknown attachment set")
    correct = with_option("--force", a.force)
    refused(correct + ["--moment-origin", "0", "0", "0"], new_dest, "origin conflicts")
    refused(
        correct + ["--solver-script", str(Path(__file__))],
        new_dest,
        "solver script SHA-256 mismatch",
    )
    if digest(out) != receipt["output_sha256"] or digest(target) != original_hash:
        raise ValueError("Rejected transfer changed an existing file")
    measured = native_values(combined, "support")
    report = {
        "format": "fieldcraft-native-structural-equilibrium-check/1",
        "elements_hex8": 16,
        "physical_nodes": 45,
        "restrained_nodes": 9,
        "support_origin_m": base.tolist(),
        "axial_preload_N": preload.tolist(),
        "pad_prestress_xx_range_Pa": [min(preload_stress), max(preload_stress)],
        "applied_magnetic_force_N": magnetic_force.tolist(),
        "applied_magnetic_moment_at_pad_Nm": integrals[a.moment_at_pad].tolist(),
        "reaction_preload_N_then_Nm": native_values(baseline, "support").tolist(),
        "reaction_loaded_N_then_Nm": measured.tolist(),
        "expected_loaded_N_then_Nm": expected_total.tolist(),
        "max_force_imbalance_N": float(max(abs(measured[:3] - expected_total[:3]))),
        "max_moment_imbalance_Nm": float(max(abs(measured[3:] - expected_total[3:]))),
        "negative_checks": [
            "destination exists",
            "wrong manifest hash",
            "wrong units",
            "unknown step",
            "unknown region",
            "inconsistent moment origin",
            "wrong solver bytes",
        ],
    }
    (w / "equilibrium.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
