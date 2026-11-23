# Windows network installer build and checks

These host-side Python 3.11+ tools accompany the `packaging/ngsolve/windows-online` recipe in the FieldCraft installer repository. The FieldCraft recipe documents its compatible helper revision, original desktop artifact IDs, required NSIS license notice and exact commands. The installer uses its separate locked bridge revision for numerical calculations.

- `prepare.py legacy` checks the original CI ZIP, its recorded checksum and the embedded offline installer before exposing the latter for extraction.
- `prepare.py core` selects just the two FieldCraft application executables and their licenses. This optional extraction step uses binary-refinery 0.11.3 solely on the packaging host.
- `prepare.py stage --recipe <windows-online-dir>` reads the recipe and shared runtime lock, validates the six original application files and their PE platforms, and assembles the input for NSIS. The byte-exact complete runtime lock is hashed into the generation identity.
- `verify_payload.py <stage> <installer.exe>` checks the complete packaged inventory and content hashes with 7-Zip.
- `remote_verify.py <known-ssh-host> <arch> <installer.exe> <new-report.json> --verify-script <windows-online-dir>/verify.ps1` transfers the verified installer and the recipe's Windows acceptance script to an isolated Windows temporary directory. It requires a preconfigured SSH account and trusted host key.

Run `python test_prepare.py` to verify that changing only one wheel digest creates a different generation while retaining the full lock bytes.

Run any tool with `--help` for its arguments. The installer does not embed or invoke these build-host utilities.
