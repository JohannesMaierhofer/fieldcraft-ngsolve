# FieldCraft–NGSolve bridge

Run a local Python formulation on the mesh already prepared in FieldCraft. The input contract supports SI Tet4 volumes, named exterior faces and oriented interior faces, and planar Tri3 regions with named edges. The external process uses the installed public NGSolve Python API. FieldCraft and the solver communicate through files.

## A first solve

Use a Python 3.11+ environment with NumPy and NGSolve installed. From the bridge checkout, use a new directory for each run. Copy the small original example recipe into the model directory so the relative save path is reproducible.

```sh
bridge="$PWD"
mkdir slab-demo
cd slab-demo
cp "$bridge/samples/slab.json" .
fieldcraft-cli run slab.json
python "$bridge/workflow.py" slab.fieldcraft "$bridge/samples/potential.py" first-run \
  --materials dielectric --boundaries left right wall
```

The one-volt slab is 1 m long with a 0.01 m² cross-section. Its field is 1 V/m in the positive x direction. The stored energy is 4.4270939064e-14 J. The run writes the potential, electric field and energy. Open the same model in a FieldCraft build with external contours, then run `results.external.contour` with the absolute path to `first-run/field-1.json`. A vector supports magnitude and individual Cartesian components. Use `python show_run.py --help` to select fields in a running FieldCraft GUI over its local control port.

## Run a saved case

Keep the model, the editable Python physics script and a case JSON together. Paths in the case file are relative to that file. To run the included example, copy all three sample files to a new project directory and create the model with `fieldcraft-cli run slab.json` there. Start the complete solve with:

```sh
fieldcraft-ngsolve case slab.case.json run-01
```

Add `--gui /path/to/fieldcraft` to start a new FieldCraft window showing the configured field and the run's integrals. On macOS the executable is inside the app at `Contents/MacOS/FieldCraft`. Use `--port PORT` for a window already displaying the same mesh. Each result stays in its own run directory if GUI display fails. A case is executed only when chosen by the user. Review the referenced Python script before running a case received from someone else.

## A user-defined formulation

The runner invokes the selected interpreter with two arguments following the script name, the exchange mesh path and the output directory. Supply `--python /path/to/python` to choose another interpreter. The mesh exports complete disjoint material and exterior boundary sets. Pass `--dimension 2` for planar meshes. On tetrahedra, `--interfaces` accepts optional named internal faces, with one selected owning side for each facet.

In the script, `mesh, data = load_mesh(mesh_path)` constructs the solver mesh from the exported coordinates and connectivity. Define FE spaces, material laws, boundary conditions, solvers and post-processing in ordinary Python. `write_results` receives named real nodal arrays with units and named scalar or vector integral quantities. Export real and imaginary parts for harmonic fields. Compute integrals from the FE solution before interpolating fields for display. Include location, frame, moment reference point or time where the quantity depends on them.

`results.json` records the mesh fingerprint, model and script hashes, runtime versions, names, units and quantities. `fields.vtu` retains the original element connectivity and node IDs. Before writing per-field view files, the runner checks node labels, coordinates, cell topology, component counts and finite values. The optional `results.external.integrals` command reads the integral list in builds that include it.

Run the bridge's regression tests using `python -m unittest discover`. [FieldCraft issue 963](https://github.com/tangency-labs/fieldcraft/issues/963)
