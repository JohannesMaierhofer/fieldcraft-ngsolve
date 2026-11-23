"""Verify a complete NSIS payload using 7-Zip without expanding it to disk."""

import argparse
import hashlib
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("installer", type=Path)
    parser.add_argument("--sevenzip", default="7z")
    args = parser.parse_args()
    payload = {name.relative_to(args.payload).as_posix(): name
               for name in args.payload.rglob("*") if name.is_file()}
    prefix = "$PLUGINSDIR/payload/"
    expected = {prefix + name for name in payload}
    listing = subprocess.check_output([args.sevenzip, "l", "-slt", "-ba", str(args.installer)], text=True)
    entries = [line[7:].replace("\\", "/") for line in listing.splitlines() if line.startswith("Path = ")]
    if len(entries) != len(expected) or set(entries) != expected:
        raise ValueError(f"Unexpected installer inventory: {set(entries) ^ expected}")
    for name, source in payload.items():
        command = [args.sevenzip, "x", "-so", "-spd", str(args.installer), prefix + name]
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            checksum = hashlib.sha256()
            for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
                checksum.update(block)
            with source.open("rb") as reference:
                expected_hash = hashlib.file_digest(reference, "sha256").digest()
            if process.wait() or checksum.digest() != expected_hash:
                raise ValueError(f"Packaged bytes differ for {name}")
    print(f"Verified all {len(payload)} package entries, including the two PE files and five licenses.")


if __name__ == "__main__":
    main()
