# Odin-family preservation audit — 2026-10-23

**The indexed set describes 23.950 TB of repeated encoded data but only 1.704 TB
of distinct encoded objects: 92.89% less storage before Cilo compression.**
This replaces the earlier sample-based storage guess for the indexed subset.
It does **not** estimate the inaccessible/unresolved remainder of the catalog,
or prove that every listed game object can currently be downloaded.

The entire catalog scope was checked: **2,192 records, 27 Odin-family products**.
**107 records have verified DOWNLOAD inventories**, representing **104 distinct
build configurations**. They comprise **101 `odin` records** (99 distinct retail
configs), four `brynhildr_odin`, one `brynhildr_odin2` and one `odinb` record.
The remaining **2,085 records could not be inventoried** through the tested
configuration endpoints. HTTP errors and unverified bodies are not deletion
proofs. No new dev/vendor product yielded a verified DOWNLOAD in this run.

Cilo 0.3 implements the future collection workflow in the downloader's
**Odin collection…** window. Use **Audit all Odin builds**, then, when storage
is ready, **Archive / resume all indexed builds**. Opening the window or auditing
does not start a multi-terabyte payload download. See the
[collection guide](../docs/COLLECTIONS.md) for controls, restore and recovery.

## Measurements and storage planning

TB means 1,000,000,000,000 bytes. These totals retain all DOWNLOAD language and
platform tags; they are not sizes of a single-language selective installation.
Catalog aliases remain distinct logical records and contribute repeated
references, while their shared objects occupy physical storage only once.

| Measurement | All indexed Odin-family records | `odin` only |
|---|---:|---:|
| Readable DOWNLOAD inventories | 107 | 101 |
| Referenced encoded bytes, before sharing | 23,950,390,531,989 | 23,895,673,242,893 |
| Distinct known encoded bytes | 1,703,585,210,671 | 1,649,466,064,059 |
| Exact-object sharing reduction | 92.89% | 93.10% |

The indexed set has **242,189 unique
object identities** and **6,298,879 build/object
references**. There are **195 config
objects of unknown size** in that indexed plan; their bytes are excluded from
the totals. The wider catalog also contributes unresolved routing-config keys:
the exported inventory contains 243,525 identities, including
1,531 configs of unknown size. Unknown configurations
and unindexed game builds are never treated as zero-byte complete builds.

For the **currently indexed set**, use approximately **1.7 TB before further
compression**, plus metadata, volume overhead and working space, as the planning
baseline. A **4 TB usable collection volume** provides comfortable headroom for
this measured subset; it is not a capacity promise for all 2,192 records.
Budget a separate backup and extra capacity for any newly recovered histories.
An accurate size for every full Odin/variant build remains unknown until its
manifest is recovered. The earlier roughly 12 TB estimate was a speculative
sample extrapolation, not a measured unique-object union.

The full audit downloaded and hash-verified **416
metadata objects**, retaining 416,412,972 bytes in its managed resume
area. The SQLite index is 1,904,844,800 bytes, with a separate consistent
backup. Normal scratch directories were empty after the run. This audit did
not archive or fetch the entire game payload set.

## File-level overlap and formats

All 107 indexed records supplied readable TVFS file inventories. Their
**6,865,404 named file references** reduce to
**241,958 distinct reconstruction recipes**. The
repeated decoded lengths total **37.297
TB**, versus **2.785 TB** counted once per
recipe (92.53%
inferred sharing). A recipe hashes the full EKeys, ordered span offsets/lengths
and total length; filename changes do not prevent a match. This measures
metadata-declared equivalence, not independently downloaded whole-file hashes.
Different encodings of identical decoded content can remain separate recipes.

The following is the **TVFS filename-suffix inventory**, summed over indexed
records. Payload headers were not parsed for every entry. Numbered `.xpak_N`
and `.sabs_N` parts are grouped only in this table; their original paths and
object references remain separate in preservation and restore.

| Suffix group | File references | Repeated decoded TB |
|---|---:|---:|
| `.xpak` and numbered parts | 497,819 | 20.883152 |
| `.sabs` and numbered parts | 535,630 | 11.078866 |
| `.ff` | 5,509,806 | 4.313017 |
| `.bik` | 21,807 | 0.763720 |
| `.fp` | 281,854 | 0.193104 |
| `.exe` | 323 | 0.030985 |
| `.dll` | 5,096 | 0.027664 |
| `.pak` | 5,916 | 0.002598 |
| `.sabl` | 343 | 0.002502 |
| `.dat` | 102 | 0.001069 |
| `.bin` | 402 | 0.000049 |
| `.sys` | 5 | 0.000012 |
| `.csv` | 102 | 0.000001 |
| `.xpaklist` | 5,995 | 0.000000 |
| `.icn` | 102 | 0.000000 |
| `.cfg` | 102 | 0.000000 |

The storage strategy follows the formats:

- **XPAK/texture data and sound banks** account for most bytes. Preserve encoded
  payloads and share identical objects across all builds. Already compressed
  textures and audio do not justify a promised 80% standalone reduction.
- **FF/FP assets** account for many small file references. Global object reuse
  avoids duplicate downloads; native content-defined chunking can also share
  partial similarities within each volume.
- **Bink video** stays bit-exact. No transcoding, quality reduction or removal
  of duplicate-looking cinematics is performed.
- **EXE/DLL/SYS** remain original bytes. Existing native PE filters are available
  when packing decoded files; encoded collection preservation does not
  reinterpret or execute game binaries.
- Unknown and encrypted objects remain preservable as verified BLTE bytes.
  Named extraction/preview can still be unsupported for their encoding mode.

The native [format modules](../docs/GAME_FORMATS.md) remain useful for decoded
single-build/multi-build archives. The collection's primary additional savings
come from global encoded-object sharing. It does not claim that those decoded
format transforms run inside arbitrary BLTE wrappers.

## Manifest and completeness findings

DOWNLOAD is the inventory authority for all listed content. ENCODING alone is
insufficient: the tested retail `11521872` table has **124 CKey entries**, while
its DOWNLOAD lists about **64,000 objects**. Cilo validates ENCODING CKey-page
checksums and supports CKey-to-EKey resolution, including alternative encodings.

**All 107 indexed configs retain an unresolved legacy `root` CKey reference**
that was absent from their ENCODING maps. Their direct `vfs-root` objects still
provided the named inventories. This report does not silently equate that fact
with resolving the legacy root or proving a Battle.net installation complete.
Every unresolved reference is retained in the per-build evidence. There were
no ENCODING parser errors and no TVFS filename-inventory errors in this pass.

Required preservation references include the build config, every DOWNLOAD
object, direct core/VFS/patch-index manifest references and declared patch
configurations. CDN routing configs are fetched when available, but are
optional for offline restore: missing routing metadata cannot remove an
already stored game payload. Missing optional configs are reported separately.

Recursive TVFS mount interpretation, unknown roots, nonzero-offset named
previews, encrypted named extraction and historical patch application remain
outside the implemented decoder. All direct VFS objects and DOWNLOAD entries
are still recorded. Historical patch archives/groups are transport history,
not automatically additional final-build game files; see the
[full endpoint/layout map](../docs/CDN_ENDPOINT_MAP.md).

`indexed` means a verified plan. `encoded_set_archived` is assigned only after
every required object in that recorded scope is stored and read back through
the native archive verifier. It is not a claim that unresolved root semantics,
patch installation or runtime dependencies have been solved. Failed object
requests, optional config gaps and damaged volumes stay visible.

## Bounded real archive validation

A separate trial covered stored objects referenced by six retail builds and
one Brynhildr companion. It imported existing verified downloader caches,
fetched 863,915 additional manifest/payload bytes, exercised interruption,
published the retained inputs, and verified every resulting volume.

| Selected stored subset | Bytes |
|---|---:|
| Repeated encoded references across builds | 1,195,719,817 |
| Distinct encoded objects stored | 264,646,204 |
| Actual Cilo volumes | 224,452,087 |

The resulting **81.23%
reduction** combines exact sharing and native compression. It is an opportunistic
48-object subset, not a representative full-build compression benchmark. The
six retail builds correctly remain partial. The companion's required encoded
set restored offline despite a missing optional CDN routing config; its
extracted `brynhildr.sys` matched SHA-256
`707fdf62fe278cf7b4ea753fddaa145e4a3817d6c1f5b9bf3e5b920b9f70c224`.
See [trial evidence](collection-2026-10-23/bounded-real-trial.json).

The earlier [1 GB capacity benchmark](../benchmarks/CAPACITY_1GB.md) supplies
measured native compression/restoration speeds on larger decoded selections.
No multi-terabyte collection download speed or full-collection compressed size
is claimed here. Missing-object archive-index searches can be lengthy; the
trial interrupted one such search and successfully resumed from verified data.

## Collection design and remaining savings opportunities

A single multi-terabyte archive would create a large in-memory manifest and
poor failure isolation. Cilo instead uses an on-disk global SQLite identity map
and immutable native volumes, normally 4 GiB of encoded input and at most
50,000 members each. Every build points to shared objects; exact reuse spans
all volumes and all products. CDN archive-index locations are also disk-backed
in the collection worker, while temporary indexes remain inside managed scratch.

Publication writes a recovery journal, atomically publishes a new `.cilo`,
reads it back, commits the database and only then deletes verified pending
inputs. Interrupted database publication can adopt an intact orphan volume.
The GUI supports cancellation, retry/resume, cache import, per-build coverage,
offline filename/detail/text/hex browsing, verification and encoded restoration.
A database backup plus later journals preserves recovery information. Keep the
entire collection directory and its backup; volumes alone do not reconstruct
every build's reference graph.

Exact EKey sharing is global. **Partial chunk sharing is currently local to a
volume**. A future global chunk index, or a decoded-content store with reversible
BLTE regeneration, could save more when encodings differ. Neither is silently
assumed in the measured 1.704 TB union. Retaining only one decoded file would
lose original encodings unless reconstruction is demonstrated byte-for-byte,
and encrypted objects cannot be assumed decodable. No objects are discarded
based merely on equal names, sizes or shortened key prefixes.

## Product coverage

| Product | Catalog records | DOWNLOAD indexed | Unresolved |
|---|---:|---:|---:|
| `brynhildr_odin` | 4 | 4 | 0 |
| `brynhildr_odin2` | 1 | 1 | 0 |
| `brynhildr_odin_qa` | 5 | 0 | 5 |
| `brynhildr_odin_qa2` | 1 | 0 | 1 |
| `brynhildr_odin_vendor` | 3 | 0 | 3 |
| `brynhildr_odin_vendor2` | 2 | 0 | 2 |
| `odin` | 315 | 101 | 214 |
| `odina` | 1 | 0 | 1 |
| `odinb` | 1 | 1 | 0 |
| `odindev` | 50 | 0 | 50 |
| `odine` | 27 | 0 | 27 |
| `odinv1` | 200 | 0 | 200 |
| `odinv10` | 111 | 0 | 111 |
| `odinv11` | 2 | 0 | 2 |
| `odinv12` | 3 | 0 | 3 |
| `odinv13` | 38 | 0 | 38 |
| `odinv14` | 58 | 0 | 58 |
| `odinv15` | 1 | 0 | 1 |
| `odinv16` | 3 | 0 | 3 |
| `odinv2` | 343 | 0 | 343 |
| `odinv3` | 545 | 0 | 545 |
| `odinv4` | 303 | 0 | 303 |
| `odinv5` | 6 | 0 | 6 |
| `odinv6` | 18 | 0 | 18 |
| `odinv7` | 110 | 0 | 110 |
| `odinv8` | 35 | 0 | 35 |
| `odinv9` | 6 | 0 | 6 |

## Evidence and reproduction

- [Compact summary and full suffix counts](collection-2026-10-23/summary.json).
- [Every build's metadata evidence, inventory counts and errors](collection-2026-10-23/audit.json.gz).
- [Unique object keys, sizes, reference counts and locally verified SHA-256 values](collection-2026-10-23/unique-objects.tsv.gz).
- [Windows/Linux validation](windows-v0.3.md) and
  [frozen application workflow result](collection-2026-10-23/frozen-windows-validation.json).

The `mdX7/ngdp_data` master revision was rechecked and matched the bundled
catalog: `d8bcfafa67e7408c95d33caa3733729aff17c6fe`. The survey used all advertised
regions plus the historical endpoint map, covering 30 distinct base URLs from
one cloud environment. It used real GET responses and content checksums,
not HEAD status alone. Regional hostnames are not separate physical vantage
points. The previously recovered, MD5-reverified Brynhildr config was seeded
from the retained 2026-10-22 cache; its provenance remains in the catalog.

The full catalog pass used `audit --loose-only`, initially eight then sixteen
workers, four-second HTTP timeouts and one attempt per endpoint. Resume skipped
already attempted records. The 107 indexed records were then re-read with four
workers and six-second timeouts for ENCODING and complete TVFS recipe analysis.
**No full-catalog archive-index payload search was performed.** The default GUI
can search indexes for missing manifests, and archive/range fallback was tested
end-to-end against a verified local CDN fixture. Unresolved records can be
retried later or supplemented using the verified cache-import action.

To reproduce on your machine:

```powershell
py tools\casc_collection.py audit D:\OdinAudit --loose-only --jobs 4
py tools\casc_collection.py status D:\OdinAudit
```

Inspect `audit.json` and `collection.sqlite3`; enable the GUI's index-search
option or omit `--loose-only` for deeper metadata retrieval. To reproduce the
storage calculation, join `refs` to `objects` and sum known encoded sizes once
per reference, then once per distinct object. Read the recipe totals separately
from encoded bytes. Missing sizes and builds remain explicit unknowns.
