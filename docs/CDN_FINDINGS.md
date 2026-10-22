# MW2019 CDN investigation — 2026-10-22

The tested regional endpoints did not recover the two reported older build
configurations. Regional fallback does help with transient endpoint failures,
and the downloader now tries every advertised region by default. The catalog
records historical metadata; inclusion in that catalog is not a promise that
Blizzard still serves every configuration, manifest, index, or payload.

## The reported builds

| Requested build | Observation |
|---|---|
| `10358343` | No `US/odin/10358343/versions` entry in the tested catalog; the uploaded list instead contains `10358353`. The error comes from GitHub's catalog endpoint. No build number is silently substituted. |
| `10358353` | Catalog entry exists. Its build config `bc90718ff5987302bf5b8496214ee32f` returned 404/403 or transport errors on the current regional and historical endpoints tested. No verified config was retrieved. |
| `10296867` | Catalog entry exists. Its build config `92db83c9f6c501b5e288a506db05c506` returned 404/403 or transport errors on the same endpoint families. No verified config was retrieved. |
| `11521872` (control) | The same endpoint families served a configuration whose MD5 matched `d88aae6a2bcd3944b48a6b80b5b46c26`. Some requests temporarily returned 503 while other endpoints succeeded. |

See the complete diagnostic results for
[`10296867`](../reports/cdn-2026-10-22/10296867-diagnostics.json) and
[`10358353`](../reports/cdn-2026-10-22/10358353-diagnostics.json), plus the
[regional/historical comparison](../reports/cdn-2026-10-22/regional-and-historical-probes.json).
403, 404, timeout, and 503 results remain separate. A 403 is not relabeled 404,
and a timeout is not evidence of deletion.

## Regions and historical hosts

The `US`, `EU`, `KR`, `SG`, `TW`, and `CN` catalog copies of `odin/cdns` all
contained the same table (sequence 3433538), with actual `us`, `eu`, and `kr`
rows. Blizzard's live `https://us.version.battle.net/v2/products/odin/cdns`
returned the same table. Switching a catalog folder alone therefore does not
necessarily select a different CDN server.

The requests included these hostnames, with HTTP/HTTPS variants where advertised:

- `us.cdn.blizzard.com`
- `eu.cdn.blizzard.com`
- `kr.cdn.blizzard.com`
- `blizzard.gcdn.cloudn.co.kr`
- `level3.blizzard.com`
- `level3.ssl.blizzard.com`
- `blzddist1-a.akamaihd.net`
- `blzddistkr1-a.akamaihd.net`

The Akamai endpoints came from historical catalog revisions, including
`d6b0e36fbb375f8be1c4fe4149b81b956041ac64` (2020-11-20) and
`26922160bf5857d8831b84d746c23ce0905d5217` (2023-06-13). They served the newer
control config but did not recover the two older target configs. These are
requests from one cloud environment to regional hostnames, not measurements
from physical machines in each country.

## Expanded retail and product-family survey

The supplied catalog snapshot contains **315 retail `odin` records**. An initial
US/Korean survey verified 87 build configs. The expanded 4,410-request matrix
across 14 working endpoint variants verified **101/315**, recovering **14 more
build configurations on historical Akamai endpoints**. These are configuration
counts, not complete downloadable builds. Some variants returned transient
errors; the union of verified results is what matters.

The additional build IDs are `7243209`, `7256861`, `7274739`, `7276347`,
`11116736`, `11155708`, `11222167`, `11227632`, `11281361`, `11330025`,
`11364056`, `11383760`, `11419693`, and `11494759`. See the
[exact keys and successful URLs](../reports/cdn-2026-10-22/expanded/additional-retail-configs.json).
All four historical US/Korean Akamai HTTP/HTTPS variants contributed verified
responses. The downloader includes these advertised historical endpoints by default.

The broader inventory covers **2,192 records across 27 Odin-related products**,
with four distinct storage paths: `tpr/odin`, `tpr/odinv`, `tpr/odind`, and
`tpr/odine`. Eighteen historical retail CDN-table revisions and the product tables
produced 29 distinct advertised base/path combinations; the user-supplied HTTPS
Level3 spelling brings the map to 30. There are 17 distinct retail variants;
the control run included one duplicate spelling, retained in its raw evidence.
The 3,917 distinct family configuration requests yielded 119 MD5-verified configs
and 187 HTTP-200 bodies whose raw MD5 did not match. Those 187 are **unverified**;
encryption/format differences have not been established as their cause.

The [expanded report](../reports/cdn-2026-10-22/expanded/README.md) includes the
per-request CSVs, compressed raw results, endpoint provenance, payload evidence,
and explicit unfinished checks. Failures remain observations of exact URLs at a
time and location; a transient or denied request is not proof of deletion.

## Payload checks and the user-supplied config

The original 87 readable retail configs all yielded verified DOWNLOAD and TVFS
manifests. A `code_post_gfx.fp` sample verified for **86 of those 87 builds**.
This was a one-file sample per build, not a full payload audit. The separate
[compression corpus](../benchmarks/README.md) contains 33 files from three builds.
New three-sample probes for the 14 additional configs are recorded separately;
proxy failures during those probes leave parts of their payload status unresolved.

The user-supplied `ec4dc25c00b68ccf203cb0d1eaa77283` is the build config for
**`brynhildr_odin` 10885062**, a companion product. It is not the retail `odin`
config for that number. BlizzTrack's public cached config page contained the
exact original 741 bytes after HTML whitespace restoration, verified against
that MD5. Its DOWNLOAD/TVFS manifests and `brynhildr.sys` payload remained
retrievable. The extracted 2,355,952-byte file has SHA-256
`707fdf62fe278cf7b4ea753fddaa145e4a3817d6c1f5b9bf3e5b920b9f70c224`.
It was downloaded for integrity checks and never executed.

The Level3 S3 `ListBucket` denial also occurred for an intentionally nonexistent
all-zero key, while known configs succeeded on the same host. Thus this message
alone does not establish that the requested object exists or that listing the
bucket would reveal it. The downloader uses exact object paths; no listing
permission is required for objects the server makes publicly readable.

## CDN object layout

| Configuration reference | Meaning / URL |
|---|---|
| Build/CDN configuration hash | `config/AA/BB/KEY`, exact raw MD5 |
| Manifest EKey or payload EKey | `data/AA/BB/KEY`, BLTE encoding checks |
| `archives` | `data/AA/BB/ARCHIVE` plus `.index`; payloads fetched by verified HTTP Range |
| `file-index` | Loose-object `.index` under `data`; entries have zero offset bytes |
| `patch-archives`, `patch-file-index` | Corresponding index/archive namespace under `patch` |
| `patch-config` | Another configuration object under `config` |
| `archive-group`, `patch-archive-group` | Locally generated combined-index identities, not required remote objects |
| Content key without an EKey | Requires an ENCODING mapping; it is not automatically a CDN URL |

The group-index distinction is documented in
[TACT.Net IndexFile.Write](https://github.com/wowdev/TACT.Net/blob/master/TACT.Net/Indices/IndexFile.cs):
group indexes are generated client-side and skipped when publishing individual
indexes. An earlier test of a guessed group URL returned 404; **that response
says nothing about the constituent archive payloads**. The parser now accounts
for loose, archive, and group offset widths (0, 4, and 6 bytes), retaining footer,
identity, table-of-contents, and page checksum validation.

`layout` lists every recognized object reference and preserves unclassified
fields from the supplied configs. The recorded accounting includes all 119
locally retained verified family configs. Patch reconstruction, legacy
content-key-only resolution, encrypted config decoding, and nested TVFS mounting
remain unsupported; they are reported explicitly.

Build `7547738` has retrievable configs, manifests, and a 5,237-entry loose-file
index. Two failed sample EKeys are present in that loose index, demonstrating
that index membership alone is not live availability. Twenty-four distributed
small loose-file samples failed on the tested endpoint. Of 52 historical archive
indexes checked on two endpoints, two verified; other requests failed or were
transient. A separate 171-index newer-CDN search verified 151 indexes with 20
transient failures and did not find the target sample. **Neither is a completed
historical-host archive search**, and neither proves all payloads are gone.

For unavailable configs, exact original bytes from an installation or public
cache can unblock manifest resolution. BlizzTrack fallback accepts only bytes
matching the requested config MD5. It never substitutes a nearby build. Public
cache scans were throttled after rate limiting; unresolved requests are retained
in the report rather than treated as missing metadata.

## Downloader changes

- Prefer the selected version's CDN region, then try all advertised regions.
  `--cdn-region` can restrict or reorder them. Verified working endpoints are
  preferred for subsequent requests without blacklisting other servers merely
  because one object returned 403/404.
- Accept live, historical, or local CDN tables through `--cdn-table`.
- Add `diagnose` with fresh per-endpoint HTTP, integrity, timing, and network
  results. Configuration checks never claim complete build availability.
- Separate catalog lookup errors from CDN retrieval errors and include URLs.
- Continue archive lookup past missing unrelated indexes. Keep failed indexes
  retryable and report an incomplete search if they prevent a definitive lookup.

Local HTTP regression tests cover regional fallback, endpoint preference,
diagnostics, catalog errors, unavailable-index continuation, and recovery of a
previously missing index. The expanded Python suite also covers cache validation/cleanup, GUI dispatch,
public-config recovery, layout accounting, and rate-limit handling.

Reproduce a target-build check with:

```powershell
py tools\casc_download.py diagnose --product odin --build 10296867 --output .\diagnose-10296867 --http-timeout 8
```
