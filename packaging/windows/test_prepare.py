"""A dependency-only pin must produce a separate install generation."""

import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


class GenerationTest(unittest.TestCase):
    def test_wheel_pin_changes_generation_and_preserves_complete_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            recipe = root / "packaging/windows-online"
            core = root / "core"
            recipe.mkdir(parents=True)
            core.mkdir()
            executable = bytearray(160)
            executable[:2] = b"MZ"
            struct.pack_into("<I", executable, 60, 128)
            struct.pack_into("<4sH", executable, 128, b"PE\0\0", 0x8664)
            names = ("fieldcraft.exe", "fieldcraft-cli.exe")
            for name in names:
                (core / name).write_bytes(executable)
            files = {name: hashlib.sha256(executable).hexdigest() for name in names}
            (recipe / "desktop-files.json").write_text(json.dumps({"x64": files}))
            for name in ("install.ps1", "INSTALL.txt"):
                (recipe / name).write_text("fixture")
            for name in ("SOLVER-NOTICES.txt", "fieldcraft-ngsolve.cmd"):
                (recipe.parent / name).write_text("fixture")
            notice = root / "notice.txt"
            notice.write_text("fixture")
            lock = {"windows": {"wheels": [{"name": "occt", "sha256": "a" * 64}]},
                    "bridge": {"url": "https://example.org/bridge/" + "b" * 40},
                    "linux": {"distinct": "other platform metadata"}}
            generations = []
            for version in ("a", "c"):
                lock["windows"]["wheels"][0]["sha256"] = version * 64
                raw = json.dumps(lock, indent=3).encode("ascii") + b"\r\n"
                (recipe.parent / "runtime-lock.json").write_bytes(raw)
                out = root / ("stage-" + version)
                subprocess.run([sys.executable, str(Path(__file__).with_name("prepare.py")),
                                "stage", "--recipe", str(recipe), "x64", str(core), str(notice), str(out)],
                               check=True)
                identity = json.loads((out / "identity.json").read_bytes())
                self.assertEqual((out / "runtime-lock.json").read_bytes(), raw)
                self.assertEqual(identity["runtime_lock_sha256"], hashlib.sha256(raw).hexdigest())
                self.assertRegex(identity["generation"], "^[a-z2-7]{52}$")
                generations.append(identity["generation"])
            self.assertNotEqual(*generations)


if __name__ == "__main__":
    unittest.main()
