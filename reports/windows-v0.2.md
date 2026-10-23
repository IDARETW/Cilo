# Cilo 0.2 Windows release validation — 2026-10-23

The release includes the native x64 Rust archiver and a portable downloader GUI
with the native archiver bundled beside it. The GUI supports direct archive
downloads and file listing/details/text/hex previews without extraction.

Builds were produced locally using Rust 1.90.0, LLVM-MinGW MSVCRT 20261020,
Windows CPython 3.12.10, Tk 8.6 and PyInstaller 6.16.0. The same pinned toolchain
and Python installation described in [the original release report](windows-release.md)
were reused. Tests ran on Linux and under Wine 10 with Xvfb. **No native Windows
hardware test or successful GitHub Actions run is claimed.**

## Validation performed

- Linux: formatting, Clippy with warnings denied, **17 Rust tests**, **38 Python
  tests**, and **3 Node tests** passed. The Python archive tests used the real
  native executable and a local HTTP CDN fixture, not a mocked compressor.
- Windows under Wine: all **16 applicable Rust tests** and all **38 Python tests**
  passed. The omitted Rust test exercises Unix executable bits and symlinks.
  Tests cover format transforms, malformed containers, archive corruption,
  v1 compatibility, deterministic parallel encoding, bounded previews,
  stream termination, duplicate/conflicting paths and cleanup after cancellation.
- The final Linux binary reproduced the measured six-build max archive size of
  **237,290,139 bytes**. The Windows binary restored its **66 real game files**;
  all independently matched their source SHA-256 hashes. A Windows-created DLL
  archive restored correctly on Linux. Binary hashes and outcomes are recorded
  in [final validation data](../benchmarks/refinement-final-validation.json).
- The frozen GUI's `--self-test` loaded **35,536** catalog records, displayed
  **315** Odin rows, and loaded **17** historical retail endpoint variants. It
  used its bundled native executable to stream a file into an archive, read
  metadata, display the actual browser's text preview, and verify the archive.
- The frozen `--cli` downloaded a two-file BLTE/TVFS fixture through local HTTP
  straight into a `.cilo` archive using its bundled Rust executable. The Linux
  reader recovered both files byte-for-byte; only the completed archive remained.
- A separate live CDN test streamed three real game files directly into an
  archive, verified it, and compared every file against earlier independent
  SHA-256 results. See [compression results](../benchmarks/REFINEMENT.md).

Game executables and libraries were inspected as data only. No game binary was
run. The distributed ZIP contains the whole GUI directory, native `cilo.exe`,
archive/downloader instructions and Python/Tk/PyInstaller/native dependency
notices. Keep `_internal` and `cilo.exe` beside `CiloDownloader.exe`.

The release `SHA256SUMS.txt` covers all distributed binaries/ZIPs. Build on a
normal Windows installation with `cargo build --release --locked` followed by
`scripts/build-downloader-windows.ps1`. CI workflows now build the native release
before running the Python/native integration tests.
