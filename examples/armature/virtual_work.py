"""Discrete magnetic virtual work for a rigid armature with a stationary magnet."""
import numpy as np

from make_model import ENVELOPE, STEEL


def armature_weights(points):
    """P1 nodal extension of unit rigid motion into the surrounding air shell."""
    low = (STEEL[0] - points) / (STEEL[0] - ENVELOPE[0])
    high = (points - STEEL[1]) / (ENVELOPE[1] - STEEL[1])
    weights = np.clip(1 - np.maximum(low, high).max(axis=1), 0, 1)
    weights[np.isclose(weights, 0, rtol=0, atol=1e-12)] = 0
    weights[np.isclose(weights, 1, rtol=0, atol=1e-12)] = 1
    return weights


def rigid_work(points, cells, B, steel, magnet, mu_air, origins):
    """Stationary scalar-potential coenergy derivative for translations and rotations.

    B contains the four vertex traces of the affine (P2 potential) flux density per
    tetrahedron. The virtual displacement is interpolated from the weighted nodal
    rigid motion. Only nonuniform nodal weights in air can perform virtual work.
    """
    weights = armature_weights(points)
    corners = weights[cells]
    if not np.all(corners[steel] == 1) or not np.all(corners[magnet] == 0):
        raise ValueError('Virtual motion must move all steel rigidly and keep the magnet fixed')
    active = np.ptp(corners, axis=1) > 0
    if not active.any() or np.any(active & (steel | magnet)):
        raise ValueError('The deformation shell must lie in air')
    ids = cells[active]
    xyz = points[ids]
    delta = xyz[:, 1:] - xyz[:, :1]
    volumes = np.linalg.det(delta) / 6
    if np.any(volumes <= 0):
        raise ValueError('The air shell contains an inverted or collapsed tetrahedron')
    basis = np.linalg.inv(delta).transpose(0, 2, 1)
    grads = np.concatenate((-basis.sum(axis=1, keepdims=True), basis), axis=1)
    b = B[active]
    # <lambda_i lambda_j> = (1 + delta_ij)/20 in a tetrahedron.
    total = b.sum(axis=1)
    bb = (np.einsum('evi,evj->eij', b, b) + np.einsum('ei,ej->eij', total, total)) / 20
    stress = (bb - np.trace(bb, axis1=1, axis2=2)[:, None, None] * np.eye(3) / 2) / mu_air
    nodal_force = -volumes[:, None, None] * np.einsum('eij,evj->evi', stress, grads)
    work_force = nodal_force * weights[ids, None]
    force = work_force.sum(axis=(0, 1))
    moments = [np.cross(xyz - origin, work_force).sum(axis=(0, 1)) for origin in origins]
    return force, moments
