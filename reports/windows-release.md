# Windows release validation — 2026-10-22

The `v0.1.0` release includes a native x64 Rust archiver and a portable x64 Python
downloader GUI. Both were produced locally because this repository's GitHub
Actions runs fail before starting jobs. No native Windows or CI pass is claimed.

The Rust executable uses Rust 1.90.0 and LLVM-MinGW MSVCRT 20261020. Its six
applicable tests, 33-file real-corpus round trip, and Linux/Windows archive
interoperability passed under Wine 10; details are in the benchmark report.

The GUI uses Windows CPython 3.12.10, Tk 8.6 and PyInstaller 6.16.0, running under
Wine 10 with Xvfb. Build dependencies are pinned in
`scripts/windows-build-requirements.txt`; the normal Windows build command is
`scripts/build-downloader-windows.ps1`. The official Python installer was fetched
over HTTPS and has SHA-256
`67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`.
Its embedded x64 MSI components were administratively extracted inside the Wine
prefix because the bundled 32-bit setup launcher could not run in this prefix.
No game executable or downloaded driver was executed.

Validation:

- All **33 Python tests passed** on Linux Python 3.12 and Windows Python 3.12.10
  under Wine, including interrupted transfers, hash failures, archive fallback,
  catalog identity/timestamps, atomic cache replacement, cleanup, and auth handling.
- The frozen EXE's `--self-test` instantiated the GUI, loaded all **35,536** bundled
  records, displayed 315 Odin rows, and loaded 17 historical retail endpoint
  variants. The frozen `--cli --help` dispatch also passed.
- The frozen EXE extracted a complete BLTE/TVFS fixture through a local HTTP
  server. Its 380 decoded bytes matched independently, SHA-256
  `eeec89f31bed11d8d5bafcac73ff7d8ae3551d996a130b2d549ef1c20b8c652a`.
- The Linux GUI's actual GitHub-update button fetched the private repository's
  35,536-record cache with an existing login. Search and exact-row selection passed.
- Success/failure probes and failed cache replacements leave no task-owned
  temporary files in the tested cases. The one-directory EXE bundle avoids
  per-launch runtime extraction. Deliberate downloads remain in their chosen folder.

The release's `SHA256SUMS.txt` covers the distributed assets. The GUI ZIP includes
its Python/Tk/PyInstaller notices; native dependency notices accompany the archiver.
Copy the entire GUI directory, including `_internal`, when moving it.
