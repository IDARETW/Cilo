# Balanced compression with a 1 GB archive budget

Measured 2026-10-23 with the released Cilo 0.2.0 Linux executable. **1 GB means
1,000,000,000 bytes** (953.674 MiB), including the archive header, payload and
manifest. Both actual archives fit below this limit; no file was truncated and
no synthetic padding or invented build copies were added.

## Capacity and measured speeds

These are two selected-file examples, not a maximum-capacity guarantee or a
whole-game measurement. Their file selections differ; this is not a controlled
same-corpus deduplication-on/off comparison. Speeds and elapsed times are medians
of three sequential runs with the balanced profile and four compression workers.

| Selection | Files | Original GB | Archive MB | Saved | Pack seconds | Pack MiB/s | Restore seconds | Restore MiB/s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| One Odin build | 27 | 1.106380 | 998.353 | 9.76% | 29.89 | 35.3 | 2.69 | 391.5 |
| Six related Odin builds | 174 | 5.700397 | 999.261 | 82.47% | 25.66 | 211.9 | 4.79 | 1135.9 |

Exact sizes:

- **Single build:** 1,106,379,552 original bytes → 998,352,622 archive bytes; **1,647,378 bytes free** below the cap (99.835% used).
- **Six builds:** 5,700,397,280 original bytes → 999,261,458 archive bytes; **738,542 bytes free** below the cap (99.926% used).

The six-build archive holds **about 5.70 GB** because these nearby builds share
most of the selected assets. Cilo reports 1,099,801,447 unique chunk bytes for
that input before solid compression. This does not imply that one arbitrary
5.70 GB game folder will fit into 1 GB. The single-build example holds about
1.11 GB, with 9.76% total savings. Logical throughput includes duplicate bytes,
so repeated-build workloads can show much higher input MiB/s.

## What was packed

The single-build corpus contains 27 complete files from **odin 11521872**:
`modernwarfare.exe`, `nvngx_dlss.dll`, seven FF files, five FP files, six sound
banks, five Bink videos and two XPAKs. Its EXE is 303,077,984 bytes. These are
native game bytes after verified BLTE transport decoding, not extracted textures
or transcoded media. Game executables were treated as data and never executed.

The six-build corpus contains **29 common asset paths in each of six builds**:
11521872, 11531527, 11558592, 11642448, 11744825 and 11810757. It includes DLL,
FF, FP, SAB, Bink and XPAK files, but excludes the main game EXE. Some selected
paths differ between builds: **23 of the 29 paths are byte-identical across all
six**, and the 174 files contain 39 distinct whole-file SHA-256 identities.
Paths that could not be retrieved for every build
were excluded from this matched selection; failed endpoint attempts are retained
in the provenance data and are not proof that all alternate CDN copies are absent.

The file sets were expanded with complete files until close to the cap, using
measured archive sizes. The intent was to fill the budget with a mixed game-file
selection, not solve an optimal subset selection problem. The one-build archive
has 1.65 MB unused and the six-build archive 0.74 MB unused; neither was padded
to round its size to exactly 1 GB. Selecting different assets or builds changes
capacity substantially.

Each build's configuration and TVFS/DOWNLOAD manifests were verified separately.
An already verified local file was reused only when the other build referenced
the same exact CASC span identities; different identities were fetched and
verified. Hard links saved staging disk space for these confirmed shared bytes.
They do not preserve hard-link identity in the archive: every file was restored
as a separate ordinary file and independently checked by SHA-256.

## Measurement and verification

- Linux, AMD EPYC 9V74 host, five visible logical CPUs, four compression workers.
  Shared cloud environment and warm source cache. These are not Windows speed
  measurements or cold-disk benchmarks.
- Released native binary SHA-256, kernel, memory and every trial are recorded in
  the raw JSON. Balanced uses Zstandard level 15, 64 MiB solid blocks, 256 KiB
  target CDC chunks, format modules and deduplication with default settings.
- Downloads and capacity-selection trial packs finished before timing began.
  The three single-build runs were followed by three six-build runs, sequentially.
  Timing includes native checksums, I/O and sync; network transfer and the extra
  independent SHA-256 comparison are excluded.
- Every restored file in every run matched the input inventory. Archive sizes
  were identical across all three runs of each selection and always below the
  cap. Temporary benchmark archives and restore directories were removed.
- Two final archives are retained in the cloud workspace for inspection:
  `/workspace/cilo-capacity/capacity.cilo` and
  `/workspace/cilo-capacity/multi/capacity.cilo`. Game payloads are not published
  in this repository.

## Data and reproduction

- [Single-build measurements](capacity-single-linux.json) and
  [CASC provenance](capacity-single-corpus.json).
- [Six-build measurements](capacity-six-linux.json) and
  [CASC provenance](capacity-six-corpus.json).
- [Retained archive checksums and verification](capacity-archive-validation.json).
- [Earlier format and profile comparisons](REFINEMENT.md).

Use the downloader to retrieve the exact paths/configurations listed in the
provenance JSON. Arrange the single-build files directly beneath one input
folder; use one subdirectory per build for the six-build input. Confirm the
source SHA-256 inventory before comparing measurements.

```sh
cargo build --release --locked
node tools/benchmark-capacity.mjs /data/single-build results-single.json 3 1000000000
node tools/benchmark-capacity.mjs /data/six-builds results-six.json 3 1000000000
```

The harness measures a fixed supplied corpus, rejects archives exceeding the
budget, validates every restored file, and cleans its temporary outputs. It does
not automatically choose or download a subset. Save its result JSON outside the
source folder. `CILO_BINARY` selects an alternate native executable.
