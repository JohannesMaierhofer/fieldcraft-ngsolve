# Force and torque on a steel armature

A permanent magnet attracts a steel armature across an air gap. This example for FieldCraft issue 963 computes that signed force and the signed torque about two points by magnetic virtual work. FieldCraft generates the tetrahedra and named regions. NGSolve solves the current-free 3D field on that mesh and returns the potential, flux density and integral values to FieldCraft. [FieldCraft issue 963](https://github.com/tangency-labs/fieldcraft/issues/963)

## Run one case

From a writable copy of this example directory, use a Python environment with this bridge, NumPy and NGSolve installed. The model and case file must be in the same directory. Keep a fresh output directory for each solve.

```sh
python make_model.py /path/to/fieldcraft-cli 2 armature.fieldcraft --gap-mm 4
fieldcraft-ngsolve case armature.case.json run-4mm
```

Add `--gui /path/to/fieldcraft` to the second command to open the result. Select `B_nodal_average` (T) and section the air box to see the flux near the gap. `scalar_potential` is in A. The integral names below identify the primary quantities in the FieldCraft results catalogue. All vectors use global Cartesian XYZ in the displayed order.

| Integral in results.json | Quantity | Unit |
| --- | --- | --- |
| `virtual_work_force_armature` | Force exerted on the steel | N |
| `virtual_work_torque_about_armature_center` | Right-hand torque about C = (3, 0, 0) mm | N·m |
| `virtual_work_torque_about_side_point` | Right-hand torque about S = (3, 2, 0) mm | N·m |
| `stationary_scalar_coenergy` | Scalar-potential functional at the computed solution | J |

## Fixed excitation and sign

The finite domain measures 24 × 16 × 16 mm. The magnet and armature each measure 2 × 4 × 4 mm. Their centres are aligned on the x axis with the magnet to the left. Remanence is fixed at **+0.6 T along x** in the stationary magnet. The relative permeabilities of air, magnet and linear isotropic steel are 1, 1.05 and 100. The outer boundary has zero scalar magnetic potential. Each gap is set by placing the magnet before the solve. The virtual motions used to measure force and torque move only the armature and neighboring air while keeping the magnet, material properties and outer boundary fixed.

With **H = −∇φ** and **B = μH + Bᵣ**, the H¹ quadratic potential makes the following functional stationary. Its additive reference is arbitrary.

```math
C(\phi)=\int_{\Omega}\left(\tfrac12\mu |\nabla\phi|^2-\mathbf B_r\cdot\nabla\phi\right)\,dV .
```

At this fixed excitation, mechanical work on the armature is the **positive** derivative of the stationary value with respect to its translation or rotation. For example, a positive x translation opens the gap. A negative derivative gives an attractive force. For angular derivatives the angle is in radians and the axis follows the right-hand rule.

Each steel vertex follows the virtual rigid motion. Nodal motion tapers to zero through a 2 mm shell of air and stays zero outside. Linear shape functions extend these motions through each tetrahedron. The isotropic steel moves rigidly and the magnet stays fixed, so only the air shell contributes. For an infinitesimal mesh velocity **v**, its contribution is −∫ **T**:∇**v** dV with **T** = ( **B**⊗**B** − ½|**B**|² **I** ) / μ₀. The implementation integrates each quadratic stress component exactly from the four element-local values of the affine flux density. It computes both rotations directly and checks **M(S) = M(C) + (C − S) × F**. The smoothed nodal field is for display. Henrotte and Hameyer derive the same air stress and nodal virtual-work relation and discuss the choice of material energy for permanent magnets. [Original paper, sections 3–4, author-hosted PDF](https://www.iem.rwth-aachen.de/global/show_document.asp?id=aaaaaaaaabceasq)

## Measured refinement and gap dependence

The table gives the axial force and the dominant side-point torque. A level uses the same grid resolution for every gap. Mesh levels contain 4,608, 36,864, 124,416 and 294,912 Tet4 elements. The JSON records all nine signed vector components, the coenergy, residual, DOF count and model, mesh and solver hashes for each run. [Complete 12-case study](virtual-work-study.json)

| Gap (mm) | L1 Fₓ (mN) | L2 Fₓ (mN) | L3 Fₓ (mN) | L4 Fₓ (mN) | L4 M<sub>z</sub> at S (µN·m) | L3→L4 ΔFₓ | L3→L4 ΔM(S), vector norm |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | -3.14663 | -3.12859 | -3.10392 | -3.09053 | -6.17918 | 0.433% | 0.447% |
| 6 | -0.60543 | -0.57747 | -0.56939 | -0.56587 | -1.13078 | 0.622% | 0.568% |
| 8 | -0.15765 | -0.14834 | -0.14591 | -0.14487 | -0.28942 | 0.712% | 0.634% |

The two symmetry planes imply zero transverse force and zero central moment in the continuum. The oriented tetrahedral subdivisions leave small residuals in those components. At the finest level the central-moment magnitudes are 2.666, 1.369, 0.463 nN·m for 4, 6 and 8 mm respectively. At 4 mm each transverse force component is +0.103 µN. The relative changes above divide by the fine-grid magnitude. They measure the last refinement step. The finite box, linear permeability approximation and magnet properties also affect the force. The left magnet-to-wall clearance shrinks from 8 mm to 4 mm across the gap study. Domain truncation error, material uncertainty and agreement with an experimental actuator have yet to be quantified.

## Independent checks

- An analytic point dipole in a uniform field with a superposed constant field gradient has **F** = (0.05, 0, 0) N, **M(C)** = (0, −0.0001, 0) N·m and **M(S)** = (0, −0.0001, +0.0001) N·m. Errors in the virtual-work vectors decrease on every mesh. From the coarsest to finest, they fall from 64.93% to **0.0211%** in force, 82.48% to **0.0240%** in central torque, and 65.80% to **0.0216%** in side-point torque. This tests the integral on a field with known nonzero force and central torque. [Analytic test data](virtual-work-checks.json)
- At 4 mm gap on levels 1 and 2, independently translate the steel by ±20 and ±10 µm along x, y and z and rotate it by ±4 and ±2 mrad about each global axis through each reference point. Each perturbed mesh is solved again at the same excitation. Central differences of the resulting coenergies verify all nine signed components. Across both grids the fine-step force vector differs by at most **0.00184%**, the side-point torque by **0.00139%**. The L2 central torque differs by **2.70 × 10⁻¹⁴ N·m**. Halving the perturbation reduced the errors by approximately four. The raw energy pairs and derivatives are included for all 72 perturbed solves. [Displaced-solve data](virtual-work-checks.json)
- The new 4 mm level-2 force and side-point moment were applied to a 16-Hex8 FieldCraft member with a 10 mN axial preload. The native support reactions balance the combined load within 1.83 × 10⁻¹⁷ N and 7.72 × 10⁻²⁰ N·m. The transfer checks the moment shift from the side point to the attachment at the armature centre. [Reproduce the structural load transfer](../../structural-load-transfer.md)

## Reproduce the study

Run these commands from the example directory in a shell with the bridge and the scientific Python environment on the search path. Choose an unused study directory.

```sh
cli=/path/to/fieldcraft-cli
study="$PWD/armature-study"
mkdir "$study"
for gap in 4 6 8; do
  for level in 1 2 3 4; do
    case_dir="$study/gap$gap-level$level"
    mkdir "$case_dir"
    python make_model.py "$cli" "$level" "$case_dir/armature.fieldcraft" --gap-mm "$gap"
    fieldcraft-ngsolve "$case_dir/armature.fieldcraft" "$PWD/magnet.py" "$case_dir/solve" \
      --materials magnet steel air --boundaries outer --interfaces armature_air_envelope
  done
done
python summarize_virtual_work.py "$study"/gap*-level*/solve/results.json > "$study/summary.json"
python check_virtual_work.py dipole "$study"/gap4-level*/solve/mesh.npz
python check_virtual_work.py differences "$study/gap4-level1/solve"
python check_virtual_work.py differences "$study/gap4-level2/solve"
```

## Supplementary surface results

The original Maxwell surface calculation remains available for comparison. It integrates the two element-side stress traces on the closed oriented interior face set `armature_air_envelope`. The older integral names `force_armature`, `torque_about_side_point`, `torque_about_armature_center`, `force_inside_trace` and `force_outside_trace` identify these surface results. Historical measurements and their original units are preserved. [Surface mesh refinement](convergence.json) · [Surface dipole check](dipole-check.json) · [Surface gap sweep](gap-sweep.json)
