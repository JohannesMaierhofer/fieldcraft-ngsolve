"""Exercise activation and repair with a tiny local archive and a real Python subprocess."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('install_runtime', Path(__file__).with_name('install-runtime.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class RuntimeRepair(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name).resolve()
        self.runtimes = self.data / 'runtimes'
        self.lock = {'bridge': {'url': 'https://codeload.github.com/owner/project/zip/abcd'}}
        self.lock_bytes = json.dumps(self.lock).encode()
        self.target = self.runtimes / hashlib.sha256(self.lock_bytes).hexdigest()
        self.bridge = self.target / 'bridge'
        self.bridge.mkdir(parents=True)
        binary = self.target / 'python/bin/python3'
        binary.parent.mkdir(parents=True)
        binary.symlink_to(sys.executable)
        (self.target / 'runtime-lock.json').write_bytes(self.lock_bytes)
        self.current = self.data / 'current'
        self.older = self.runtimes / 'previous'
        self.older.mkdir()
        self.current.symlink_to(self.older)
        archive = self.data / 'bridge.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for name in installer.REQUIRED_MODULES:
                content = 'import case_run\n' if name == 'workflow.py' else ''
                z.writestr('fieldcraft-ngsolve-abcd/' + name, content)
                (self.bridge / name).write_text(content)
        self.fetch = self.enterContext(patch.object(installer, 'fetch', return_value=archive))
        self.enterContext(patch.object(installer, 'smoke'))
        self.custom = self.bridge / 'local.txt'
        self.custom.write_text('preserve')

    def activate(self):
        installer.activate(self.lock, self.lock_bytes, self.data, self.runtimes)

    def test_missing_case_and_workflow_are_repaired_then_reused_offline(self):
        for name in ('workflow.py', 'case_run.py'):
            (self.bridge / name).unlink()
        self.activate()
        self.fetch.assert_called_once()
        self.assertEqual(self.current.resolve(), self.target)
        self.assertEqual((self.bridge / 'workflow.py').read_text(), 'import case_run\n')
        self.assertTrue((self.bridge / 'case_run.py').is_file())
        self.assertEqual(self.custom.read_text(), 'preserve')
        self.assertTrue(self.older.is_dir())
        self.activate()
        self.fetch.assert_called_once()

    def test_failed_repair_preserves_old_active_and_local_edits(self):
        (self.bridge / 'case_run.py').unlink()
        (self.bridge / 'show_run.py').write_text('local change')
        with self.assertRaisesRegex(ValueError, 'Current runtime unchanged'):
            self.activate()
        self.assertEqual(self.current.resolve(), self.older)
        self.assertFalse((self.bridge / 'case_run.py').exists())
        self.assertEqual((self.bridge / 'show_run.py').read_text(), 'local change')
        self.assertEqual(self.custom.read_text(), 'preserve')

    def test_foreign_current_is_preserved(self):
        self.current.unlink()
        self.current.symlink_to(self.data / 'foreign')
        with self.assertRaisesRegex(ValueError, 'Unmanaged runtime path'):
            self.activate()
        self.assertEqual(self.current.readlink(), self.data / 'foreign')
        self.fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
