# Python NGDP/CASC downloader

`tools/casc_download.py` runs on Windows and Linux with **Python 3.10+ and no pip
dependencies**. It reads the actual `versions` and `cdns` tables maintained by
[mdX7/ngdp_data](https://github.com/mdX7/ngdp_data), or accepts explicit build/CDN
configuration hashes and product base URLs. The large uploaded text list is not
required at runtime.

## Desktop GUI and availability cache

```powershell
py tools\casc_download.py gui
```

Use `python3 tools/casc_download.py gui` on Linux. Python's standard Tk GUI
library is required (included with the usual python.org Windows installer;
Debian/Ubuntu package `python3-tk`). The CLI works without Tk or a display.

The Windows release also provides `CiloDownloader-windows-x64.zip`. Extract the
whole folder and open `CiloDownloader.exe`; keep its `_internal` folder beside
it. Python is bundled. It is a directory bundle so launching it does not unpack
a runtime into TEMP. Use `CiloDownloader.exe --cli ...` for the same CLI commands.

Choose a product, search a build/version/config, and select a row. **Download**
uses that row's exact configuration hashes, preserving distinct variants with
the same build number. Choose named extraction or encoded mirroring, a filename
pattern, and optionally a file limit. Downloads go into separate product/build
subdirectories of your chosen destination. Downloaded files are never executed.

Version 0.2 adds **Download directly to a .cilo archive** and **Browse a .cilo
archive…**. The release bundle includes native Cilo. File details and text/hex
previews are available without extracting. For CLI `--archive` examples,
temporary-space requirements, cancellation and archive browsing, see
[archive usage](ARCHIVES.md). Directory output retains its resume behavior;
archive output publishes only after every selected file succeeds.

**Update availability from GitHub** retrieves the published compressed catalog
from this repository. All 35,536 distinct records across 500 products from the
supplied `ngdp_data` snapshot are included, including records with failed or
unverified config checks. This button downloads a published snapshot; it does
not rescan every CDN from your PC. Check the per-build observation timestamp.
Newer checks you performed locally survive an update to an older published cache.
For a private repository, the updater uses `GH_TOKEN`, `GITHUB_TOKEN`, or an
existing `gh auth login`. If access fails, the GUI offers a masked token prompt;
use a token with Contents read access to `IDARETW/Cilo`. Entered tokens stay in
memory for that session and are never written into the cache. Cancel keeps the
bundled cache usable offline.

The labels distinguish config, DOWNLOAD manifest, TVFS manifest, and sample
verification. **Sample verified** means the stated sample succeeded, never that
every payload is retained. Imported US-only observations are identified in the
details. A timeout, 403, 404, hash mismatch, and unsupported format are distinct
evidence, not interchangeable claims that a build was deleted.

**Check selected build** performs a fresh check of configs/manifests and up to
three sample files (at most 32 MiB decoded each). These bounded checks try loose
objects; they do not assert that an unsuccessful archive search was completed.
The CLI probe's `--archive-search` option enables that more expensive search.
Probe configs, manifests, `.part` files, and decoded samples live in a dedicated
temporary directory removed on normal completion or an exception. The GUI waits
for active tasks before closing. Forced process termination or power loss can
interrupt cleanup, as with any application.

Only the small availability cache persists after a check, under
`%LOCALAPPDATA%\Cilo` on Windows or `$XDG_CACHE_HOME/Cilo` (normally
`~/.cache/Cilo`) on Linux. Cache updates validate before atomic replacement and
remove their temporary files on failure. Explicit game downloads retain their
output and resumable `.part` files in the destination you chose.

Maintain or refresh the published cache with the commands in
[the catalog documentation](../data/README.md).

There are two different outputs:

- **`mirror`** saves verified, encoded CASC content under `OUTPUT/cdn/data/...`,
  with the build configuration and core manifests. It is independent of the
  game's filename/root format. It can preserve encrypted objects without their
  decryption keys. This is a local content mirror, not a ready-to-launch
  Battle.net installation or a generated `Data/data.NNN` layout.
- **`extract`** writes original named files under `OUTPUT/files/...` using a
  TVFS v1 manifest, as used by MW2019. It decodes BLTE transport compression and
  preserves the native compression inside game files. It does not decode Oodle,
  transcode media, or run downloaded executables.

## Select a build from the catalog

PowerShell examples (use `python3` instead of `py` on Linux):

```powershell
py tools\casc_download.py info --product odin --build 11521872 --output D:\CASC\11521872
py tools\casc_download.py list --product odin --build 11521872 --output D:\CASC\11521872 --pattern "*.fp" --limit 20
py tools\casc_download.py extract --product odin --build 11521872 --output D:\CASC\11521872 --pattern common.ff --pattern code_post_gfx.fp
```

Product names are catalog directory names, such as `odin`, `s2`, `hero`, or
`wow_classic_era`. `--build` is the corresponding build directory. By default,
the script reads `US/PRODUCT/BUILD/versions` and `US/PRODUCT/cdns`, selecting the
`us` version row (or the global `xx` version row). CDN retrieval prefers that
region, then falls back to **all other regions advertised in the table**.
Both `Servers` and additional `Hosts` entries are considered. A verified working
endpoint is preferred for subsequent objects. Change `--region` and
`--catalog-region` when needed; they do not select a different build silently.
The bundled [historical endpoint map](../data/cdn-endpoints.json) adds documented
older endpoints for the same storage path, respecting `--cdn-region`. Use
`--current-cdns-only` to disable that addition, or `--cdn-map FILE` for another map.
Explicit `--cdn-base` values replace both current and historical selection.

To restrict CDN selection or set its order, repeat `--cdn-region`:

```powershell
py tools\casc_download.py info --product odin --build 11521872 --cdn-region kr --cdn-region eu --output D:\CASC\11521872
```

Pin the catalog or use a local clone for reproducibility:

```powershell
py tools\casc_download.py info --product odin --build 11521872 --output D:\CASC\11521872 --ngdp-root https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe
py tools\casc_download.py info --product odin --build 11521872 --output D:\CASC\11521872 --ngdp-root D:\ngdp_data
```

If a catalog mirror is unavailable, specify `--cdn-base` to override the
advertised endpoints. The known working US endpoint for the measured MW2019
builds is `http://us.cdn.blizzard.com/tpr/odin`.

## Diagnose HTTP errors and regional availability

```powershell
py tools\casc_download.py diagnose --product odin --build 10296867 --output D:\CASC\diagnose-10296867 --http-timeout 10

# Use Blizzard's live CDN table, independently of the historical versions row.
py tools\casc_download.py diagnose --product odin --build 11521872 --output D:\CASC\diagnose-11521872 --cdn-table https://us.version.battle.net/v2/products/odin/cdns

# Or use an older CDN table from the catalog's history, including older hosts.
py tools\casc_download.py diagnose --product odin --build 10296867 --output D:\CASC\diagnose-old-hosts --cdn-table https://raw.githubusercontent.com/mdX7/ngdp_data/d6b0e36fbb375f8be1c4fe4149b81b956041ac64/US/odin/cdns
```

`--cdn-table` also accepts a local `cdns` file and works with normal downloads.
`--cdn-base` takes precedence over table selection. These checks use public CDN
URLs; results can still differ by network location and time.

`diagnose` makes one fresh request per build/CDN configuration URL, records the
HTTP status, timeout/network errors, MD5 verification, and timing, and writes
`diagnostics.json`. It ignores cached configuration hits and does not download
game payloads. Exit status is nonzero if no build configuration was verified.
HTTP 200 with the wrong MD5 is not accepted. These are configuration checks:
neither success nor failure establishes whether **every game file** is retained.

A GitHub/raw catalog 404 means the requested metadata path is absent; a CDN 404
means that particular object URL failed. For example, the catalog contains
`odin/10358353`, not `odin/10358343`. The downloader reports catalog failures
separately and includes the actual URL in CDN retrieval failures. Archive lookup
continues past missing unrelated indexes; if no match is found and some indexes
could not be read, it reports an **incomplete search**, not proof of deletion.
Failed indexes remain retryable after a 30-second cooldown or on the next run.
Index lookups use bounded parallel batches; a new failure is not immediately
retried again in the same search.

When normal config retrieval fails, commands using `--product` also try
BlizzTrack's public cached config page. A bounded set of HTML whitespace variants
is checked against the exact requested MD5. Unmatched content is rejected; a
nearby build is never substituted. This recovered the original config for
`brynhildr_odin` build `10885062`, whose `brynhildr.sys` payload was still available.
Use `--config-fallback none` to disable this external metadata cache. `diagnose`
continues to test CDN URLs directly; it does not conceal their failures with a
cached page. Public caches can be incomplete or rate limited.

See [the dated regional investigation](CDN_FINDINGS.md) for the tested builds,
regional hosts, historical hosts, and limitations of the availability results.

## Map and inspect the storage layout

```powershell
py tools\casc_download.py layout --product odin --build 11521872 --output D:\CASC\layout
py tools\casc_cdn_map.py discover --family odin --history-product odin --output endpoints.json
py tools\casc_cdn_map.py matrix --product odin --kind build_config --map endpoints.json --output matrix.jsonl --jobs 4
```

`discover` records regions, paths, table revisions and lookup failures. Repeat
`--history-product` to traverse other product histories; `--all-products`
selects every catalog product. `matrix` deduplicates identical config URLs while
retaining their product/build references. It uses GET plus MD5, distinguishes
proxy transport errors and rate limits, and pauses a rate-limited host for the
rest of the run. No response-body downloads are retained after a probe.

`layout` accounts for direct manifest EKeys, data/patch archives, loose-file
indexes, patch configs, unresolved content keys, client-generated group indexes,
and unclassified config fields. Group-index hashes are not assumed to be remote
objects. Patch reconstruction and content-key-only ENCODING resolution remain
unsupported; listing those references does not imply they can be extracted.

## Explicit configuration data

```powershell
py tools\casc_download.py info --build-config d88aae6a2bcd3944b48a6b80b5b46c26 --cdn-config 70be2109cbf418718643727cb6198fc0 --cdn-base http://us.cdn.blizzard.com/tpr/odin --output D:\CASC\11521872
```

Repeat `--cdn-base` to provide fallback servers. A missing CDN configuration does
not prevent retrieval of loose objects, but archive-backed objects require the
matching CDN configuration and available archive indexes.

After a direct object request fails, the downloader also reads the CDN's
`file-index` entries when available. It validates their identity, format and
declared size, then reports whether the EKey is listed as a loose object. This
is separate from payload verification: archive indexes are still searched for
an alternate copy, and index membership alone never counts as a recovered file.

## Mirror part or all of a build

```powershell
# Inspect size and available tags first.
py tools\casc_download.py info --product s2 --build 98310 --output D:\CASC\sc2

# Download the three smallest objects plus core manifests as a smoke test.
py tools\casc_download.py mirror --product s2 --build 98310 --output D:\CASC\sc2 --limit 3 --jobs 2

# Plan a selection without downloading its payloads (metadata is still fetched).
py tools\casc_download.py mirror --product odin --build 11521872 --output D:\CASC\11521872 --dry-run --tag enUS --limit 100

# Full DOWNLOAD-listed build content; can be hundreds of gigabytes.
py tools\casc_download.py mirror --product odin --build 11521872 --output D:\CASC\11521872 --jobs 4 --max-total-gib 300
```

`--tag` selects objects bearing **all** requested tags. It is a filter, not an
installer dependency resolver: an `enUS`-only mirror can omit untagged shared
assets and is not a complete English installation. With no tag filter, all
DOWNLOAD entries are selected. `--limit` selects the smallest objects first in
mirror mode. The total-size cap covers selected payloads; core manifests and
archive indexes are additional. `--max-object-gib` defaults to 8.

Rerun the same command after interruption. Verified objects are reused;
incomplete `.part` downloads resume with HTTP Range. Servers that ignore Range
for a loose object cause a safe restart. Archive range requests must return a
matching `206 Content-Range`; a server's full archive response is rejected.
Existing extracted files are kept only when their SHA-256 matches the freshly
verified output; differing files are never overwritten.

Each operation writes `build.json` and appends success/failure records to
`mirror-journal.jsonl` or `extract-journal.jsonl`. Any failed item causes a nonzero
exit status, even if other items completed. A partial selection is never
reported as an entire build. Use a separate output directory for each build.
For compression, group the resulting `files` directories beneath one parent
and pack that parent with Cilo.

## Coverage and limits

- DOWNLOAD manifests v1–v3, including tag bitmaps; direct content keys must be
  present in the build configuration for the metadata needed by the operation.
- Loose CDN objects and loose-file index v1, plus archive lookup using modern
  CDN index v1 with 16-byte keys, 4-byte sizes/offsets, and 8-byte checksums.
  Index identity, footer, table of contents, pages, BLTE headers and every
  encoded chunk are verified.
- BLTE `N` (stored) and `Z` (zlib) extraction. Encrypted and other modes can be
  mirrored if structurally valid, but extraction fails explicitly. No keys are
  guessed or fetched.
- TVFS v1 named extraction supports multi-span files whose source offsets are
  zero. Nested TVFS manifests are not recursively mounted. Non-TVFS games can
  be mirrored; their game-specific filename roots are not decoded by this tool.
- Unsupported legacy metadata/index layouts are reported rather than treated as
  complete downloads. A recorded build does not guarantee the CDN still retains
  its configuration or every payload. HTTP 404/403 failures remain failures.
- Configuration/BLTE hashes follow CASC's MD5 format. They detect corruption but
  are not signatures. HTTPS verification stays enabled; some advertised CDN
  endpoints use HTTP. Normal proxy and certificate environment settings apply.
- Extraction needs temporary disk space for one decoded file per worker and a
  filesystem supporting hard links for exclusive publication (NTFS/ext4 work).
  Keep the output tree under your control while downloading; do not run two
  processes against the same output directory simultaneously.

The real validation covers MW2019 named extraction and repeatability, and a
small StarCraft II mirror, plus a live CDN archive-index lookup and verified
HTTP Range retrieval. Local HTTP tests exercise interrupted-file resume,
ignored Range responses, concurrent reuse, archive-index lookup, encoded
corruption, parser bounds, and unsupported encryption. This does not establish
compatibility with every historical build of every CASC game.

## Archive every available Odin-family build

Open **Odin collection…** for the metadata audit and resumable shared-object
archive workflow. It includes offline named-file browsing, verified cache import,
compressed volumes and restore. See [the collection guide](COLLECTIONS.md) and
[the full catalog audit](../reports/ODIN_COLLECTION_AUDIT.md).
