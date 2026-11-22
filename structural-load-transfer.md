# Apply a signed force and moment to a native structural step

Use the external run's integrated force on the receiving body and its moment about a known point. The magnetic and structural meshes can differ. Both models must use the same right-handed global Cartesian axes and global coordinate origin. A region name identifies the structural attachment. Choose a face or a node set spanning an area so that the coupling can transmit all six force and moment components.

Install the Python bridge and put the FieldCraft CLI on the search path. Record the SHA-256 of the original completed run manifest at the time the result is accepted. Supply this independently retained value when applying the loads.

```sh
fieldcraft-ngsolve-transfer accepted-run/results.json flexure.fieldcraft loaded.fieldcraft \
  --manifest-sha256 "$ACCEPTED_MANIFEST_SHA256" \
  --force force_armature --moment torque_about_side_point \
  --region armature_pad --step Operating \
  --reference-point 0.003 0 0 --solve > transfer-receipt.json
```

The signed force must contain three components in N and the signed moment three components in N·m. The moment origin comes from the manifest's `physics.<moment-integral-name>_origin_m`. Pass `--moment-origin X Y Z` in metres when that key is absent. When both declarations are present, they must agree. With reference point **r**, original moment point **s**, force **F** and original moment **M(s)**, the native moment is **M(r) = M(s) + (s − r) × F**. The reference point and the couple use global axes. A scalar axial torque needs its axis and three-component representation from the producing calculation before applying this operation.

The adapter verifies the original manifest, FieldCraft mesh bytes and solver script before using any integral. It checks the identity embedded in the exchanged mesh and the full model and exchange hashes when the producing run recorded them. Older manifests have separate Boolean fields in the receipt that identify the scope of verification. Use `--source-model`, `--solver-script` and `--exchange` to locate moved artifacts with the same identities. The solver script is read only for hashing.

FieldCraft creates a reference point, attaches it with a six-degree-of-freedom distributing coupling, creates one force and one moment, and activates them on the named step alongside its existing loads. It interprets explicit SI-unit quantities even when the structural model displays millimetres and N·mm. The output model is published at a new path using a same-filesystem hard link. An existing destination, including a symbolic link, causes the operation to fail. The output directory must support hard links. Use `--prefix` to choose distinct native names for independent contributions.

`--solve` runs the selected native `static`, `nonlinear_static` or `coupled_temperature_displacement` procedure before publication and saves displacement, stress and reactions according to its output requests. A solve error includes the FieldCraft diagnostic and leaves the requested output path unused. Open the resulting model in FieldCraft, select the structural step and choose its stress or displacement contour. Use `results.reactions` with an explicit `about` point to obtain the native support resultant. In the CLI's reactions CSV the `applied` row reports force only. The support and total rows report both force and moment.

## Reproduce the native equilibrium check

```sh
python verify_transfer.py accepted-run/results.json \
  --manifest-sha256 "$ACCEPTED_MANIFEST_SHA256" \
  --force force_armature --moment torque_about_side_point \
  --moment-at-pad torque_about_armature_center \
  --cli /path/to/fieldcraft-cli --work check-new
```

This fixture creates a 20 mm long flexure with a 4 × 4 mm section directly as 16 native Hex8 elements. At x = −17 mm, nine nodes form the fixed support. The attachment is centred at (3, 0, 0) mm. A 10 mN axial force produces a measured tensile pad stress of 624–627 Pa. The native linear operating step holds this preload and adds the magnetic resultant. The check reopens both solved models, exports support reactions about the support centre and compares each of the six components with rigid-body equilibrium, including the lever arm of the combined loads. It also checks the magnetic moment against a second integral evaluated directly about the attachment point, target and output hashes, preservation of the original step, and rejected transfers. The exported CSVs, `equilibrium.json`, model and receipt are in the selected output directory.

The test measures static equilibrium with an existing tensile preload in a linear-elastic structure. For a nonlinear loading path, define the material, increments and procedure on the structural step and check its native convergence and saved frames.
