# Magnetic diffusion on a fixed FieldCraft mesh

This example advances the magnetoquasistatic diffusion equation in NGSolve and returns 17 snapshots of the magnetic flux density to FieldCraft. Two spatial modes decay at different rates. Their changing superposition alters both the shape and the amplitude of the field.

## Reproduce

Copy `mesh.json`, `slab.case.json` and `solve.py` into a writable working directory. Set `BRIDGE` to the checkout containing `workflow.py`, `PY` to an interpreter with NGSolve and NumPy, and `FC` and `GUI` to the FieldCraft CLI and desktop executables. The desktop build must provide `results.external.series`.

```sh
"$FC" run mesh.json
"$PY" "$BRIDGE/workflow.py" case slab.case.json decay --gui "$GUI"
```

Omit `--gui` for a numerical run. Use `"$PY" "$BRIDGE/show_run.py" decay --port PORT --open-model --series B --component y` to load the saved frames into a compatible running window.

## Physics and measured check

The model is a homogeneous, stationary conductor spanning 10 × 2 × 2 mm. It represents a transverse-uniform slice with conductivity 10⁷ S/m and permeability 1.25663706127 × 10⁻⁶ H/m. The imposed initial condition stores magnetic energy, which subsequently decays without a source. The magnetic vector potential points along z. Its amplitude A obeys ∂A/∂t = (μσ)⁻¹ ΔA, with A = 0 on the two x ends and zero normal derivative on the four transverse faces. These conditions and the initial state preserve the transverse-uniform solution. [Haus and Melcher, Magnetoquasistatic Relaxation and Diffusion](https://ocw.mit.edu/courses/res-6-001-electromagnetic-fields-and-energy-spring-2008/pages/chapter-10/)

With k = π/L, B₀ = 0.1 T and τ = μσ/k², the initial potential is B₀/k [sin(kx) + 0.15 sin(2kx)]. The two terms acquire the factors exp(−t/τ) and exp(−4t/τ), respectively. The corresponding exact induction has Bᵧ = −∂A/∂x. Its magnetic energy is V B₀²/(4μ) [exp(−2t/τ) + 0.09 exp(−8t/τ)], where V is the conductor volume.

The original `mesh.block` recipe generated 576 linear tetrahedra and 225 vertices. The actual solver used H1 order 2 and 64 Crank–Nicolson steps of τ/32 from zero to 2τ (254.648 µs). At every fourth step it projected curl(A e_z) onto a continuous, first-order vector field for display. The error and energy checks integrate the FE field before that projection. With NGSolve 6.2.2608 the largest relative L² induction error across the 17 output times was 0.08323 %. Magnetic energy decreased at every recorded step, reaching 1.457040 × 10⁻⁶ J against the analytic 1.457513 × 10⁻⁶ J. `diffusion-check.json` contains the comparison at every recorded time.
