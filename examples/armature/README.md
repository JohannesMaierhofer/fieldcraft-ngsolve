# A permanent magnet pulling a steel armature

This independent case for [FieldCraft issue 963](https://github.com/tangency-labs/fieldcraft/issues/963) uses three SI material regions, a named outer boundary and a named closed surface entirely in air. FieldCraft creates the tetrahedra. NGSolve solves for a quadratic scalar magnetic potential. The run returns the potential, a three-component magnetic flux density, the signed force on the armature, and the signed moments about the armature centre and its side point.

The finite domain measures 24 × 16 × 16 mm. The magnet and armature each measure 2 × 4 × 4 mm. The magnet has 0.6 T remanence along +x, with relative permeabilities 1.05, 100 and 1 in magnet, idealized linear steel and air. The outer boundary has zero scalar potential. The supplied sweep moves the magnet and keeps the steel and finite outer box fixed. The side-point moment is about (3, 2, 0) mm. The .fieldcraft model stores the air-side facets as an oriented set named `armature_air_envelope`.

## Run

From this example's directory use a Python interpreter with NumPy, NGSolve and this bridge installed. Each solve needs a fresh run directory.

```sh
python make_model.py /path/to/fieldcraft-cli 2 armature.fieldcraft --gap-mm 4
fieldcraft-ngsolve case armature.case.json run-4mm
```

Add `--gui /path/to/fieldcraft` to the second command to see the flux density. A section through the outer air box reveals the interior. For a gap sweep repeat with 6 and 8 mm and new run directories. For mesh refinement use levels 1 through 4 with the default 4 mm gap and keep each mesh in its own case folder.

## Checks

Magnetic flux density comes from the gradient on each tetrahedron. The display vectors are volume-weighted nodal averages. Maxwell force and moment use the two air-side FE traces before nodal averaging. The quadrature integrates the moment of the quadratic Maxwell traction on each plane triangle. The implementation checks orientation, the closed surface area, its two air neighbors and translation of the moment between the two origins. [MIT lecture on the Maxwell stress tensor](https://ocw.mit.edu/courses/6-641-electromagnetic-fields-forces-and-motion-spring-2009/d73e79c9e065499bda537452d5278d1a_MIT6_641s09_lec15.pdf)

The 4 mm mesh study has 4,608 to 294,912 tetrahedra. Its final axial force changes by 1.56%, the force vector by 5.14% and the side-point moment by 11.55%. An independent analytic dipole field checks the surface integrator with 0.42% force and 0.43% torque error on the finest grid. [Mesh refinement data](convergence.json) · [Analytic check](dipole-check.json) · [Three air gaps at fixed mesh resolution](gap-sweep.json)
