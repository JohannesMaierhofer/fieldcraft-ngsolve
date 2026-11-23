"""Integration checks for native tetrahedron labels, SI tags and reordered VTU vertices."""

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

from mesh_exchange import export_mesh
from result_exchange import read_field


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.native = self.root / "unit.fieldcraft"
        self.npz = self.root / "unit.npz"
        self.vtu = self.root / "unit.vtu"
        self.ids = np.array([2, 9, 12, 23])
        self.xyz = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
        mesh = b"MCMESH02" + struct.pack("<5Q", 4, 1, 0, 0, 0)
        for label, xyz in zip(self.ids, self.xyz):
            mesh += struct.pack("<I3d", label, *xyz)
        mesh += struct.pack("<IB4I", 7, 7, *self.ids)
        sets = {
            "volume": {"elements": [7]},
            "a": {"faces": [{"element": 7, "face": 1}]},
            "b": {"faces": [{"element": 7, "face": i} for i in (2, 3, 4)]},
        }
        payloads = {"model.json": json.dumps({"sets": sets}).encode(), "mesh.bin": mesh}
        parts = {
            k: {"bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
            for k, v in payloads.items()
        }
        with zipfile.ZipFile(self.native, "w") as f:
            f.writestr(
                "fieldcraft.json",
                json.dumps({"format": "fieldcraft/3", "parts": parts}),
            )
            for name, data in payloads.items():
                f.writestr(name, data)
        export_mesh(self.native, self.npz, ["a", "b"], ["volume"])

    def test_optional_loads_preserve_previous_manifests_and_validate_labels(self):
        from fc_results import validate_loads, write_results
        with np.load(self.npz,allow_pickle=False) as data:
            output=self.root / "solver"
            fields={"u":("V",np.zeros(len(data["node_ids"])))}
            write_results(output,data,fields,[])
            manifest=output/"results.json"
            self.assertNotIn("loads", json.loads(manifest.read_text()))
            write_results(output,data,fields,[],loads=["left electrode (1 V)","right electrode (0 V)"])
            self.assertEqual(json.loads(manifest.read_text())["loads"],
                             ["left electrode (1 V)","right electrode (0 V)"])
        validate_loads([])
        validate_loads(["ü"*60])
        validate_loads([str(i) for i in range(4096)])
        for invalid in (None, "left", ["left","left"], [""], [" "], [True], ["a\nb"], ["ü"*61],
                        [str(i) for i in range(4097)]):
            with self.subTest(invalid=type(invalid)), self.assertRaises(ValueError):
                validate_loads(invalid)

    def test_workflow_rejects_malformed_loads_from_a_plain_python_script(self):
        from argparse import Namespace
        import sys
        from workflow import run
        script=self.root / "malformed.py"
        script.write_text("import json,sys,numpy as np\nfrom pathlib import Path\n"
                          "with np.load(sys.argv[1]) as data: digest=str(data['mesh_sha256'].item())\n"
                          "record={'mesh_sha256':digest,'fields':[],'integrals':[], 'loads':['a','a']}\n"
                          "(Path(sys.argv[2])/'results.json').write_text(json.dumps(record))\n")
        output=self.root/"bad-run"
        args=Namespace(model=self.native,script=script,python=sys.executable,dimension=3,
                       boundaries=["a","b"],materials=["volume"],interfaces=[],
                       adapter=Path(__file__).parent,out=output,timeout=30)
        with self.assertRaisesRegex(ValueError,"duplicate load"):
            run(args)
        self.assertFalse(output.exists())

    def write_vtu(self, labels=None):
        # Original IDs are retained although the solver's vertex rows have been reordered.
        permutation = np.array([2, 0, 3, 1])
        labels = self.ids[permutation] if labels is None else labels
        root = ET.Element("VTKFile", type="UnstructuredGrid")
        piece = ET.SubElement(
            ET.SubElement(root, "UnstructuredGrid"),
            "Piece",
            NumberOfPoints="4",
            NumberOfCells="1",
        )

        def array(p, name, dtype, values, components=1):
            a = ET.SubElement(
                p,
                "DataArray",
                Name=name,
                type=dtype,
                format="ascii",
                NumberOfComponents=str(components),
            )
            a.text = " ".join(map(str, np.asarray(values).ravel()))

        coords = self.xyz[permutation]
        data = ET.SubElement(piece, "PointData")
        array(data, "fieldcraft_node_id", "Int64", labels)
        array(data, "position", "Float64", coords, 3)
        array(data, "height", "Float64", coords[:, 2])
        array(ET.SubElement(piece, "Points"), "points", "Float64", coords, 3)
        cells = ET.SubElement(piece, "Cells")
        array(cells, "connectivity", "Int32", np.argsort(permutation))
        array(cells, "types", "Int32", [10])
        array(cells, "offsets", "Int32", [4])
        ET.ElementTree(root).write(self.vtu)

    def test_import_scalar_and_vector_with_permuted_vertices(self):
        self.write_vtu()
        with np.load(self.npz) as data:
            np.testing.assert_array_equal(data["node_ids"], self.ids)
            np.testing.assert_array_equal(data["points_m"], self.xyz)
            np.testing.assert_array_equal(data["boundary_ids"], [1, 2, 2, 2])
        scalar = read_field(self.vtu, self.npz, "height", "m")
        self.assertEqual(scalar["values"], [0, 0, 0, 1])
        vector = read_field(self.vtu, self.npz, "position", "m")
        self.assertEqual(vector["values"], self.xyz.ravel().tolist())

    def test_mismatched_ids_and_partial_boundary_are_rejected(self):
        self.write_vtu([11, 2, 23, 9])
        with self.assertRaisesRegex(ValueError, "node labels"):
            read_field(self.vtu, self.npz, "height", "m")
        with self.assertRaisesRegex(ValueError, "lack a group"):
            export_mesh(self.native, self.npz, ["a"], ["volume"])


if __name__ == "__main__":
    unittest.main()
