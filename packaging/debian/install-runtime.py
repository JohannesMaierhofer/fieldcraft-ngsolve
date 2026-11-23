"""Install the independently pinned vendor runtime on Debian/Ubuntu amd64."""

import fcntl
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


def fetch_bridge(lock, stage):
    bridge = fetch(lock['bridge'], stage, 'codeload.github.com', 'bridge.zip')
    revision = lock['bridge']['url'].rsplit('/', 1)[-1]
    with zipfile.ZipFile(bridge) as source:
        source.extractall(stage / 'source')
    folder = stage / 'source' / ('fieldcraft-ngsolve-' + revision)
    return folder


REQUIRED_MODULES = ('workflow.py', 'case_run.py', 'show_run.py', 'fc_results.py', 'mesh_exchange.py',
                    'result_exchange.py', 'planar_exchange.py', 'time_series.py', 'interior.py')


def check_bridge(root, bridge):
    for name in REQUIRED_MODULES:
        module = bridge / name
        if module.is_symlink() or not module.is_file():
            raise ValueError(f'Missing or unmanaged bridge module: {module}')
    python = root / 'python/bin/python3'
    for args in ([bridge / 'workflow.py', 'case', '--help'], [bridge / 'show_run.py', '--help']):
        subprocess.run([python, '-E', '-s', '-B', *args], stdout=subprocess.DEVNULL, check=True)


def check_or_repair_bridge(root, lock, stage):
    bridge = root / 'bridge'
    if bridge.is_symlink() or (bridge.exists() and not bridge.is_dir()):
        raise ValueError(f'Unmanaged bridge path: {bridge}. Current runtime unchanged.')
    try:
        check_bridge(root, bridge)
        return
    except (ValueError, subprocess.CalledProcessError):
        pass
    fresh = fetch_bridge(lock, stage)
    check_bridge(root, fresh)
    if not bridge.exists():
        fresh.rename(bridge)
        return
    missing = []
    for src in sorted(fresh.glob('*.py')):
        dst = bridge / src.name
        if not os.path.lexists(dst):
            missing.append((src, dst))
        elif dst.is_symlink() or not dst.is_file() or digest(src) != digest(dst):
            raise ValueError(f'Existing bridge file differs from its pinned archive: {dst}. '
                             'Current runtime unchanged. Back up the differing file and retry configuration.')
    # Install only missing modules from the verified archive, without changing local extras.
    installed = []
    try:
        for src, dst in missing:
            os.link(src, dst)
            installed.append(dst)
        check_bridge(root, bridge)
    except (OSError, ValueError, subprocess.CalledProcessError):
        for dst in installed:
            dst.unlink()
        raise


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
    fetch_bridge(lock, stage).rename(tree / 'bridge')
    (tree / 'runtime-lock.json').write_bytes(lock_bytes)
    smoke(tree)
    check_bridge(tree, tree / 'bridge')
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
    if runtimes.is_symlink():
        raise ValueError(f'Unmanaged runtime directory: {runtimes}')
    runtimes.mkdir(parents=True, exist_ok=True)
    # Serialize checks and repairs by package configuration processes on this filesystem.
    lock_fd = os.open(runtimes, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        activate(lock, lock_bytes, data_dir, runtimes)
    finally:
        os.close(lock_fd)


def activate(lock, lock_bytes, data_dir, runtimes):
    target = runtimes / hashlib.sha256(lock_bytes).hexdigest()
    current = data_dir / 'current'
    if os.path.lexists(current):
        if not current.is_symlink() or current.resolve().parent != runtimes.resolve():
            raise ValueError(f'Unmanaged runtime path: {current}')
    if target.is_symlink():
        raise ValueError(f'Unmanaged runtime generation: {target}')
    with tempfile.TemporaryDirectory(prefix='.install-', dir=data_dir) as tmp:
        stage = Path(tmp)
        if target.exists():
            marker = target / 'runtime-lock.json'
            if marker.is_symlink() or not marker.is_file() or marker.read_bytes() != lock_bytes:
                raise ValueError(f'Unmanaged runtime generation: {target}')
            smoke(target)
            check_or_repair_bridge(target, lock, stage)
        else:
            prepare(lock, lock_bytes, stage).rename(target)
        link = stage / 'current'
        link.symlink_to(target.relative_to(data_dir))
        link.replace(current)


if __name__ == '__main__':
    install(Path(sys.argv[1]), Path(sys.argv[2]))
