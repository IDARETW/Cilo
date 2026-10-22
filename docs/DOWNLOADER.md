# Python NGDP/CASC downloader

`tools/casc_download.py` runs on Windows and Linux with **Python 3.10+ and no pip
dependencies**. It reads the actual `versions` and `cdns` tables maintained by
[mdX7/ngdp_data](https://github.com/mdX7/ngdp_data), or accepts explicit build/CDN
configuration hashes and product base URLs. The large uploaded text list is not
required at runtime.

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
`us` version and CDN rows (or the global `xx` version row). Change `--region`
and `--catalog-region` when needed.

Pin the catalog or use a local clone for reproducibility:

```powershell
py tools\casc_download.py info --product odin --build 11521872 --output D:\CASC\11521872 --ngdp-root https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe
py tools\casc_download.py info --product odin --build 11521872 --output D:\CASC\11521872 --ngdp-root D:\ngdp_data
```

If a catalog mirror is unavailable, specify `--cdn-base` to override the
advertised endpoints. The known working US endpoint for the measured MW2019
builds is `http://us.cdn.blizzard.com/tpr/odin`.

## Explicit configuration data

```powershell
py tools\casc_download.py info --build-config d88aae6a2bcd3944b48a6b80b5b46c26 --cdn-config 70be2109cbf418718643727cb6198fc0 --cdn-base http://us.cdn.blizzard.com/tpr/odin --output D:\CASC\11521872
```

Repeat `--cdn-base` to provide fallback servers. A missing CDN configuration does
not prevent retrieval of loose objects, but archive-backed objects require the
matching CDN configuration and available archive indexes.

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
- Loose CDN objects, plus archive lookup using modern CDN index v1 with 16-byte
  keys, 4-byte sizes/offsets, and 8-byte checksums. Index identity, footer, table
  of contents, pages, BLTE headers and every encoded chunk are verified.
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
