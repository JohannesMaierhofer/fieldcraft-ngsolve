"""Current-free permanent-magnet/steel armature example for the external FieldCraft runner."""
import json
import sys
from pathlib import Path

import ngsolve as ng
import numpy as np

from fc_results import load_mesh, write_results
from make_model import DOMAIN, ENVELOPE, FACES, MAGNET, STEEL, boundary_faces, contains

# Idealized linear material data. The permeabilities and remanent flux specify this case.
MU0 = 1.25663706127e-6
RELATIVE_MU = {"air": 1.0, "magnet": 1.05, "steel": 100.0}
REMANENCE = 0.6  # Tesla in the positive x direction.
PIVOT = np.array([0.003, 0.002, 0.0])  # armature side point, metres
CENTER = STEEL.mean(axis=0)


def integrate_field(points, cells, B, steel, magnet, interface=None):
    """Integrate the two quadratic air-side stress traces over the 6 box faces."""
    corners = points[cells]
    centroids = corners.mean(axis=1)
    inner = contains(centroids, ENVELOPE)
    if not inner[steel].all() or inner[magnet].any():
        raise ValueError("Integration box must enclose all steel and no magnet")
    i_cell, i_local = boundary_faces(cells, inner)
    expected_tri = cells[i_cell[:,None], FACES[i_local]]
    if interface is None:
        # Analytic dipole check builds its surface independently from the geometric box.
        hx = np.max(np.ptp(corners, axis=1), axis=0)
        nearby = contains(centroids, ENVELOPE + np.array([-hx, hx]))
        o_cell, o_local = boundary_faces(cells, ~inner & nearby)
        outer = {tuple(sorted(face)): cell for face, cell in
                 zip(cells[o_cell[:,None], FACES[o_local]], o_cell)}
        tri = expected_tri
        o_cell = np.array([outer[tuple(sorted(face))] for face in tri])
    else:
        i_cell, o_cell, tri = interface
        if (not inner[i_cell].all() or inner[o_cell].any()
                or {tuple(sorted(t)) for t in expected_tri} != {tuple(sorted(t)) for t in tri}):
            raise ValueError("Named integration surface differs from the closed armature envelope")
    if np.any((steel | magnet)[np.r_[i_cell, o_cell]]):
        raise ValueError("Integration faces must have air on both sides")
    t = points[tri]
    a = np.cross(t[:,1]-t[:,0], t[:,2]-t[:,0])/2
    center = t.mean(axis=1)
    if np.any(np.einsum("ij,ij->i", center - ENVELOPE.mean(axis=0), a) <= 0):
        raise ValueError("An integration face has an inward normal")
    np.testing.assert_allclose(a.sum(axis=0), np.zeros(3), atol=1e-14)
    l = ENVELOPE[1]-ENVELOPE[0]
    np.testing.assert_allclose(np.linalg.norm(a, axis=1).sum(), 2*(l[0]*l[1]+l[1]*l[2]+l[2]*l[0]), rtol=1e-11)
    # Degree-three Hammer quadrature integrates quadratic stress times linear moment arm.
    # All points are interior to their triangle. H1 gradients may jump across this air interface.
    bary = np.array([[1/3]*3, [3/5,1/5,1/5], [1/5,3/5,1/5], [1/5,1/5,3/5]])
    weights = [-27/48, 25/48, 25/48, 25/48]
    def traces(cell_indices):
        local = np.argmax(cells[cell_indices,None,:] == tri[:,:,None], axis=2)
        return B[cell_indices[:,None], local]
    def traction(b):
        return (np.einsum("ij,ij->i",b,a)[:,None]*b - 0.5*np.einsum("ij,ij->i",b,b)[:,None]*a)/MU0
    inside, outside = traces(i_cell), traces(o_cell)
    F_in, F_out = np.zeros(3), np.zeros(3)
    torque, torque_center = np.zeros(3), np.zeros(3)
    for w, lmb in zip(weights, bary):
        r = np.einsum("j,ijk->ik", lmb, t)
        tin = traction(np.einsum("j,ijk->ik", lmb, inside))
        tout = traction(np.einsum("j,ijk->ik", lmb, outside))
        f = 0.5*w*(tin+tout)
        F_in += w*tin.sum(axis=0)
        F_out += w*tout.sum(axis=0)
        torque += np.cross(r-PIVOT, f).sum(axis=0)
        torque_center += np.cross(r-CENTER, f).sum(axis=0)
    total = (F_in+F_out)*0.5
    np.testing.assert_allclose(torque, torque_center + np.cross(CENTER-PIVOT, total), atol=1e-15)
    return total, torque, torque_center, F_in, F_out


def solve(source, output):
    mesh, data = load_mesh(source)
    xyz, cells = data["points_m"], data["tetrahedra"]
    names = data["material_names"].astype(str)
    labels = names[data["material_ids"] - 1]
    steel, magnet = labels == "steel", labels == "magnet"
    mu = mesh.MaterialCF({name: MU0*value for name,value in RELATIVE_MU.items()})
    br = mesh.MaterialCF({"magnet": REMANENCE}, default=0)
    if set(mesh.GetMaterials()) != set(RELATIVE_MU) or set(mesh.GetBoundaries()) != {"outer", "armature_air_envelope"}:
        raise ValueError("Expected air, magnet, steel materials and an outer flux-normal boundary")
    fes = ng.H1(mesh, order=2, dirichlet="outer")
    u, v = fes.TnT()
    a = ng.BilinearForm(fes, symmetric=True)
    a += mu*ng.grad(u)*ng.grad(v)*ng.dx
    f = ng.LinearForm(fes)
    f += br*ng.grad(v)[0]*ng.dx
    pre = ng.Preconditioner(a, "local")
    a.Assemble()
    f.Assemble()
    potential = ng.GridFunction(fes)
    potential.vec.data = ng.solvers.CG(mat=a.mat, rhs=f.vec, pre=pre.mat, tol=1e-12, maxsteps=2000, printrates=False)
    free = ng.Projector(fes.FreeDofs(), True)
    residual = ng.Norm(free*(a.mat*potential.vec-f.vec))/ng.Norm(free*f.vec)
    if not np.isfinite(residual) or residual > 1e-8:
        raise ValueError(f"Poor relative free-DOF residual {residual}")
    vtx = np.asarray(potential(mesh(*xyz.T))).reshape(-1)
    points = xyz[cells]
    delta = points[:,1:] - points[:,:1]
    grad = np.linalg.solve(delta, (vtx[cells[:,1:]] - vtx[cells[:,:1]])[:,:,None]).squeeze(2)
    volume = np.linalg.det(delta)/6
    for name, box in (("air", DOMAIN), ("magnet", MAGNET), ("steel", STEEL)):
        expected = np.prod(box[1]-box[0])
        if name == "air":
            expected -= np.prod(MAGNET[1]-MAGNET[0]) + np.prod(STEEL[1]-STEEL[0])
        np.testing.assert_allclose(volume[labels==name].sum(), expected, rtol=1e-10)
    mu_cells = np.array([RELATIVE_MU[name] for name in names])[data["material_ids"]-1]*MU0
    # Each P2 potential restricts to a quadratic on its tetrahedron. Recover its
    # affine gradient exactly from its four vertex and six edge-midpoint samples.
    basis_grad = np.transpose(np.linalg.inv(delta), (0,2,1))
    basis_grad = np.concatenate((-basis_grad.sum(axis=1)[:,None,:], basis_grad), axis=1)
    gradient = np.repeat(grad[:,None,:], repeats=4, axis=1)
    for i in range(4):
        for j in range(i+1,4):
            mid = 0.5*(points[:,i]+points[:,j])
            mid_phi = np.asarray(potential(mesh(*mid.T))).reshape(-1)
            correction = 4*(mid_phi - 0.5*(vtx[cells[:,i]]+vtx[cells[:,j]]))
            gradient[:,i] += correction[:,None]*basis_grad[:,j]
            gradient[:,j] += correction[:,None]*basis_grad[:,i]
    sample = np.linspace(0, len(cells)-1, min(len(cells), 64), dtype=int)
    centroids = points[sample].mean(axis=1)
    direct = np.asarray(ng.grad(potential)(mesh(*centroids.T))).reshape(-1,3)
    reconstructed = gradient[sample].mean(axis=1)
    if np.max(np.abs(direct - reconstructed)) > 1e-9 * max(1, np.max(np.abs(direct))):
        raise ValueError("P2 gradient reconstruction disagrees with NGSolve at cell centroids")
    B = -gradient*mu_cells[:,None,None]
    B[magnet,:,0] += REMANENCE
    name = "armature_air_envelope"
    group = data["interface_names"].tolist().index(name) + 1
    surface = data["interface_ids"] == group
    interface = tuple(data[key][surface] for key in ("interface_owner", "interface_neighbor", "interface_triangles"))
    F, tau, tau_c, F_in, F_out = integrate_field(xyz, cells, B, steel, magnet, interface)
    # Display only: averaging both sides at a material interface can smooth a physical jump.
    accum, weight = np.zeros_like(xyz), np.zeros(len(xyz))
    for local in range(4):
        np.add.at(accum, cells[:,local], B[:,local]*volume[:,None])
        np.add.at(weight, cells[:,local], volume)
    B_display = accum / weight[:,None]
    write_results(output, data, {"scalar_potential": ("A", vtx), "B_nodal_average": ("T", B_display)},
                  [{"name":"force_armature", "unit":"N", "value":F.tolist()},
                   {"name":"torque_about_side_point", "unit":"N m", "value":tau.tolist()},
                   {"name":"torque_about_armature_center", "unit":"N m", "value":tau_c.tolist()},
                   {"name":"force_inside_trace", "unit":"N", "value":F_in.tolist()},
                   {"name":"force_outside_trace", "unit":"N", "value":F_out.tolist()},
                   {"name":"free_dof_residual", "unit":"1", "value":float(residual)}])

    manifest_path = Path(output)/"results.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["physics"] = {
        "formulation": "current-free 3D magnetostatic scalar potential, H1/P2",
        "relative_permeabilities": RELATIVE_MU, "remanent_flux_T": [REMANENCE, 0, 0],
        "outer_boundary": "zero scalar potential", "gap_m": float(xyz[cells[steel],0].min() - xyz[cells[magnet],0].max()),
        "surface": "armature_air_envelope", "air_envelope_m": ENVELOPE.tolist(),
        "torque_about_side_point_origin_m": PIVOT.tolist(),
        "torque_about_armature_center_origin_m": CENTER.tolist(), "degree": 2, "dofs": fes.ndof,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    solve(Path(sys.argv[1]), Path(sys.argv[2]))
