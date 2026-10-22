# MW2019 sample results — 2026-10-22

Measured on Linux, AMD EPYC 9V74, 5 exposed CPUs, 33.3 GiB RAM; 4 compression
workers. Rust 1.90.0 release build, Zstandard 1.5.7, XZ 5.8.1. Throughput is
original logical MiB per second, including file I/O. Timings are from this
shared cloud machine, not Windows or a local archival hard disk.

The corpus contains 11 complete game files from each of retail `odin` builds
11521872, 11531527, and 11744825. These include `.ff`, `.fp`, `.xpak`, `.sabs`,
`.dll`, and `.bik`. Eight selected paths are unchanged across all three builds;
three have two distinct contents. This deliberately exercises shared and changed
assets. It is a small chosen sample, **not a random estimate of a full build**.
The two nearby builds share all selected files. No duplicates were synthesized.

All payloads were fetched from CASC, verified against encoding/chunk hashes, and
BLTE-decoded into original game files. Native game asset compression remains.
Every measured restoration was independently SHA-256 checked against the source.
Source keys, sizes, URLs and SHA-256 digests are in [corpus.json](corpus.json).
No game payload is committed.

## Three builds together: 576.10 MiB, 33 files

| Method | Archive MiB | Original/archive | Space saved | Compress MiB/s | Restore MiB/s |
|---|---:|---:|---:|---:|---:|
| Cilo fast | 213.71 | 2.70:1 | 62.90% | 567.2 | 777.0 |
| Cilo balanced | 198.91 | 2.90:1 | 65.47% | 77.5 | 793.5 |
| Cilo max | **197.09** | **2.92:1** | **65.79%** | 31.9 | 705.5 |
| tar + zstd -19, 64 MiB window | 533.04 | 1.08:1 | 7.47% | 5.3 | 674.1 |
| tar + xz -6 | 533.85 | 1.08:1 | 7.33% | 7.4 | 197.6 |

Speeds are medians of three complete runs. Cilo defaults: 256 KiB average chunks,
32 MiB solid blocks, 256 MiB decode cache. Content deduplication reduces the
604,082,836 input bytes to 234,419,389 unique bytes before solid compression.

A stronger cross-build baseline and a chunk/block tuning experiment were each
run once separately:

| Additional experiment (one run) | Archive MiB | Ratio | Compress MiB/s | Restore MiB/s |
|---|---:|---:|---:|---:|
| tar + zstd -19, **1 GiB window** | 197.55 | 2.916:1 | 12.5 | 812.6 |
| Cilo max, 64 MiB blocks / 64 KiB chunks | 197.78 | 2.913:1 | 24.7 | 548.0 |

The 1 GiB Zstandard window can see repeated data across builds and nearly closes
the size gap. Cilo max was 481,547 bytes smaller and about 2.55 times faster to
compress than that run; its restoration was slower. Larger blocks and smaller
chunks did not improve this sample, so the defaults were retained. This does
not establish optimal settings for every game corpus.

For this collection, balanced is a useful tradeoff: it is only 1.81 MiB larger
than max and compresses about 2.4 times faster. Fast mode provides much higher
throughput at the cost of another 14.81 MiB.

## One build: 191.86 MiB, 11 files

| Method | Archive MiB | Space saved | Compress MiB/s | Restore MiB/s |
|---|---:|---:|---:|---:|
| Cilo fast | 180.41 | 5.97% | 294.1 | 466.3 |
| Cilo balanced | 179.24 | 6.58% | 24.8 | 505.1 |
| Cilo max | 177.58 | 7.44% | 10.1 | 424.9 |
| tar + zstd -19, 64 MiB window | **177.51** | **7.48%** | 4.6 | 655.1 |
| tar + xz -6 | 177.79 | 7.33% | 7.5 | 181.4 |

Most of these assets are already compressed. A standalone build sees modest
additional savings. The larger gains above depend on actual cross-build reuse.

## Reproduce

```sh
node tools/fetch-samples.mjs /data/cilo-samples
cargo build --release --locked
node tools/benchmark.mjs /data/cilo-samples /data/results.json 3 fast,balanced,max,tar-zstd-19,tar-xz-6
node tools/benchmark.mjs /data/cilo-samples /data/tuning.json 1 max-64m-64k,tar-zstd-19-long1g three-builds
```

The source cache is warmed by validation before measurement. Runs are sequential;
network time and the independent SHA-256 comparison are excluded. Cilo includes
its own checksums, filesystem reads/writes, and output `sync_all`; tar baselines
include tar creation/extraction, but do not provide the same fsync guarantee.
The baseline tar orders files by path; Cilo groups by extension and filename.
Other solid ordering/window/dictionary choices may change the result.

Raw per-run timings: [results-linux.json](results-linux.json) and
[tuning-linux.json](tuning-linux.json). Only Linux was benchmarked. GitHub Actions
did not start jobs in this session, so no Windows CI pass is claimed here.
