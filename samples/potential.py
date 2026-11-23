"""One-volt linear potential on a one-metre FieldCraft slab with left and right electrodes."""
import sys
import ngsolve as ng
from fc_results import load_mesh, write_results


def solve(source, destination):
    mesh, data = load_mesh(source)
    if not {"left", "right"} <= set(mesh.GetBoundaries()):
        raise ValueError("The slab needs left and right electrode sets")
    fes = ng.H1(mesh, order=2, dirichlet="left|right")
    u, v = fes.TnT()
    stiffness = ng.BilinearForm(ng.grad(u)*ng.grad(v)*ng.dx).Assemble()
    phi = ng.GridFunction(fes)
    electrodes = {"left": 1, "right": 0}
    phi.Set(mesh.BoundaryCF(electrodes), definedon=mesh.Boundaries("left|right"))
    residual = phi.vec.CreateVector()
    residual.data = -stiffness.mat*phi.vec
    phi.vec.data += stiffness.mat.Inverse(fes.FreeDofs(), inverse="sparsecholesky")*residual
    eps = 8.8541878128e-12
    electric = -ng.grad(phi)
    energy = ng.Integrate(0.5*eps*(electric*electric), mesh)
    nodes = mesh(*data["points_m"].T)
    fields = {"potential": ("V", phi(nodes).ravel()), "electric_field": ("V/m", electric(nodes).reshape(-1,3))}
    write_results(destination, data, fields, [{"name": "stored_energy", "unit": "J", "value": energy}],
                  loads=[f"{face} electrode ({voltage} V)" for face, voltage in electrodes.items()])


if __name__ == "__main__":
    solve(*sys.argv[1:])
