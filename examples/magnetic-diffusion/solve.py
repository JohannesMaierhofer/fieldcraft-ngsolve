"""Magnetoquasistatic free decay of two spatial modes in a conducting slab."""
import json
import math
from pathlib import Path
import sys

import ngsolve as ng
import numpy as np

from fc_results import load_mesh, write_results


def solve(source, destination):
    mesh, data = load_mesh(source)
    if set(mesh.GetMaterials()) != {'conductor'} or set(mesh.GetBoundaries()) != {'left','right','wall'}:
        raise ValueError('Expected one conductor with left, right and symmetry boundaries')
    length = 0.01
    mu, sigma = 1.25663706127e-6, 1.0e7
    amplitude, harmonic = 0.1, 0.3
    k = math.pi/length
    tau = mu*sigma/k**2
    dt = tau/32
    fes = ng.H1(mesh, order=2, dirichlet='left|right')
    u, v = fes.TnT()
    diffusion = ng.grad(u)*ng.grad(v)/(mu*sigma)
    lhs = ng.BilinearForm((u*v + 0.5*dt*diffusion)*ng.dx).Assemble()
    rhs = ng.BilinearForm((u*v - 0.5*dt*diffusion)*ng.dx).Assemble()
    inverse = lhs.mat.Inverse(fes.FreeDofs(), inverse='sparsecholesky')
    a = ng.GridFunction(fes)
    a.Set(amplitude/k*(ng.sin(k*ng.x) + harmonic/2*ng.sin(2*k*ng.x)))
    fixed = ~np.fromiter(fes.FreeDofs(), dtype=bool, count=fes.ndof)
    a.vec.FV().NumPy()[fixed] = 0.0
    b = ng.CF((ng.grad(a)[1], -ng.grad(a)[0], 0))
    display = ng.VectorH1(mesh, order=1)
    trial, test = display.TnT()
    projector = ng.BilinearForm((trial*test)*ng.dx).Assemble().mat.Inverse(display.FreeDofs(), inverse='sparsecholesky')
    load = ng.LinearForm((b*test)*ng.dx)
    b_nodes = ng.GridFunction(display)
    points = mesh(*data['points_m'].T)
    volume = ng.Integrate(1,mesh)
    frames, checks = [], []
    for step in range(65):
        if step % 4 == 0:
            t = step*dt
            load.Assemble()
            b_nodes.vec.data = projector*load.vec
            frames.append((t, np.asarray(b_nodes(points)).reshape(-1,3).copy()))
            exact_y = -amplitude*(ng.cos(k*ng.x)*math.exp(-t/tau)
                                  + harmonic*ng.cos(2*k*ng.x)*math.exp(-4*t/tau))
            error = b - ng.CF((0,exact_y,0))
            e_l2 = math.sqrt(ng.Integrate(error*error,mesh)/ng.Integrate(exact_y**2,mesh))
            energy = ng.Integrate(b*b/(2*mu),mesh)
            reference = volume*amplitude**2/(4*mu)*(math.exp(-2*t/tau)+harmonic**2*math.exp(-8*t/tau))
            checks.append(dict(time_s=t, energy_J=energy, exact_energy_J=reference, relative_B_L2=e_l2))
        if step < 64:
            right = a.vec.CreateVector()
            right.data = rhs.mat*a.vec
            a.vec.data = inverse*right
    if max(c['relative_B_L2'] for c in checks) > .02:
        raise ValueError('The transient field differs from the analytic reference by more than 2%')
    if not all(a['energy_J'] > b['energy_J'] for a,b in zip(checks,checks[1:])):
        raise ValueError('Magnetic energy must decrease during free decay')
    integral = [{'name':'final_magnetic_energy','unit':'J','value':checks[-1]['energy_J']}]
    write_results(destination, data, {'final_B':('T',frames[-1][1])}, integral, series={'B':('T',frames)})
    (Path(destination)/'diffusion-check.json').write_text(json.dumps({'tau_s':tau,'dt_s':dt,'frames':checks},indent=2)+'\n')


if __name__ == '__main__':
    solve(*sys.argv[1:])
