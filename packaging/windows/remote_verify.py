"""Run verify.ps1 in a disposable per-user directory on one supplied Windows SSH host."""

import argparse
import base64
import json
from pathlib import Path, PureWindowsPath
import re
import subprocess
import uuid

from prepare import digest


def powershell(host, source, *, capture=False):
    encoded = base64.b64encode(("$ErrorActionPreference='Stop'; " + source).encode("utf-16-le")).decode("ascii")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "--", host,
               "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "RemoteSigned", "-EncodedCommand", encoded]
    return subprocess.run(command, check=True, stdout=subprocess.PIPE if capture else None,
                          text=True, encoding="utf-8", errors="replace").stdout


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def sftp_quote(value):
    value = str(value)
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control characters are unsupported in transfer paths")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sftp(host, lines):
    subprocess.run(["sftp", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-b", "-", "--", host],
                   input="\n".join(lines) + "\n", text=True, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="one Windows OpenSSH host/alias with an already trusted host key")
    parser.add_argument("arch", choices=("x64", "arm64"))
    parser.add_argument("installer", type=Path)
    parser.add_argument("proof", type=Path, help="new local JSON report (never overwritten)")
    parser.add_argument("--verify-script", required=True, type=Path, help="verify.ps1 from the pinned FieldCraft installer recipe")
    args = parser.parse_args()
    if not args.host or args.host.startswith("-") or any(c.isspace() for c in args.host):
        parser.error("Specify a single known SSH host or user@host")
    if args.proof.exists() or not args.proof.parent.is_dir():
        parser.error("Choose a new proof filename in an existing folder")
    installer = args.installer.resolve(strict=True)
    expected = installer.with_suffix(".exe.sha256").read_text().split()[0].lower()
    if not re.fullmatch("[0-9a-f]{64}", expected) or digest(installer) != expected:
        raise ValueError("Local installer differs from its SHA-256 sidecar")

    verification = args.verify_script.resolve(strict=True)
    name = "FieldCraft-NGSolve-QA-" + uuid.uuid4().hex
    # Transfer the path as JSON encoded with UTF-8, independent of Windows console code pages.
    encoded_path = powershell(args.host, f"""
        $p = Join-Path ([IO.Path]::GetTempPath()) {literal(name)}
        if (Test-Path -LiteralPath $p) {{ throw 'Scratch directory already exists' }}
        [void](New-Item -ItemType Directory -Path $p)
        [IO.File]::WriteAllText((Join-Path $p 'owner.txt'), {literal(name)})
        [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($p))
    """, capture=True).strip()
    remote = PureWindowsPath(base64.b64decode(encoded_path, validate=True).decode("utf-8"))
    if remote.name != name or not re.fullmatch("[A-Za-z]:", remote.drive):
        raise ValueError(f"Unexpected Windows scratch location: {remote}")
    print(f"Windows verification directory: {remote}", flush=True)
    path = "/" + remote.as_posix()
    sftp(args.host, [f"put {sftp_quote(installer)} {sftp_quote(path + '/installer.exe')}",
                     f"put {sftp_quote(verification)} {sftp_quote(path + '/verify.ps1')}"])
    powershell(args.host, f"""
        $p = {literal(remote)}
        $installer = Join-Path $p 'installer.exe'
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne {literal(expected)}) {{
            throw 'Transferred installer hash mismatch'
        }}
        $env:TEMP = [IO.Directory]::CreateDirectory((Join-Path $p 'temp')).FullName
        $env:TMP = $env:TEMP
        & (Join-Path $p 'verify.ps1') -Installer $installer -Arch {literal(args.arch)} -Work (Join-Path $p 'test')
    """)
    # Write the proof before cleanup; a failed test or interrupted download retains the logs.
    temp = args.proof.with_name(name + ".json")
    try:
        sftp(args.host, [f"get {sftp_quote(path + '/test/verification.json')} {sftp_quote(temp.absolute())}"])
        proof = json.loads(temp.read_text(encoding="utf-8-sig"))
        if proof["archive_sha256"] != expected or proof["install_count"] != 3 or proof["field_imports"] != 2 or not proof["damaged_case_repaired"]:
            raise ValueError("Incomplete report from remote verification")
        with args.proof.open("xb") as output:
            output.write(json.dumps(proof, indent=2).encode("utf-8") + b"\n")
    finally:
        temp.unlink(missing_ok=True)
    powershell(args.host, f"""
        $p = {literal(remote)}
        $owner = [IO.File]::ReadAllText((Join-Path $p 'owner.txt'))
        if ($owner -ne {literal(name)} -or [IO.Path]::GetFileName($p) -ne {literal(name)}) {{
            throw 'Unexpected test directory ownership'
        }}
        Remove-Item -LiteralPath $p -Recurse -Force
    """)
    print(f"Verified {args.arch} package; report: {args.proof}")


if __name__ == "__main__":
    main()
