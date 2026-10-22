# Cilo v0.1.0 prebuilt Windows files

These binaries correspond to source commit `bd021013bafd64832a58b614742d7a9dcac61fd5`
and tag [v0.1.0](https://github.com/IDARETW/Cilo/releases/tag/v0.1.0).

- `cilo.exe`: standalone native Rust archiver.
- `cilo-windows-x64.zip`: archiver plus documentation and license notices.
- `CiloDownloader-windows-x64.zip`: extract the whole folder, then run CiloDownloader.exe.
- `SHA256SUMS.txt`: checksums of all three downloads.

Use the download links on the release page, or open a file here and choose
Download raw file. The repository is private, so sign into GitHub first.

This dedicated binary branch is the distribution fallback because the current
environment can push commits and create releases, but uploads.github.com rejects
its release-asset credentials with HTTP 401. The binaries are preserved here
without adding generated executables to the main source branch.

Windows compatibility checks passed under Wine 10; native Windows testing is
pending. See the source tag's reports/windows-release.md and benchmarks/README.md.
