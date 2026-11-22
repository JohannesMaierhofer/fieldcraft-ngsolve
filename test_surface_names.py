"""Check that material adjacency never changes the name used for a surface integral."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import ngsolve as ng
import numpy as np

from fc_results import load_mesh


class SurfaceNames(unittest.TestCase):
    def test_shared_names_on_different_material_pairs(self):
        # Two triangular bipyramids, each with one internal interface. Give both
        # interfaces one name, and group exterior faces across all four volumes.
        block = np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[0,0,-1]], dtype=float)
        points = np.vstack((block, block + [3,0,0]))
        cells = np.array([[0,1,2,3], [1,0,2,4], [5,6,7,8], [6,5,7,9]])
        common = np.array([[0,2,1],[5,7,6]])
        exterior = []
        labels = []
        for tet in cells:
            for opposite in (0,1,2):
                exterior.append(np.delete(tet,opposite))
                labels.append(1 if opposite < 2 else 2)
        triangles = np.array(exterior)
        labels = np.array(labels)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'mesh.npz'
            np.savez(path, points_m=points, tetrahedra=cells, triangles=triangles,
                     material_names=['a','b','c','d'], material_ids=[1,2,3,4],
                     boundary_names=['side','cap'], boundary_ids=labels,
                     interface_names=['seam'], interface_ids=np.ones(2,int),
                     interface_triangles=common, interface_owner=np.array([0,2]),
                     interface_neighbor=np.array([1,3]))
            mesh, _ = load_mesh(path)
        for name, faces in [('side',triangles[labels==1]), ('cap',triangles[labels==2]), ('seam',common)]:
            with self.subTest(name=name):
                a,b,c = points[faces].transpose(1,0,2)
                area = np.linalg.norm(np.cross(b-a,c-a), axis=1).sum()/2
                self.assertAlmostEqual(ng.Integrate(1,mesh.Boundaries(name)), area, places=12)
        self.assertEqual(set(mesh.GetBoundaries()), {'side','cap','seam'})
