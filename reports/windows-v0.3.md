# Cilo 0.3 release validation — 2026-10-23

The Windows bundle includes the native Rust archiver and the new Odin collection
GUI/CLI, SQLite runtime, catalog, endpoint map, guides and dependency notices.
The `.cilo` format remains v2 with v1/v2 read compatibility.

The builds use Rust 1.90.0, LLVM-MinGW MSVCRT 20261020, Windows CPython 3.12.10,
Tk 8.6 and PyInstaller 6.16.0. Tests ran on Linux and Windows executables under
Wine 10/Xvfb. No native Windows hardware test or successful hosted Actions run
is claimed.

- Linux: formatting, Clippy with warnings denied, 18 Rust tests and 50 Python
  tests passed. The 11 collection tests use real SQLite, local HTTP and native
  archives. They exercise global object reuse, resume, crash recovery, corrupt
  payloads and volumes, cache import, offline restoration, optional routing
  metadata, byte budgets, cancellation checks, named previews, archive-range
  fallback and temporary-file cleanup.
- Windows/Wine: 17 applicable Rust tests passed (the Unix permissions/symlink
  case is excluded). All 50 Python tests passed, including the complete
  11-test collection module and its archive-range fallback case. ENCODING parser tests include page
  integrity, alternative EKeys and decoded sizes above 4 GiB.
- The frozen application's self-test loaded 35,536 catalog rows and 17 retail
  endpoints, opened the archive browser and collection window, verified a native
  archive preview, and ran the packaged collection worker through the GUI.
- A separate frozen executable test used its bundled native binary to audit a
  local HTTP/BLTE/TVFS fixture, archive it, verify every volume, preview a named
  file and restore the encoded build after all HTTP routes were removed.
  [Machine-readable result](collection-2026-10-23/frozen-windows-validation.json).
- A bounded live-data trial retained 48 distinct encoded objects across six
  retail builds plus a Brynhildr companion. All volumes passed native readback.
  The companion restored offline and its extracted `brynhildr.sys` matched the
  previously recorded SHA-256. Six retail builds deliberately remain partial.
  [Trial evidence](collection-2026-10-23/bounded-real-trial.json).

The Windows tests caught and corrected a file-sync issue: Python/Windows needs
an appropriately writable handle for `fsync`. Test fixture databases now close
before temporary directories are deleted, which Windows enforces strictly.
No downloaded game executable or driver was executed.

Release binaries are checksummed. If GitHub's attachment upload endpoint rejects
uploads in this environment, the release notes link to immutable files committed
on a dedicated release-binaries branch; those downloads are independently
fetched and compared with the published SHA256SUMS file before delivery.
