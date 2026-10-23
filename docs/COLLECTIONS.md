# Preserving the Odin family with Cilo 0.3

The collection workflow stores each identical encoded CASC object once across
all builds and products. Every build has its own references to those objects.
It preserves original BLTE bytes, including encrypted payloads, and compresses
them into independently readable `.cilo` volumes using native Rust Cilo.
Python handles the catalog, HTTP, verified CASC metadata and SQLite indexing.

Read the [measured audit](../reports/ODIN_COLLECTION_AUDIT.md) before planning
storage. A readable DOWNLOAD manifest establishes a download plan, not the
availability of every payload. The collection does not promise a runnable
Battle.net installation or preserve every historical patch transport blob.

## Desktop workflow

1. In CiloDownloader, update the catalog from GitHub and open **Odin collection…**.
2. Choose a collection directory on the intended storage device. The default
   scope includes every catalog product containing `odin`, including retail,
   development, vendor and Brynhildr variants. Catalog aliases remain distinct.
3. Optionally **Import an existing cache…** from a downloader output directory
   containing `cdn/config` and `cdn/data`. Source files remain intact; each
   imported object is reverified and globally reused. Flat `config-KEY` /
   `data-KEY` research caches are also accepted. Decoded extracted files cannot
   substitute for their original encoded objects.
4. Click **Audit all Odin builds**. This fetches metadata, inventories every
   DOWNLOAD tag, resolves supported manifest references, and measures exact
   sharing. The archive-index option searches archive members after loose
   metadata misses. Failed configs cannot supply an object plan. Repeating an
   audit retries failures without erasing previously verified plans.
5. When the storage is ready, click **Archive / resume all indexed builds**.
   Leave the download limit at zero for the entire indexed set, or set a
   nonzero GiB limit for a trial. No bulk download starts merely by opening the
   window or running the metadata audit.
6. Use **Verify stored data** periodically. Select a build to see its coverage,
   errors and format inventory. **Browse selected build files…** lists TVFS
   names from local manifests and previews stored content without restoring
   the whole build. **Restore selected encoded build…** writes a new offline
   CDN mirror once every required object is stored.

The Stop button cooperatively cancels HTTP and archive ingestion. An in-flight
native compression/read may take time to finish. Verified pending objects are
retained for resume; incomplete publication is never counted as archived.
Stop and wait before closing the main application or disconnecting storage.

## What is stored and how sharing works

- The identity is `(config or data, full 128-bit key)`, **not** filename, product,
  CDN host, nine-byte TVFS prefix or file length. DOWNLOAD resolves TVFS prefixes.
- Configuration MD5, BLTE EKey and every available BLTE chunk checksum are
  checked before admission. SHA-256 records the complete encoded bytes; a
  conflicting SHA-256 under the same CASC identity is rejected.
- Exact objects are reused globally across all volumes. Native Cilo also shares
  identical content-defined chunks **within each volume**. Partial similarities
  between different objects in different volumes are not globally deduplicated.
- Different BLTE encodings of the same decoded file retain their separate
  original objects. The original encoding is useful for exact offline mirrors,
  encryption support and compatibility. Decoding and keeping only one CKey
  would change that preservation contract; this release does not do so.
- DOWNLOAD covers all listed languages/platform tags, not just the current PC's
  language. Core manifests, build configurations, direct patch-config
  references and supported ENCODING-resolved dependencies are also recorded.
  CDN routing configs are attempted and retained when reachable, but are optional
  for offline restoration: the game payload does not depend on those URL/index
  routing tables. Missing optional CDN configs are reported separately.
- TVFS file recipes are fingerprinted by full EKeys, ordered spans and lengths,
  regardless of filename. Matching recipes measure inferred decoded-file
  sharing without downloading game payloads. They are not content-hash proofs
  for payloads that have not been fetched.
- ENCODING v1 CKey pages are validated and resolve supported single-CKey
  manifests. Some Odin `root` values are absent from ENCODING; those remain
  explicitly unresolved. The tiny ENCODING table is not the complete Odin
  asset inventory. Unknown config fields remain visible in the audit.
- All direct VFS objects and DOWNLOAD-listed objects are preserved. Recursive
  TVFS mount interpretation, unknown root formats, encrypted named extraction,
  nonzero-offset named previews and historical patch application are not
  implemented. The encoded data remains available for later tooling.

A failed object is tried through the distinct source configurations of builds
that reference it, including CDN archive-index/range lookup. One source's
failure does not blacklist the object globally. Later runs retry failures.
Public config-cache fallback is restricted to keys with previously verified
recovery evidence in the catalog; the collection does not bulk-scrape that
third-party service.

## Status and integrity

`pending` means no metadata attempt; `not_retrieved` records a failed attempt,
not proof of deletion. `config_verified` has a configuration but no complete
DOWNLOAD plan. `indexed` has a verified plan with unverified payloads. `partial`
has some archived objects. `encoded_set_archived` means every required object
in the recorded scope was published and read back successfully; inspect the
unresolved-reference details before making broader completeness claims.

A verification failure marks the volume damaged and removes its objects from
reported healthy coverage. Restore the affected volume from backup, then run
Verify to revalidate it. Ordinary resume checks volume presence and size;
Verify reads and hashes every byte to detect same-size damage. Reports reflect
verification at admission/last explicit verification, not continuous monitoring.

## On-disk layout and recovery

```
collection/
  collection.sqlite3               global build/object/reference index
  collection-backup.sqlite3        consistent snapshot after completed operations
  catalog.json.gz                  GUI's selected catalog snapshot
  audit.json                       coverage, byte counts and per-build evidence
  volumes/UUID.cilo                immutable compressed objects + embedded journal
  volumes/UUID.cilo.json           recoverable publication journal
  pending/config-KEY, data-KEY     verified inputs retained until publication
  scratch/work-*/                  task-owned temporary downloads and indexes
  collection.lock                 operating-system worker lock
```

Keep the entire folder together and back it up. SQLite uses transactions, full
synchronous writes and WAL. A volume journal is written before atomic native
publication; the archive is read back before its objects are committed. Pending
inputs are removed only after that transaction. On resume, verified published
volumes whose database update was interrupted are recovered from their embedded
and sidecar journals. On supported Unix filesystems, publication directories
are also synced. Storage hardware/filesystem durability still matters.

Normal success, failure and cooperative cancellation clean task scratch files.
A process/power crash may leave scratch files; the next exclusive worker
removes its `work-*` directories. `pending` is intentional verified resume data,
not disposable temporary data. A backup database plus later volume journals
can recover an interrupted publication sequence. Rebuilding every build's
references from volumes alone is not supported: preserve the catalog/database
backups too. Do not edit or delete individual database/volume files in a live
collection. There is no automatic garbage collection or build-deletion action.

Default volumes target 4 GiB of encoded input, capped at 50,000 objects. A
single larger object occupies its own larger volume. This bounds native
manifest growth; the entire collection never becomes one giant `.cilo`
manifest. Leave headroom for verified pending input, a compressed volume being
written, native staging of one object, downloaded archive indexes and SQLite
backup/WAL files. For the audited Odin set, the largest declared object is
under 1 GB; allow at least 16–20 GiB of working headroom beyond the planned
archive and metadata sizes. That is not a universal limit for other datasets.
Compression uses four native workers; `max` can consume substantially more RAM
than `balanced`. Network payload ingestion is sequential; metadata audit jobs
are bounded separately. There are no whole-collection in-memory file lists.

## CLI examples

Use `py` on Windows or `python3` on Linux; the portable application also supports
`CiloDownloader.exe --collection` followed by the same arguments. Native Cilo
0.3 is required for collection restore. Set `CILO_BINARY` or `--cilo` if needed.

```powershell
py tools\casc_collection.py audit D:\OdinCollection
py tools\casc_collection.py audit D:\OdinCollection --resume-audit
py tools\casc_collection.py import-cache D:\OdinCollection --source D:\PreviousBuild
py tools\casc_collection.py archive D:\OdinCollection --max-download-gib 1
py tools\casc_collection.py archive D:\OdinCollection
py tools\casc_collection.py verify D:\OdinCollection
py tools\casc_collection.py files D:\OdinCollection --build-id ID_FROM_AUDIT_JSON
py tools\casc_collection.py preview D:\OdinCollection --build-id ID_FROM_AUDIT_JSON --path common.ff
py tools\casc_collection.py restore D:\OdinCollection --build-id ID_FROM_AUDIT_JSON --destination D:\RestoredBuild
```

`--loose-only` is a faster metadata survey that explicitly skips archive-index
fallback. `--refresh` rechecks successful metadata too; `--resume-audit` skips
all previously attempted records. `--product` and `--build` restrict a trial.
`--jobs` permits 1–16 metadata workers, default four. `--volume-gib` changes the
volume input target. The payload byte limit excludes metadata/index requests,
previously retained inputs, and resulting archive size. With a nonzero limit,
objects whose sizes are unknown are skipped instead of exceeding the budget.
The result remains partial when required objects are skipped.

Native `cilo unpack-selected ARCHIVE NEW_DIRECTORY selection.json` restores a
JSON array of member paths using one verified reader/block cache. Ordinary
`cilo contents`, `preview`, `cat`, `verify` and the archive browser also work on
each volume independently. Offline restoration requires no CDN connection.
