"""Build input for the Windows network installer from pinned CI desktop binaries."""

import argparse
import base64
import hashlib
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import zipfile


CORE = "9def8cf427505a7faf5fc79c014357ce7e170375"
PIN = {
    "x64": ("b09e662144f31af73beba295364b8b7f7b60a38c15cbe912106ecd0b8cc98114",
            "a8bb6bf43f9301f2175d45b0e5c275c728a35975430e942ab6b9a63b89b05bb0", 0x8664),
    "arm64": ("6e33ebdfbb54b0be3b6509535872c995242da73616a8ffa0230b0d9e49f1f1d6",
              "4dc9b7f94bdd535e7d47957576a3eb92e9a5527e34883de2f14f44af05a39124", 0xaa64),
}
LICENSES = ("LICENSE-MIT", "LICENSE-APACHE", "NOTICE", "THIRD-PARTY-LICENSES.txt")


def digest(path):
    with path.open("rb") as stream:
        value = hashlib.sha256()
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
        return value.hexdigest()


def verify(path, expected):
    if digest(path) != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")


def machine(path):
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"Invalid DOS signature in {path}")
        stream.seek(60)
        stream.seek(struct.unpack("<I", stream.read(4))[0])
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"Invalid PE signature in {path}")
        return struct.unpack("<H", stream.read(2))[0]


def legacy(args):
    archive_hash, exe_hash, _ = PIN[args.arch]
    verify(args.archive, archive_hash)
    name = f"fieldcraft-ngsolve-windows-{args.arch}.exe"
    with zipfile.ZipFile(args.archive) as archive:
        if sorted(archive.namelist()) != sorted([name, name + ".sha256"]):
            raise ValueError("Unexpected files in original CI archive")
        if archive.read(name + ".sha256").decode("ascii").split() != [exe_hash, name]:
            raise ValueError("Unexpected inner digest in original CI archive")
        with archive.open(name) as source, args.output.open("xb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
    verify(args.output, exe_hash)


def extract_core(args):
    # Optional build-time dependency. No extractor is installed by the Windows package.
    from refinery.lib.inno.archive import InnoArchive

    verify(args.installer, PIN[args.arch][1])
    names = ["fieldcraft.exe", "fieldcraft-cli.exe"] + ["licenses/" + name for name in LICENSES]
    paths = {"data/{app}/" + name: name for name in names}
    args.output.mkdir(parents=True, exist_ok=False)
    with args.installer.open("rb") as source:
        mapped = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
        archive = InnoArchive(mapped)
        selected = {}
        for entry in archive.files:
            path = str(entry.path).replace("\\", "/")
            if path in paths:
                if path in selected:
                    raise ValueError(f"Multiple entries for {path}")
                selected[path] = entry
        if selected.keys() != paths.keys():
            raise ValueError("Incomplete application files in original installer")
        # Reserve all six ranges first so the solid compressed stream is traversed once.
        readers = {path: archive.extractor(entry) for path, entry in selected.items()}
        for path in sorted(selected, key=lambda key: selected[key].offset):
            dest = args.output / paths[path]
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("xb") as output:
                output.write(archive.check_file(selected[path], readers[path]()))



def stage(args):
    _, _, machine_id = PIN[args.arch]
    recipe = args.recipe.resolve()
    packaging = recipe.parent
    lock = json.loads((packaging / "runtime-lock.json").read_text())
    original = json.loads((recipe / "desktop-files.json").read_text())[args.arch]
    payload = args.output
    payload.mkdir(parents=True, exist_ok=False)
    (payload / "licenses").mkdir()
    for entry, expected in original.items():
        source = args.core / entry
        verify(source, expected)
        if entry.endswith(".exe") and machine(source) != machine_id:
            raise ValueError(f"Wrong PE machine for {source}")
        try:
            os.link(source, payload / entry)
        except OSError:
            shutil.copyfile(source, payload / entry)
    shutil.copyfile(args.nsis_notice, payload / "licenses" / "NSIS.txt")
    for name in ("SOLVER-NOTICES.txt", "fieldcraft-ngsolve.cmd"):
        shutil.copyfile(packaging / name, payload / name)
    for name in ("install.ps1", "INSTALL.txt"):
        shutil.copyfile(recipe / name, payload / name)
    shutil.copyfile(packaging / "runtime-lock.json", payload / "runtime-lock.json")
    identity = {
        "desktop": CORE, "bridge": lock["bridge"]["url"].split("/")[-1], "arch": args.arch,
        "runtime_lock_sha256": digest(payload / "runtime-lock.json"),
        "files": {name: original[name] for name in ("fieldcraft.exe", "fieldcraft-cli.exe")},
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("ascii")
    fingerprint = hashlib.sha256(canonical).digest()
    identity["generation"] = base64.b32encode(fingerprint).decode("ascii").rstrip("=").lower()
    (payload / "identity.json").write_bytes((json.dumps(identity, indent=2) + "\n").encode("ascii"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    extract = commands.add_parser("legacy", help="verify and unwrap a pinned original CI archive")
    extract.add_argument("arch", choices=PIN)
    extract.add_argument("archive", type=Path)
    extract.add_argument("output", type=Path)
    core = commands.add_parser("core", help="extract only six application files with binary-refinery 0.11.3")
    core.add_argument("arch", choices=PIN)
    core.add_argument("installer", type=Path)
    core.add_argument("output", type=Path)
    prepare = commands.add_parser("stage", help="stage verified app binaries, metadata and notices")
    prepare.add_argument("arch", choices=PIN)
    prepare.add_argument("--recipe", required=True, type=Path, help="FieldCraft packaging/ngsolve/windows-online directory")
    prepare.add_argument("core", type=Path, help="extracted {app} folder of the original installer")
    prepare.add_argument("nsis_notice", type=Path, help="copyright and license notice from the NSIS distribution")
    prepare.add_argument("output", type=Path)
    args = parser.parse_args()
    {"legacy": legacy, "core": extract_core, "stage": stage}[args.command](args)


if __name__ == "__main__":
    main()
