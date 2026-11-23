"""Independent checks of rigid magnetic virtual work on the armature mesh."""
import argparse
import json
import tempfile
from pathlib import Path

import ngsolve as ng
import numpy as np

from fc_results import load_mesh
from magnet import CENTER, MU0, PIVOT, STEEL, solve_potential
from virtual_work import armature_weights, rigid_work


def dipole(source):
    with np.load(source, allow_pickle=False) as data:
        xyz, cells = data['points_m'], data['tetrahedra']
        labels = data['material_names'][data['material_ids'] - 1]
    moment = np.array([0.001, 0, 0])  # A m²
    background = np.array([0, 0, 0.1])  # T at CENTER
    gradient = np.diag([50., -50., 0.])  # T/m, curl-free and divergence-free
    r = xyz - CENTER
    norm = np.linalg.norm(r, axis=1)
    use = norm > 1e-12  # The only singular point is inside the rigid armature.
    unit = r[use] / norm[use, None]
    field = background + r @ gradient.T
    field[use] += MU0 / (4*np.pi*norm[use, None]**3) * (3*(unit @ moment)[:, None]*unit - moment)
    force, (side, center) = rigid_work(xyz, cells, field[cells], labels == 'steel', labels == 'magnet', MU0, (PIVOT, CENTER))
    exact_force = gradient.T @ moment
    exact_center = np.cross(moment, background)
    exact_side = exact_center + np.cross(CENTER-PIVOT, exact_force)
    def relative(a, b):
        return float(np.linalg.norm(a-b) / np.linalg.norm(b))

    return {'tetrahedra': len(cells), 'force_N': force.tolist(), 'exact_force_N': exact_force.tolist(),
            'moment_center_Nm': center.tolist(), 'exact_center_Nm': exact_center.tolist(),
            'moment_side_Nm': side.tolist(), 'exact_side_Nm': exact_side.tolist(),
            'relative_force_error': relative(force, exact_force), 'relative_center_moment_error': relative(center, exact_center),
            'relative_side_moment_error': relative(side, exact_side)}


def differences(run):
    """Re-solve actually moved meshes; leave outer boundary and magnet fixed."""
    result = json.loads((run/'results.json').read_text())
    work = {x['name']: np.asarray(x['value']) for x in result['integrals']}
    with np.load(run/'mesh.npz', allow_pickle=False) as archive:
        data = dict(archive)
    xyz = data['points_m']
    weights = armature_weights(xyz)
    ids = data['tetrahedra']
    labels = data['material_names'][data['material_ids'] - 1]
    if (np.any(weights[ids[labels == 'steel']] != 1) or np.any(weights[ids[labels == 'magnet']] != 0)
            or np.any(weights[data['triangles']] != 0)):
        raise ValueError('The moving armature, fixed magnet or outer wall has an invalid virtual motion')
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'perturbed.npz'
        def energy(key, origin, axis, h):
            if key == 'translation':
                displacement = h * axis
            else:
                # Rodrigues rotation of every steel node. Only the air transition is blended.
                r = xyz - origin
                displacement = np.sin(h)*np.cross(axis, r) + 2*np.sin(h/2)**2*np.cross(axis, np.cross(axis, r))
            moved = xyz + weights[:, None] * displacement
            tet = moved[ids]
            if np.any(np.linalg.det(tet[:, 1:] - tet[:, :1]) <= 0):
                raise ValueError('Virtual displacement inverted a tetrahedron')
            np.savez(path, **{**data, 'points_m': moved})
            mesh, _ = load_mesh(path)
            phi, mu, br, residual = solve_potential(mesh)
            g = ng.grad(phi)
            return float(ng.Integrate(.5*mu*g*g - br*g[0], mesh)), float(residual)
        for key, origin, integral, step in [
            ('translation', CENTER, 'virtual_work_force_armature', 20e-6),
            ('rotation_center', CENTER, 'virtual_work_torque_about_armature_center', .004),
            ('rotation_side', PIVOT, 'virtual_work_torque_about_side_point', .004),
        ]:
            for h in (step, step/2):
                pairs, residuals = [], []
                for axis in np.eye(3):
                    minus, r_minus = energy(key, origin, axis, -h)
                    plus, r_plus = energy(key, origin, axis, h)
                    pairs.append([minus, plus])
                    residuals.extend((r_minus, r_plus))
                estimated = np.diff(pairs, axis=1).ravel()/(2*h)
                target = work[integral]
                error = np.linalg.norm(estimated-target)
                norm = np.linalg.norm(target)
                # A central moment may be zero by symmetry. Normalize its absolute
                # error by a mechanical torque scale in that case and keep raw SI values.
                scale = np.linalg.norm(work['virtual_work_force_armature'])
                if key != 'translation':
                    scale *= np.max(STEEL[1]-STEEL[0])
                scaled_error = float(error / max(scale, norm))
                rows.append({'motion':key,'step':h,'step_unit':'m' if key=='translation' else 'rad',
                             'quantity_unit':'N' if key=='translation' else 'N m', 'origin_m':origin.tolist(),
                             'stationary_coenergy_pairs_J':pairs,'central_derivative':estimated.tolist(),
                             'virtual_work':target.tolist(),'absolute_vector_error':float(error),
                             'relative_vector_error':float(error/norm) if norm else None,
                             'scaled_error':scaled_error, 'max_solver_residual':max(residuals)})
            if rows[-1]['scaled_error'] >= 1e-3 or rows[-1]['scaled_error'] > max(rows[-2]['scaled_error']/2, 1e-8):
                raise ValueError(f'{key} failed the step-halving check at fixed excitation')
    return {'model_sha256':result['model_sha256'], 'mesh_sha256':result['mesh_sha256'],
            'perturbed_solves':36, 'coenergy_sign':'positive derivative at fixed excitation', 'rows':rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='check', required=True)
    sub.add_parser('dipole').add_argument('meshes', type=Path, nargs='+')
    sub.add_parser('differences').add_argument('run', type=Path)
    args = parser.parse_args()
    result = [dipole(mesh) for mesh in args.meshes] if args.check == 'dipole' else differences(args.run)
    if args.check == 'dipole' and len(result) > 1:
        for coarse, fine in zip(result, result[1:]):
            if fine['tetrahedra'] <= coarse['tetrahedra']:
                raise ValueError('Supply dipole meshes in coarse-to-fine order')
            for measure in ('relative_force_error', 'relative_center_moment_error', 'relative_side_moment_error'):
                if fine[measure] >= coarse[measure]:
                    raise ValueError(f'Analytic dipole {measure} failed to decrease on refinement')
    print(json.dumps(result, indent=2, allow_nan=False))
