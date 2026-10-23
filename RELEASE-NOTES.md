Cilo 0.2 adds lossless format-aware compression and direct CASC-to-archive downloads, plus a GUI archive browser.

- Validated PE, FF/FP, SAB, XPAK and Bink layouts guide chunking. Executable branch and numeric byte filters are reversible and used only when their compressed block is smaller. Unknown layouts retain generic compression.
- Whole-file deduplication and a bounded compression pipeline improve repeated-build ingestion. Default blocks are now 64 MiB.
- Enable **Download directly to a .cilo archive** in the downloader, or use `extract/mirror --archive FILE`. Failed or cooperatively cancelled downloads discard their incomplete archive and staging files; archive downloads are not resumable.
- **Browse a .cilo archive…** shows file details and text/hex previews without extraction. Native commands: `contents`, `preview`, `cat`, and `pack-stream`.
- Writes v2 archives; reads v1 and v2. Keep Cilo 0.2 with new archives: Cilo 0.1 cannot read v2.

### Downloads

`CiloDownloader-windows-x64.zip` includes the GUI, Python runtime, and native `cilo.exe`. Extract the entire folder and keep `_internal` and `cilo.exe` beside `CiloDownloader.exe`. `cilo.exe` / `cilo-windows-x64.zip` are the standalone native tool; `cilo-linux-x64` is the Linux executable. `SHA256SUMS.txt` covers all four assets.

### Measured results

A selected 1,152.38 MiB sample spanning six related Odin builds becomes **226.30 MiB (80.36% saved)** in max mode, at **48.2 MiB/s pack / 1,069.9 MiB/s restore**. Balanced gives **228.32 MiB** at **171.1 / 1,188.3 MiB/s**. These are medians of three Linux runs with four workers.

Most savings come from unchanged files across builds; **0.1 already saved 80.34% on this same sample**. The single-build sample saves only **7.44%**. The DLL-only archive improves by 4.33%; max mode is slower overall and some formats regress. This is not a whole-game benchmark or a claim of 80% savings on a single build.

[Full before/after results and provenance](https://github.com/IDARETW/Cilo/blob/v0.2.0/benchmarks/REFINEMENT.md) · [Archive/download usage](https://github.com/IDARETW/Cilo/blob/v0.2.0/docs/ARCHIVES.md) · [Format modules](https://github.com/IDARETW/Cilo/blob/v0.2.0/docs/GAME_FORMATS.md) · [Windows validation](https://github.com/IDARETW/Cilo/blob/v0.2.0/reports/windows-v0.2.md)

Validation: 17 Rust / 38 Python / 3 Node tests passed on Linux; 16 applicable Rust / 38 Python tests passed under Wine. The frozen GUI's archive creation/listing/preview/verification test and frozen downloader's HTTP-to-archive test passed. All 66 real files restored across Linux/Windows binaries matched SHA-256. Windows binaries were built locally and tested under Wine 10; native Windows hardware validation remains pending. Game executables were never run.
