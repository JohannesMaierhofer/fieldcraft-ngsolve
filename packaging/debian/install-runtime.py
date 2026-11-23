"""Install the independently pinned vendor runtime on Debian/Ubuntu amd64."""

import errno
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def fetch(record, folder, allowed_host, name=None):
    url = record['url']
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc != allowed_host:
        raise ValueError(f'Unexpected runtime download URL: {url}')
    filename = name or unquote(parsed.path.rsplit('/', 1)[-1])
    if Path(filename).name != filename or filename in ('', '.', '..'):
        raise ValueError('Invalid download filename')
    asset = folder / filename
    subprocess.run(['curl', '--disable', '--proto', '=https', '--proto-redir', '=https',
                    '--fail', '--location', '--silent', '--show-error', '--retry', '2',
                    '--connect-timeout', '20', '--max-time', '600', '--output', asset, url], check=True)
    if digest(asset) != record['sha256']:
        raise ValueError(f'SHA-256 mismatch for {filename}')
    return asset


def smoke(root):
    python = root / 'python/bin/python3'
    subprocess.run([python, '-I', '-B', '-c', '''
import importlib.metadata as m
from pathlib import Path
import sys
import ngsolve as ng
import netgen
import numpy
from netgen.geom2d import unit_square
assert all(Path(p.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()) for p in (ng, netgen, numpy))
for package, version in [('ngsolve','6.2.2608'),('netgen-mesher','6.2.2608'),('netgen-occt','7.8.1'),('ngsolve-openblas','0.3.33'),('numpy','2.3.5')]:
    assert m.version(package) == version
assert abs(ng.Integrate(1, ng.Mesh(unit_square.GenerateMesh(maxh=.5))) - 1) < 1e-12
'''], check=True)


def prepare(lock, lock_bytes, stage):
    pins = lock['linux']
    tree = stage / 'tree'
    tree.mkdir(mode=0o755)
    runtime = fetch(pins['runtime'], stage, 'github.com')
    subprocess.run(['tar', '-xzf', runtime, '--no-same-owner', '-C', tree], check=True)
    wheels = [fetch(item, stage, 'files.pythonhosted.org') for item in pins['wheels']]
    pip = [tree / 'python/bin/python3', '-I', '-B', '-m', 'pip', '--isolated', '--no-cache-dir', '--disable-pip-version-check']
    subprocess.run([*pip, 'install', '--no-index', '--no-deps', '--no-compile', '--no-warn-script-location', *wheels], check=True)
    subprocess.run([*pip, 'check'], check=True)
    bridge = fetch(lock['bridge'], stage, 'codeload.github.com', 'bridge.zip')
    revision = lock['bridge']['url'].rsplit('/', 1)[-1]
    with zipfile.ZipFile(bridge) as source:
        source.extractall(stage / 'source')
    folder = stage / 'source' / ('fieldcraft-ngsolve-' + revision)
    if not all((folder / script).is_file() for script in ('workflow.py', 'show_run.py')):
        raise ValueError('Bridge archive lacks its entry points')
    folder.rename(tree / 'bridge')
    (tree / 'runtime-lock.json').write_bytes(lock_bytes)
    smoke(tree)
    return tree


def install(lock_path, data_dir):
    if sys.platform != 'linux' or platform.machine() != 'x86_64':
        raise ValueError('This installer requires Linux x86_64')
    os.umask(0o022)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'):
        os.environ.pop(name, None)
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    runtimes = data_dir / 'runtimes'
    runtimes.mkdir(parents=True, exist_ok=True)
    target = runtimes / hashlib.sha256(lock_bytes).hexdigest()
    current = data_dir / 'current'
    if os.path.lexists(current) and not current.is_symlink():
        raise ValueError(f'Unmanaged runtime path: {current}')
    with tempfile.TemporaryDirectory(prefix='.install-', dir=data_dir) as tmp:
        stage = Path(tmp)
        if not target.exists():
            tree = prepare(lock, lock_bytes, stage)
            try:
                tree.rename(target)
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY) or not target.is_dir():
                    raise
        if (target / 'runtime-lock.json').read_bytes() != lock_bytes:
            raise ValueError('Installed runtime lock differs')
        smoke(target)
        link = stage / 'current'
        link.symlink_to(target.relative_to(data_dir))
        link.replace(current)


if __name__ == '__main__':
    install(Path(sys.argv[1]), Path(sys.argv[2]))
