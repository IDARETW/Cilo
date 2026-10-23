# Downloading and browsing Cilo archives

Cilo 0.2 writes lossless v2 archives and reads both v1 and v2. Keep the new
executable with your archives; Cilo 0.1 cannot read v2.

## Download directly to an archive

In the downloader GUI, select a build, choose **Extract named files** or
**Mirror encoded content**, enable **Download directly to a .cilo archive**,
choose a compression profile, and click **Download**. The output is a new
`.cilo` file beneath the selected product directory. The Windows GUI bundle
includes the native `cilo.exe`; Python performs network/BLTE work and Rust
performs compression. Downloaded game executables are never run.

From the CLI:

```powershell
py tools\casc_download.py extract --product odin --build 11521872 --archive D:\Archives\odin-11521872.cilo --pattern common.ff --pattern "*.dll" --compression balanced
py tools\casc_download.py mirror --product odin --build 11521872 --archive D:\Archives\odin-encoded.cilo --limit 3 --compression fast
```

Use `python3` on Linux, or `CiloDownloader.exe --cli` in the portable Windows
bundle. Source installations must build native Cilo first or supply `--cilo
PATH`, set `CILO_BINARY`, or put `cilo` on `PATH`.

`--archive` replaces `--output` and supports `extract` and `mirror`. The usual
patterns, tags, limits, CDN fallback and integrity checks still apply.
`--archive-threads` controls native compression workers (default 4). Archive
downloads process files sequentially; `--jobs` controls ordinary directory
downloads. Native block compression runs concurrently with ingestion.

- **Extract:** archive paths are the TVFS filenames, without an extra `files/`
  prefix. BLTE is verified and decoded into the native writer. Native game
  compression inside FF/SAB/Bink files remains intact.
- **Mirror:** archive paths contain `build.json`, `cdn/config/...` for the build
  config, and `cdn/data/...` for core manifests and selected encoded objects.
  It preserves supported encrypted objects without decrypting them. This is
  the same selection semantics as directory mirroring, not a complete Battle.net
  installation or a promise that all CDN payloads are available.

The pipeline stages at most **one decoded file**, plus its encoded object and
metadata/index cache, beside the growing archive. It does not first write a
complete uncompressed build. Budget disk space for the final archive, the
largest decoded file, one encoded object, and metadata/indexes. Metadata may
include large manifests; staging is bounded by individual files, not a fixed
small byte count. The native streaming file limit is 16 GiB by default; the
downloader permits up to 1 TiB per logical file while retaining its configured
encoded-object and total-selection caps.

Archive publication is atomic and never overwrites an existing destination.
Any failed selected file aborts the entire archive. `streamed` progress means
the file was submitted; only the final `published` message means the archive
is complete. Normal success, failure, and cooperative cancellation remove the
download cache, native staging file and incomplete archive. Use **Cancel archive
download** in the GUI; cancellation can wait for a socket timeout or current
compression work. A CLI caller can pass `--cancel-file PATH` and create that
file to request cancellation. Forced termination or power loss can interrupt
cleanup, so use cooperative cancellation when possible.

Incomplete archive downloads are **not resumable**. For unreliable long
transfers, use resumable directory output, then `cilo pack`. Archives are
immutable and do not share chunks with other archives. Packing multiple builds
under one directory gives cross-build deduplication. TVFS does not provide file
timestamps or Unix modes here; streamed downloads use timestamp zero and no
executable bit. These metadata limits do not affect file bytes.

## Browse without extracting

Click **Browse a .cilo archive…** in the GUI, then open an archive. The browser
shows paths, original sizes, timestamps, chunk counts and BLAKE3 digests. Search
filters the file list; the first 3,000 matches are displayed. Select a file and
click **Preview selected file** for UTF-8 text and hex views of its first
64 KiB–4 MiB. No extracted preview files are written. Binary media is displayed
as bytes, not decoded into pictures or playable audio/video.

The CLI provides the same access:

```powershell
cilo contents D:\Archives\odin.cilo
cilo preview D:\Archives\odin.cilo common.ff --max-bytes 65536
cilo cat D:\Archives\odin.cilo common.ff
cilo verify D:\Archives\odin.cilo
```

`contents` returns JSON file details and validates the header/manifest without
reading payload blocks. `preview` returns bounded bytes as a JSON integer array,
verifies all blocks/chunks it reads, and reports whether the whole-file checksum
was checked. A truncated preview does not establish the integrity of unseen
content. Solid compression may require decoding an entire block (up to 64 MiB)
to preview a few bytes. Unrelated payload blocks are not read.

`cat` writes one complete file as raw bytes to stdout and checks its checksum.
Use a binary-safe pipe/redirection when saving it; older PowerShell versions
can alter native binary stdout. `unpack` remains the simplest verified restore
to a new directory. **Verify whole archive** checks every block, chunk and file.
Successful metadata listing alone does not imply that payloads are undamaged.

## Streaming interface for other downloaders

`cilo pack-stream OUTPUT --profile balanced` reads this framing from stdin:

1. Eight bytes `CILOSTR1`.
2. For each file: little-endian u32 JSON byte length (1–65,536), UTF-8 JSON
   `{"path":"dir/file.ff","size":123}`, then exactly `size` original bytes.
   Optional `modified_seconds` (signed Unix seconds) and `modified_nanos`
   default to zero. Unknown fields and nonportable/conflicting paths fail.
3. A zero u32 length, followed by EOF. EOF without the explicit terminator,
   trailing data, or a short file aborts publication.

The caller must close stdin after the terminator. Input file order controls
solid grouping; the CASC downloader sorts by extension, filename, then path.
Parent directories are inferred; this protocol does not encode empty
directories. `--cancel-file` and `--max-file-gib` apply to this command too.
No Python compression codec or external game library is required.
