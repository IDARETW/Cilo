# Cilo

A native Rust compressor/decompressor for lossless local archives of game builds,
including MW2019/CASC files. It combines FastCDC content-defined chunking,
BLAKE3 deduplication across the entire input directory, and parallel solid
Zstandard compression. Put several builds beneath one directory to share their
unchanged data in one archive.

Cilo preserves the **exact file bytes**. It does not decrypt game assets or
transcode textures/audio. Already compressed assets often shrink little on their
own; repeated assets across builds are the main opportunity. Compression results
depend on the files and versions selected; this is not a claim of universally
optimal compression.

## Windows

Download `cilo-Windows-x64` from a successful [GitHub Actions run](../../actions)
and extract `cilo.exe`, or build with Rust 1.90.0 and Visual Studio Build Tools
(the **Desktop development with C++** workload):

```powershell
cargo build --release --locked
.\target\release\cilo.exe pack D:\Builds D:\Archives\mw2019.cilo --profile balanced
.\target\release\cilo.exe verify D:\Archives\mw2019.cilo
.\target\release\cilo.exe unpack D:\Archives\mw2019.cilo D:\RestoredBuilds
```

`D:\RestoredBuilds` must not already exist. The archive must be outside the input
directory, and its parent directory must exist. No Python, Node, DLL downloads,
or external compression executables are needed to run Cilo.

## Linux

Install Rust (the repository pins 1.90.0), a C compiler, and standard build tools.
On Debian/Ubuntu the native prerequisite is `build-essential`; dependencies,
including Zstandard, are built from the locked Cargo sources.

```sh
cargo build --release --locked
./target/release/cilo pack /data/builds /archives/mw2019.cilo --profile balanced
./target/release/cilo verify /archives/mw2019.cilo
./target/release/cilo unpack /archives/mw2019.cilo /data/restored
./target/release/cilo list /archives/mw2019.cilo
```

For multiple builds, use a layout such as `builds/11521872/...`,
`builds/11531527/...`, and `builds/11744825/...`, then pack `builds` once.
Independent archives cannot share deduplicated chunks.

## Tuning

| Option | Behavior |
|---|---|
| `--profile fast` | Zstandard level 3; prioritize throughput |
| `--profile balanced` | Level 15 with long-distance matching; default |
| `--profile max` | Level 22 with long-distance matching; much more CPU for potentially smaller output |
| `--threads 4` | Parallel solid-block jobs; default is available CPUs capped at 4 |
| `--block-mib 32` | Solid compression block size, 4–64 MiB |
| `--chunk-kib 256` | Average deduplication chunk size; powers of two from 64–1024 KiB |
| `--no-dedup` | Disable deduplication for comparisons |
| `unpack/verify --cache-mib 256` | Decoded-block LRU cache, 64–65536 MiB |
| `unpack --max-output-gib 500` | Optional cap on the total declared restored bytes |

Smaller chunks can match more partially changed data at the cost of metadata and
CPU. Larger solid blocks can improve compression. Max mode can use over 1 GiB
per active compression worker; reduce `--threads` on smaller machines. Working
memory consists of pending solid blocks, compressor workspaces, the dedup index,
and archive metadata. Extraction does not load the complete payload into RAM.
Related filenames from different builds are kept adjacent to improve solid
compression and reuse of cached blocks during restoration.

Commands print JSON with byte counts, deduplicated size, compression ratio,
elapsed time, and logical MiB/s. A ratio of 3 means original size / archive size
is 3:1. Speeds include checksums and file I/O. `list` checks metadata integrity;
`verify` checks every referenced block, chunk, and file.

## Integrity and supported files

- BLAKE3 checksums cover the manifest, decoded blocks, chunks, and whole files.
  Zstandard frames also have checksums. These detect corruption; they are not
  signatures or encryption, and do not authenticate an archive's sender.
- Incompressible solid blocks are stored verbatim. Headers and metadata can make
  archives slightly larger than their input, especially for tiny files.
- Archive creation uses a temporary file and refuses to replace an existing
  archive. Restoration verifies into a temporary sibling directory and publishes
  it only after success. Failed restoration does not leave a partial destination.
- Regular files, empty files, directories, file modification times, and the Unix
  executable flag are supported. Paths must be UTF-8 and portable to Windows;
  case collisions, Windows device names, symlinks, and special files are rejected.
- ACLs, alternate data streams, extended attributes, ownership, hard-link
  identity, sparse allocation, directory timestamps, and full Unix modes are not
  preserved. This is a game-file archiver, not a filesystem image utility.
- Keep sources stable during packing. Length and modification-time checks catch
  common edits but do not provide a filesystem snapshot. Do not concurrently
  modify the output directory or its parents while extracting.
- Metadata is capped at 512 MiB uncompressed and held in memory. Extremely large
  collections with millions of unique chunks may need larger chunks or multiple
  archives. V1 archives are immutable; adding a build requires repacking.

## Development and reproducible experiments

For the general Python build downloader, see [NGDP/CASC downloading](docs/DOWNLOADER.md).
It supports catalog-based selection, verified local mirrors, resumable downloads,
archive-backed objects, and named MW2019 file extraction.

```sh
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
node --test tools/casc.test.mjs
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The optional sample downloader needs Node 20+ and curl. It selects complete files
from three pinned retail `odin` builds and verifies CASC configuration keys,
BLTE encoding keys, per-chunk MD5 checksums, manifest content keys, and sizes.
The CDN endpoint uses HTTP; CASC's MD5 checks are format integrity checks, not
transport authentication. No downloaded executable is run. Encrypted or unknown
BLTE modes fail explicitly. Only provenance and measurements belong in Git;
game content stays in your local sample directory.

```sh
node tools/fetch-samples.mjs /data/cilo-samples
cargo build --release --locked
node tools/benchmark.mjs /data/cilo-samples /data/cilo-results.json 3
```

The benchmark harness additionally needs GNU tar, zstd, and xz on Linux. It
compares single-build and three-build corpora, validates every restored file
with independent SHA-256, and writes every timing rather than only the best run.

MIT licensed. Uses FastCDC, BLAKE3, and Zstandard under their respective licenses.
