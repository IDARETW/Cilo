# Odin CASC CDN endpoint map

Evidence snapshot: **2026-10-22 UTC**. This document compiles the saved investigation;
it does not claim that every endpoint was retested when this Markdown file was written.

This is the complete **discovered Odin-family endpoint inventory in this investigation**:
**30 normalized game-storage base URLs, 9 hostnames, 4 game-storage paths**, covering
27 catalog products. **17 of the 30 bases use `tpr/odin`**, the retail storage path.
The other 13 are listed separately. This is not a global inventory of every Blizzard
product, undisclosed origin, physical edge server, or future CDN hostname.

## Contents

1. [Scope and evidence](#scope-and-evidence)
2. [Hostname and path grid](#hostname-and-path-grid)
3. [Retail odin endpoints](#retail-odin-endpoints)
4. [Other Odin-family endpoints](#other-odin-family-endpoints)
5. [Product-to-path inventory](#product-to-path-inventory)
6. [Object URL layout](#object-url-layout)
7. [Metadata and configuration discovery](#metadata-and-configuration-discovery)
8. [Historical sources and exact server strings](#historical-sources-and-exact-server-strings)
9. [Known builds and working controls](#known-builds-and-working-controls)
10. [Failure interpretation and remaining gaps](#failure-interpretation-and-remaining-gaps)
11. [Reproducing the investigation](#reproducing-the-investigation)
12. [Source and evidence index](#source-and-evidence-index)
13. [Copyable endpoint list](#copyable-endpoint-list)

## Scope and evidence

- Catalog snapshot: `mdX7/ngdp_data` commit `d8bcfafa67e7408c95d33caa3733729aff17c6fe`.
- Inventory inputs: 27 product CDN tables at that snapshot, plus 18 historical
  `US/odin/cdns` revisions dated 2019-10-22 through 2025-12-11.
- These provide **29 advertised base/path combinations**. The user-supplied
  `https://level3.blizzard.com/tpr/odin` spelling supplies the 30th; it has a
  successful control check but is not asserted to be advertised in those tables.
- There are 19 distinct retail table source revisions when counting the catalog
  snapshot as well as the 18 historical revisions. Historical commit dates are
  table-observation dates, not proven endpoint deployment or retirement dates.
- Retail survey: **315 `odin` build records**, **4,410 requests over 14 bases**,
  **101 build records with a verified build config**. The same config hash can
  belong to more than one build record; these are not counts of unique objects.
- Payload evidence: **87 distinct `odin` build IDs with at least one downloaded,
  hash-verified game file**: 86 in the original single-file sweep, plus `7276347`
  in the later Akamai probe. No complete game build was audited.
- Family survey: 2,192 records, 27 products, 3,917 distinct config URL requests
  through the US endpoint for each storage path; 119 configs verified, 187 raw
  HTTP-200 bodies did not match the requested config MD5.
- All requests originated from one cloud environment. A regional hostname does
  not establish that testing occurred physically inside that region.

`Verified` means the requested bytes passed the relevant CASC integrity checks.
`Advertised` means a source table contained the endpoint. `Untested` and transient
failures remain separate from verified absence. No DNS/IP/CNAME topology census
was performed; names are retained instead of guessing edge IPs.

## Hostname and path grid

`H` = HTTP observed in the inventory; `S` = HTTPS observed. A dash means that
combination was not discovered in the saved inputs. It does not prove the URL
cannot work. This grid does not invent every possible host/path combination.

| Hostname | `tpr/odin` | `tpr/odinv` | `tpr/odind` | `tpr/odine` |
|---|---|---|---|---|
| `blizzard.gcdn.cloudn.co.kr` | H (E01) / S (E09) | — | — | H (E21) / S (E23) |
| `blzddist1-a.akamaihd.net` | H (E02) / S (E10) | — | — | — |
| `blzddistkr1-a.akamaihd.net` | H (E03) / S (E11) | — | — | — |
| `eu.cdn.blizzard.com` | H (E04) / S (E12) | H (E25) / S (E28) | — | — |
| `gcp.cdn.blizzard.com` | H (E05) / S (E13) | — | — | — |
| `kr.cdn.blizzard.com` | H (E06) / S (E14) | — | — | — |
| `level3.blizzard.com` | H (E07) / S (E15) | H (E26) | H (E18) | H (E22) |
| `level3.ssl.blizzard.com` | S (E16) | S (E29) | S (E19) | S (E24) |
| `us.cdn.blizzard.com` | H (E08) / S (E17) | H (E27) / S (E30) | S (E20) | — |

`level3.blizzard.com` and `level3.ssl.blizzard.com` are different hostnames.
HTTP and HTTPS are separate tested variants. Hostname text alone is not proof
of the current CDN operator or backing storage architecture.

## Retail odin endpoints

The region column is the union of CDN-table `Name` rows advertising the base.
For user-supplied E15, `us` is the map's recorded association, not a table claim.
These are region preferences, not an access-control rule or physical geolocation.
Source IDs resolve to exact URLs in the appendix.

| ID | Full base URL | Advertised regions | 315-build matrix: MD5-verified records | Other matrix outcomes | Sources |
|---|---|---|---:|---|---|
| E01 | `http://blizzard.gcdn.cloudn.co.kr/tpr/odin` | kr | 80/315 | 404: 221, 503: 14 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45, S46 |
| E02 | `http://blzddist1-a.akamaihd.net/tpr/odin` | eu, kr, us | 99/315 | 404: 216 | S02, S03, S04, S06, S07, S08, S44, S45 |
| E03 | `http://blzddistkr1-a.akamaihd.net/tpr/odin` | kr | 99/315 | 404: 216 | S03, S06, S07, S44 |
| E04 | `http://eu.cdn.blizzard.com/tpr/odin` | eu | 87/315 | 404: 228 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45 |
| E05 | `http://gcp.cdn.blizzard.com/tpr/odin` | eu | Not in full matrix | 7-key controls only: 403: 7 | S44 |
| E06 | `http://kr.cdn.blizzard.com/tpr/odin` | kr | 87/315 | 404: 228 | S02, S04, S05, S06, S08, S11, S13, S14, S16, S17, S18, S23, S25, S45 |
| E07 | `http://level3.blizzard.com/tpr/odin` | eu, kr, us | 87/315 | 403: 209, transport: 19 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45, S46 |
| E08 | `http://us.cdn.blizzard.com/tpr/odin` | us | 86/315 | 404: 228, 503: 1 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45 |
| E09 | `https://blizzard.gcdn.cloudn.co.kr/tpr/odin` | kr | Not in full matrix | 7-key controls only: 503: 7 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45, S46 |
| E10 | `https://blzddist1-a.akamaihd.net/tpr/odin` | eu, kr, us | 97/315 | 404: 214, 503: 4 | S02, S03, S04, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S44, S45, S46 |
| E11 | `https://blzddistkr1-a.akamaihd.net/tpr/odin` | kr | 96/315 | 404: 215, 503: 4 | S02, S03, S04, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S44, S45, S46 |
| E12 | `https://eu.cdn.blizzard.com/tpr/odin` | eu | 68/315 | 404: 228, 503: 19 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45 |
| E13 | `https://gcp.cdn.blizzard.com/tpr/odin` | eu | Not in full matrix | 7-key controls only: transport: 7 | S44 |
| E14 | `https://kr.cdn.blizzard.com/tpr/odin` | kr | 69/315 | 404: 228, 503: 18 | S02, S04, S05, S06, S08, S11, S13, S14, S16, S17, S18, S23, S25, S45 |
| E15 | `https://level3.blizzard.com/tpr/odin` | us | 83/315 | 403: 154, 503: 4, transport: 74 | S01 |
| E16 | `https://level3.ssl.blizzard.com/tpr/odin` | eu, kr, us | 84/315 | 403: 177, 503: 3, transport: 51 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45, S46 |
| E17 | `https://us.cdn.blizzard.com/tpr/odin` | us | 70/315 | 404: 228, 503: 17 | S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S13, S14, S15, S16, S17, S18, S23, S25, S44, S45 |

The matrix contains build-config checks, not manifests or payload completeness.
14 bases were selected after successful controls. HTTPS Korean GCDN and both GCP
variants were omitted from the full matrix because none of their seven controls
verified in that run. They remain in the inventory, rather than being deleted
from the investigation map. In the saved control report there are **119 checks =
17 distinct retail bases × 7 keys**.

The earlier two-base survey verified 87 retail configs. The expanded matrix found
14 additional build records on historical Akamai bases. Endpoint-specific totals
vary because object retention and transient failures vary; take the union of
verified objects, not the maximum per-host count or the sum across mirrors.

### Seven-key control results per retail base

These are a separate run from the 315-build matrix. A control labeled `user`
refers to the companion-product key supplied in the conversation, not a retail
`odin` game config. HTTP-200 controls in this report passed the requested MD5.

| Endpoint | Verified control labels | Observed outcomes |
|---|---|---|
| E01 | current, mid, early | 200: 3, 404: 4 |
| E02 | mid, early, user | 200: 3, 404: 4 |
| E03 | mid, early, user | 200: 3, 404: 4 |
| E04 | current, mid, early | 200: 3, 404: 4 |
| E05 | None | 403: 7 |
| E06 | current, mid, early | 200: 3, 404: 4 |
| E07 | current, mid, early | 200: 3, 403: 2, transport: 2 |
| E08 | current, mid, early | 200: 3, 404: 4 |
| E09 | None | 503: 7 |
| E10 | mid, early, user | 200: 3, 404: 4 |
| E11 | mid, early, user | 200: 3, 404: 4 |
| E12 | current, mid, early | 200: 3, 404: 4 |
| E13 | None | transport: 7 |
| E14 | current, mid, early | 200: 3, 404: 4 |
| E15 | current, mid, early | 200: 3, 403: 2, transport: 2 |
| E16 | current, mid, early | 200: 3, 403: 4 |
| E17 | current, mid, early | 200: 3, 404: 4 |

## Other Odin-family endpoints

These use separate storage namespaces. A failed `tpr/odin` request is not
silently retried under a vendor/development path and accepted as another build.
Use the product's actual `cdns` table and exact config keys. The family config
survey used one US base per path; a complete per-host family matrix remains open.
The table below records discovery provenance, not 315-build retail results.

| ID | Full base URL | Advertised regions | Sources |
|---|---|---|---|
| E18 | `http://level3.blizzard.com/tpr/odind` | eu, us | S24, S26 |
| E19 | `https://level3.ssl.blizzard.com/tpr/odind` | eu, us | S24, S26 |
| E20 | `https://us.cdn.blizzard.com/tpr/odind` | eu, us | S24, S26 |
| E21 | `http://blizzard.gcdn.cloudn.co.kr/tpr/odine` | kr | S27 |
| E22 | `http://level3.blizzard.com/tpr/odine` | eu, kr, us | S27 |
| E23 | `https://blizzard.gcdn.cloudn.co.kr/tpr/odine` | kr | S27 |
| E24 | `https://level3.ssl.blizzard.com/tpr/odine` | eu, us | S27 |
| E25 | `http://eu.cdn.blizzard.com/tpr/odinv` | eu | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |
| E26 | `http://level3.blizzard.com/tpr/odinv` | eu, us | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |
| E27 | `http://us.cdn.blizzard.com/tpr/odinv` | us | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |
| E28 | `https://eu.cdn.blizzard.com/tpr/odinv` | eu | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |
| E29 | `https://level3.ssl.blizzard.com/tpr/odinv` | eu, us | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |
| E30 | `https://us.cdn.blizzard.com/tpr/odinv` | us | S19, S20, S21, S22, S28, S29, S30, S31, S32, S33, S34, S35, S36, S37, S38, S39, S40, S41, S42, S43 |

## Product-to-path inventory

These are catalog product identifiers and their observed `Path` values; the
suffixes are not evidence that a build can be launched or that development
content is publicly readable. Companion products sharing `tpr/odin` remain
separate products. The retail-only count uses product **exactly `odin`**.

| Product | Catalog records | Observed game-storage path |
|---|---:|---|
| `brynhildr_odin` | 4 | `tpr/odin` |
| `brynhildr_odin2` | 1 | `tpr/odin` |
| `brynhildr_odin_qa` | 5 | `tpr/odinv` |
| `brynhildr_odin_qa2` | 1 | `tpr/odinv` |
| `brynhildr_odin_vendor` | 3 | `tpr/odinv` |
| `brynhildr_odin_vendor2` | 2 | `tpr/odinv` |
| `odin` | 315 | `tpr/odin` |
| `odina` | 1 | `tpr/odind` |
| `odinb` | 1 | `tpr/odin` |
| `odindev` | 50 | `tpr/odind` |
| `odine` | 27 | `tpr/odine` |
| `odinv1` | 200 | `tpr/odinv` |
| `odinv10` | 111 | `tpr/odinv` |
| `odinv11` | 2 | `tpr/odinv` |
| `odinv12` | 3 | `tpr/odinv` |
| `odinv13` | 38 | `tpr/odinv` |
| `odinv14` | 58 | `tpr/odinv` |
| `odinv15` | 1 | `tpr/odinv` |
| `odinv16` | 3 | `tpr/odinv` |
| `odinv2` | 343 | `tpr/odinv` |
| `odinv3` | 545 | `tpr/odinv` |
| `odinv4` | 303 | `tpr/odinv` |
| `odinv5` | 6 | `tpr/odinv` |
| `odinv6` | 18 | `tpr/odinv` |
| `odinv7` | 110 | `tpr/odinv` |
| `odinv8` | 35 | `tpr/odinv` |
| `odinv9` | 6 | `tpr/odinv` |

## Object URL layout

Let `BASE` be one exact endpoint above, `KEY` a lowercase 32-hex-character key,
`AA = KEY[0:2]`, and `BB = KEY[2:4]`. `KEY` is not a build number.

```text
BASE/
├── config/AA/BB/KEY       build config, CDN config, or referenced patch config
├── data/AA/BB/EKEY       BLTE-encoded manifest or loose game payload
├── data/AA/BB/ARCHIVE    archive blob, read by byte range for individual members
├── data/AA/BB/ARCHIVE.index
├── data/AA/BB/FILE_INDEX.index
├── patch/AA/BB/ARCHIVE   patch archive namespace; reconstruction not implemented
├── patch/AA/BB/ARCHIVE.index
└── patch/AA/BB/FILE_INDEX.index
```

The `patch` URLs are declared-layout templates from config fields and format
references. This investigation did not validate a full patch reconstruction.
Do not describe those templates as successful payload downloads merely because
a CDN config contains patch hashes.

| Input reference | How to use it | Validation / limitation |
|---|---|---|
| `versions.BuildConfig` | `BASE/config/AA/BB/KEY` | Raw config MD5 must equal requested key |
| `versions.CDNConfig` | Same `config` namespace | Lists archive/index references; raw MD5 checked |
| Build `download`, `install`, `encoding`, `size`, `vfs-root`, `vfs-N` | When a CKey/EKey pair is present, fetch the **EKey** under `data` | Decode BLTE; check encoded identity/chunks and decoded content as applicable |
| Build `patch-index` with CKey/EKey pair | EKey under `data` | This metadata reference does not imply patch reconstruction support |
| Build `patch-config` | Referenced key under `config` | Separate patch metadata; not a substitute build config |
| CDN `archives` | `data/AA/BB/ARCHIVE.index` locates member EKeys inside `data/AA/BB/ARCHIVE` | Verify index identity/footer/TOC/pages; fetch member range and validate member |
| CDN `file-index` | `data/AA/BB/FILE_INDEX.index` | Loose-object inventory; membership does not establish present-day reachability |
| CDN `patch-archives` | Equivalent archive/index URLs under `patch` | Declared layout; not an implemented restoration route |
| CDN `patch-file-index` | Loose index under `patch` | Declared layout; individual retention not audited |
| CDN `archive-group`, `patch-archive-group` | Client-generated combined-index identity | Not a required remotely published URL |
| `root` or any CKey without an EKey | Resolve through ENCODING where supported | A CKey is not automatically an object filename; current legacy CKey-only fallback is unsupported |
| Sizes, `build-name`, `build-uid`, feature/type fields | Metadata | Do not invent URLs from these fields |

**Archive names are not a promise that MD5(archive blob) equals that name.**
The downloader validates the index format/identity and requested encoded members.
For a member with offset `O` and encoded size `N`:

```http
GET /tpr/odin/data/AA/BB/ARCHIVE HTTP/1.1
Host: SELECTED_CDN_HOST
Range: bytes=O-(O+N-1)
```

Compute the range end numerically. The server must return a matching `206` and
`Content-Range`. A `200` full-archive response is not accepted as the requested
member range. No `.index` suffix is appended to the archive-blob request.

Modern index layouts accounted for here have offset widths of 0 bytes (loose),
4 bytes (individual archive), or 6 bytes (locally generated group). The index
parser checks checksums rather than treating any HTTP-200 body as an index.
[TACT.Net IndexFile.Write](https://github.com/wowdev/TACT.Net/blob/master/TACT.Net/Indices/IndexFile.cs)
skips publishing group indexes. Thus a guessed remote group-index 404 says
nothing about whether the individual archive members remain available.

### Concrete retail example: odin 11521872

These manifest EKeys come from the hash-verified build config. The manifest bytes
were used in the investigation; no present-day recheck is implied by listing them.

```text
Build config:
http://us.cdn.blizzard.com/tpr/odin/config/d8/8a/d88aae6a2bcd3944b48a6b80b5b46c26

DOWNLOAD manifest EKey:
http://us.cdn.blizzard.com/tpr/odin/data/4f/f8/4ff8ad8c5b30f3a3b0fcdf897f7b40b9

TVFS root EKey:
http://us.cdn.blizzard.com/tpr/odin/data/f0/c9/f0c90d14530d448b63b73012aa31ecda

INSTALL manifest EKey:
http://us.cdn.blizzard.com/tpr/odin/data/b9/20/b9209f1b74a18dbb01f97b1af2d5115e

ENCODING manifest EKey (declared by verified config):
http://us.cdn.blizzard.com/tpr/odin/data/2b/e3/2be37843bc53a7186fca5bd05fdbfdd3

Patch config (declared reference; separate retrieval not asserted):
http://us.cdn.blizzard.com/tpr/odin/config/7b/a4/7ba4dc25455364190381b74cc5f68579
```

## Metadata and configuration discovery

Game-storage endpoints and metadata services have different roles:

| Service / input | URL or template | Role / recorded coverage |
|---|---|---|
| Version catalog | `https://raw.githubusercontent.com/mdX7/ngdp_data/REV/REGION/PRODUCT/BUILD/versions` | Exact historical build/CDN/ProductConfig keys, region and version; a GitHub 404 is not a CDN object check |
| Product CDN catalog | `https://raw.githubusercontent.com/mdX7/ngdp_data/REV/REGION/PRODUCT/cdns` | `Name`, `Path`, `Hosts`, `Servers`, `ConfigPath`; current snapshot plus historical rows |
| Live Blizzard product metadata | `https://us.version.battle.net/v2/products/odin/cdns` | Returned the same current CDN table in the recorded check; metadata service, not a game payload base |
| GitHub table history | `https://api.github.com/repos/mdX7/ngdp_data/commits?path=US/odin/cdns&per_page=100&page=1` | Enumerate historical table revisions; paginate if necessary |
| Public cached build config | `https://blizztrack.com/config/PRODUCT/bc/KEY` | External cached HTML; accept only restored bytes matching exact config MD5 |
| Public cached CDN config | `https://blizztrack.com/config/PRODUCT/cdn/KEY` | Same strict integrity requirement; not a Blizzard game CDN |

Substitute the catalog revision above for `REV`. The raw catalog's root can use
`master` for current discovery, but a commit hash is needed for reproducibility.
`REGION` in a GitHub directory (for example `US`, `EU`, `KR`, `SG`, `TW`, `CN`)
and `Name` in the returned table (for example `us`, `eu`, `kr`) are distinct.
The six retail catalog-region copies compared in this investigation contained
the same CDN table, sequence 3433538; changing the GitHub folder alone did not
select a new physical CDN. Other live regional version-service hostnames were
not exhaustively enumerated or tested.

### ConfigPath is a separate namespace

The cached tables also advertise **`ConfigPath = tpr/configs/data`**. It is not
one of the four game-storage `Path` values, and is not counted among the 30
normalized game-storage bases. `ProductConfig` in `versions` is a separate key
from `BuildConfig` and `CDNConfig`; do not conflate these references.

For example, the saved `odin` 10296867 versions row contains:

```text
BuildConfig   = 92db83c9f6c501b5e288a506db05c506
CDNConfig     = ff08a1bb1357d9dd0155c4087eebc258
ProductConfig = 88a724e03042e031f9cedb367a024c75
ConfigPath    = tpr/configs/data
```

A **derived investigation candidate**, not a verified URL in these probe reports,
is `ORIGIN/tpr/configs/data/88/a7/88a724e03042e031f9cedb367a024c75`, using an origin
from that product's table. The current downloader does not consume ProductConfig
or route through ConfigPath. There is no evidence here that moving a missing
BuildConfig into ConfigPath recovers it. The raw table references in the appendix
preserve ConfigPath and routing fields for further investigation.

### Servers query parameters and normalization

`Servers` contains complete origin URIs; `Hosts` contains additional hostnames.
Examples include `?maxhosts=5` and `?maxhosts=4&fallback=1`. The downloader extracts
the HTTP/HTTPS origin, appends the table's game-storage `Path`, and removes these
routing hints from the object request. It also incorporates bare `Hosts` entries
as HTTP candidates. This explains why normalized bases differ from the literal
`Servers` strings. These parameters are not authentication tokens, and their
presence does not establish a separate object namespace. Parameter-sensitive
retention or availability was not tested systematically.

## Historical sources and exact server strings

### Retail CDN-table change history

These 18 commits were returned by the recorded `US/odin/cdns` history query.
The snapshot table is an additional source. A host may appear in several rows;
first/last table appearance is not a proven service lifetime.

| Commit | Commit time (UTC) | Table source |
|---|---|---|
| `856338e1ac527ffbbac1c4f7a4b9b7ae6d96e60e` | 2019-10-22T17:00:25Z | S12 |
| `6f58df0c2456e6046d668433d8d52ac3692542c3` | 2019-12-09T22:00:22Z | S10 |
| `fa2c1962a040e4b8ce391f04154ef4e4fad85c06` | 2019-12-09T23:00:21Z | S46 |
| `6881d5c666edc56d5a1f9c2cb3e350505e15cd49` | 2020-01-15T01:00:18Z | S09 |
| `d930ca588bf37b3918a832ebb0e9fb51e1690745` | 2020-03-09T19:00:25Z | S44 |
| `11021154a4696285c379877100c15707e16de53c` | 2020-03-11T15:30:27Z | S03 |
| `43ddfa4f93a393525579d5da566158bfa3ade4a8` | 2020-03-13T17:00:31Z | S07 |
| `d0f40770f6b3b80a3a7e36cbd225676a45702272` | 2020-03-16T15:30:42Z | S15 |
| `9e5435ce8de7d7cb596a1d7eca05e9d570ef9552` | 2020-10-09T19:00:19Z | S13 |
| `3a9c8ae091f70f8becf7b2494acb8c46e1c85412` | 2020-11-10T05:30:19Z | S06 |
| `d6b0e36fbb375f8be1c4fe4149b81b956041ac64` | 2020-11-20T17:30:24Z | S16 |
| `7885b2538dd50476301e09d69dfd89bf6f2ebb9f` | 2021-12-07T01:00:20Z | S11 |
| `a834c3d78d1ababa9c7fb686bfae108c5ceb2f77` | 2022-04-18T22:20:30Z | S14 |
| `0c8a49c13c3c1ead138e3fb65174addb71c27d34` | 2022-11-17T03:16:20Z | S02 |
| `57ef29b541d82f69d9f1304434ac932bd23b869d` | 2022-11-17T19:07:41Z | S08 |
| `e93c2b3080d8f5fbd3fd50945c233aca58e59ffe` | 2022-11-19T00:16:34Z | S45 |
| `26922160bf5857d8831b84d746c23ce0905d5217` | 2023-06-13T19:22:08Z | S04 |
| `374528c0a748e7b93da93c44df1ad71b3ddf8111` | 2025-12-11T20:33:05Z | S05 |

### Literal origin URI variants from the saved input tables

The following list preserves every distinct token in `Servers`, including its
query string. These are origin-level advertisements, **not object URLs**, and
have not each been independently probed with their original query parameters.
Region/path/source associations remain in the linked raw tables. Ordering within
a table may express preference; sorting this inventory does not preserve that
per-table priority.

```text
http://blizzard.gcdn.cloudn.co.kr
http://blizzard.gcdn.cloudn.co.kr/?maxhosts=4
http://blzddist1-a.akamaihd.net/?fallback=1&maxhosts=5
http://blzddist1-a.akamaihd.net/?maxhosts=4
http://blzddist1-a.akamaihd.net/?maxhosts=5
http://blzddistkr1-a.akamaihd.net/?maxhosts=4
http://eu.cdn.blizzard.com/?maxhosts=10
http://eu.cdn.blizzard.com/?maxhosts=4
http://eu.cdn.blizzard.com/?maxhosts=5
http://gcp.cdn.blizzard.com/?maxhosts=5
http://kr.cdn.blizzard.com/?maxhosts=4
http://level3.blizzard.com/?fallback=1
http://level3.blizzard.com/?maxhosts=10
http://level3.blizzard.com/?maxhosts=4
http://level3.blizzard.com/?maxhosts=5
http://us.cdn.blizzard.com/?maxhosts=10
http://us.cdn.blizzard.com/?maxhosts=4
http://us.cdn.blizzard.com/?maxhosts=5
https://blizzard.gcdn.cloudn.co.kr/?fallback=1&maxhosts=4
https://blizzard.gcdn.cloudn.co.kr/?maxhosts=4&fallback=1
https://blzddist1-a.akamaihd.net/?fallback=1
https://blzddist1-a.akamaihd.net/?fallback=1&maxhosts=4
https://blzddist1-a.akamaihd.net/?fallback=1&maxhosts=5
https://blzddistkr1-a.akamaihd.net/?fallback=1
https://blzddistkr1-a.akamaihd.net/?fallback=1&maxhosts=4
https://eu.cdn.blizzard.com/?fallback=1&maxhosts=10
https://eu.cdn.blizzard.com/?fallback=1&maxhosts=4
https://eu.cdn.blizzard.com/?maxhosts=4&fallback=1
https://gcp.cdn.blizzard.com/?fallback=1&maxhosts=4
https://kr.cdn.blizzard.com/?fallback=1&maxhosts=4
https://kr.cdn.blizzard.com/?maxhosts=4&fallback=1
https://level3.ssl.blizzard.com/?fallback=1&maxhosts=10
https://level3.ssl.blizzard.com/?fallback=1&maxhosts=4
https://level3.ssl.blizzard.com/?maxhosts=10
https://level3.ssl.blizzard.com/?maxhosts=10&fallback=1
https://level3.ssl.blizzard.com/?maxhosts=4&fallback=1
https://us.cdn.blizzard.com/?fallback=1&maxhosts=10
https://us.cdn.blizzard.com/?fallback=1&maxhosts=4
https://us.cdn.blizzard.com/?maxhosts=10
https://us.cdn.blizzard.com/?maxhosts=4&fallback=1
```

### Additional bare Hosts values

These are the distinct bare hostnames in the saved table `Hosts` columns.
`level3.ssl.blizzard.com` appears through `Servers` rather than necessarily as a
bare Host. The user-added HTTPS Level3 base is separately sourced.

```text
blizzard.gcdn.cloudn.co.kr
blzddist1-a.akamaihd.net
blzddistkr1-a.akamaihd.net
eu.cdn.blizzard.com
gcp.cdn.blizzard.com
kr.cdn.blizzard.com
level3.blizzard.com
us.cdn.blizzard.com
```

## Known builds and working controls

### Control key identities

All seven control keys were requested from each of the 17 retail bases. `missing`
is deliberately invalid. The `user` key belongs to a companion product, so its
success must not increase retail `odin` build counts.

| Recorded label | Exact build-config key | Identity / meaning |
|---|---|---|
| `current` | `0da91bc57ef9e333ac0183a037a27412` | Newer retail control; label describes the run, not a promise of latest release. |
| `mid` | `d88aae6a2bcd3944b48a6b80b5b46c26` | Retail odin 11521872. |
| `early` | `7edd7a7bc6be04b10d6cbb6547923489` | Retail odin 7547738. |
| `missing` | `00000000000000000000000000000000` | All-zero deliberately nonexistent control. |
| `user` | `ec4dc25c00b68ccf203cb0d1eaa77283` | brynhildr_odin 10885062 companion-product config. |
| `10296867` | `92db83c9f6c501b5e288a506db05c506` | User-reported retail build, still no verified config in recorded checks. |
| `10358353` | `bc90718ff5987302bf5b8496214ee32f` | Existing nearby catalog ID; distinct from requested 10358343. |

For the user's `ec4dc25c00b68ccf203cb0d1eaa77283` URL, Level3's S3 `ListBucket`
denial also occurred with the intentionally invalid all-zero key. A successful
control on that same service shows why the denial is not proof that the requested
object exists. Historical Akamai control requests **did** verify the user's exact
companion-product key. Separately, the public BlizzTrack page restored the exact
741-byte config and the companion driver payload was verified. These are distinct
successful routes; neither proves retail build availability.

The supplied catalog has no `odin/10358343/versions` record; `10358353` is a
different build. No substitution is made. `10296867` and `10358353` still had no
verified build config on the tested endpoints.

### Fourteen retail build records gained through historical Akamai

Each of these keys verified on all four US/Korean Akamai HTTP/HTTPS variants
in the expanded matrix. This table records config recovery. Later payload
attempts remain separate; only `7276347` yielded a verified sample in that
follow-up run. Build IDs `11227632` and `11281361` share the same build-config
key; their CDN configuration identities can differ.

| Retail build ID | Build-config key |
|---|---|
| `7243209` | `f15d9d3dced303bd94b215d47987cb04` |
| `7256861` | `238ac6541d796edadf352212fb4c044a` |
| `7274739` | `12e621f4fa7d684e48022e7b362f396e` |
| `7276347` | `c1dd35f0689b44fa2814a2966aa950a3` |
| `11116736` | `2ad3af877cd717561b96c9e3a7198db9` |
| `11155708` | `7226c2a891dc9d7faec1a93d15d9bfb1` |
| `11222167` | `b02fa6b4cf5a595e3d34b5a04227a45a` |
| `11227632` | `52d8fae9961bb41d6e251a119d585e5e` |
| `11281361` | `52d8fae9961bb41d6e251a119d585e5e` |
| `11330025` | `b4df96a5483a2627e894b49fbc85591c` |
| `11364056` | `bbea53e6a6c2bc27c9170565c7c0fa37` |
| `11383760` | `15da05151846cae6bd91477b516b0607` |
| `11419693` | `c801fcb80d0d9227e6ac1f61bb7a95cb` |
| `11494759` | `02d638c858724155c494e625f736a166` |

### Archive-retention example: odin 7547738

- Verified config, DOWNLOAD, TVFS and a 5,237-entry loose-file index.
- Two unsuccessful payload EKeys appear in that loose index:
  `beaa5b40500737959e9a369238320715` and `932f1e8ea509763cb6e1dd1b75c77242`.
  Membership is not proof of a successful fetch at the observation time.
- 24 distributed small loose-file samples failed on the tested US base without
  archive fallback. This is not an assertion that every file in that build is gone.
- The old 52-archive list checked on US/Korean bases produced only two verified
  index responses and multiple unresolved transient failures.
- A separate 171-index newer-CDN search verified 151 indexes, left 20 transient
  failures, and did not locate the target EKey in the readable indexes.
- Follow-up on both historical Akamai HTTP bases made 104 requests for the old
  52-index list: six responses verified three unique indexes, and 98 were 404.
  Neither target EKey was found in those three indexes. Other endpoints and
  unresolved earlier requests remain separate coverage gaps.

## Failure interpretation and remaining gaps

| Observation | Evidence it provides | Conclusion it does not establish |
|---|---|---|
| HTTP 200 + matching config MD5 | Exact config bytes retrieved | Every referenced manifest/payload is pullable |
| HTTP 200 + raw config MD5 mismatch | Body returned but rejected as verified config | Proven encryption, successful decoding, or correct build identity |
| HTTP 403 / S3 ListBucket denial | That exact request was denied | Object exists, object is deleted, or listing permission is required for readable objects |
| HTTP 404 | That URL returned not found at that time | Same key is absent from every mirror/archive/config cache |
| HTTP 429 | Rate limit encountered | Metadata or game object is absent |
| HTTP 503, timeout, tunnel error | Request failed transiently or through an intermediary | Blizzard origin definitively lacks that object |
| Successful index parse | Index bytes and identities verified | Every member blob remains fetchable |
| Successful sample extraction | Named/encoded bytes in that sample verified | Full build is available or ready to launch |

During later checks, unrelated services also returned
`envoy://cloudflare_https_tunnel/` connection errors from the environment proxy.
Those were not converted into deletion claims. Earlier 503 bodies were not always
saved, so their origin cannot be reconstructed afterwards. `transport` in the
matrix groups requests without a final HTTP status; consult the raw error text
rather than assuming every one was a DNS failure.

The 30-base list is complete relative to the saved discovery inputs. Remaining
work for broader coverage includes:

- Historical CDN-table traversal for every vendor/dev/companion product, rather
  than only retail `US/odin/cdns` history.
- A complete endpoint/config matrix for the 1,877 non-retail Odin-family records.
- Additional regional metadata history and current advertisements after the snapshot.
- Independent regional network vantage points, DNS/CNAME/IP topology, and edge-specific behavior.
- `ConfigPath`/ProductConfig retrieval and validation; parameter-sensitive server probes.
- Retrying transient config, manifest, sample and archive-index failures.
- Full payload audits instead of sample checks; patches, encrypted configs,
  legacy ENCODING resolution and nested TVFS mounts require more format support.

## Reproducing the investigation

Run from the Cilo repository with Python 3.10+ (`py` may replace `python` on Windows).
Discovery/probes use their own temporary directories and remove downloaded probe
bodies on normal completion or failure. JSON/JSONL reports are intentional outputs.

```sh
# Discover the 27-product family at the cache snapshot and retail table history.
python tools/casc_cdn_map.py discover --family odin --history-product odin --output endpoints.json

# Inspect every retail build/CDN config against bases for its recorded storage path.
# Both kinds are checked by default; these will be fresh observations.
python tools/casc_cdn_map.py matrix --product odin --map endpoints.json --output retail-matrix.jsonl --jobs 4

# Broader family matrix; potentially many thousands of requests.
python tools/casc_cdn_map.py matrix --family odin --map endpoints.json --output family-matrix.jsonl --jobs 4

# Fresh per-endpoint diagnostic evidence for a reported retail build.
python tools/casc_download.py diagnose --product odin --build 10296867 --output diagnose-10296867 --http-timeout 8

# Inspect config-declared object layout without implying object availability.
python tools/casc_download.py layout --product odin --build 11521872 --output layout-11521872

# Single-build config/manifest/sample probe; temporary downloaded bytes are removed.
python tools/casc_probe.py --product odin --build 7276347 --jobs 1 --samples 3 --output samples.jsonl
```

`discover` will regenerate advertised entries; re-add the user-supplied HTTPS
Level3 spelling explicitly if needed, because it is outside that table provenance.
Repeat `--history-product PRODUCT` to expand historical discovery. The matrix
uses bounded concurrency and pauses a rate-limited host for the run. A skipped
request stays unresolved. Normal CLI downloads additionally support exact
hash-verified public-cache recovery; bulk probes disable it by default to avoid
third-party request bursts. An explicit `--cdn-base` overrides automatic endpoint
selection. Keep exact product/build/config identities in every result.

A minimal manual hash check with temporary-file cleanup:

```python
import hashlib
from pathlib import Path
import tempfile
import urllib.request

key = 'd88aae6a2bcd3944b48a6b80b5b46c26'
base = 'http://us.cdn.blizzard.com/tpr/odin'
url = f'{base}/config/{key[:2]}/{key[2:4]}/{key}'
with tempfile.TemporaryDirectory(prefix='cilo-config-check-') as directory:
    path = Path(directory) / 'config'
    request = urllib.request.Request(url, headers={'User-Agent': 'Cilo-CDN-investigation'})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError('configuration response exceeds limit')
    path.write_bytes(body)
    if hashlib.md5(body).hexdigest() != key:
        raise ValueError('configuration MD5 mismatch')
    print(url, len(body), 'bytes; MD5 verified')
```

This snippet checks one raw config, not encrypted formats or a whole build.
Use GET and validate returned bytes; HEAD status alone does not verify CASC content.

## Source and evidence index

### Endpoint discovery sources

Every S-ID cited in the endpoint tables resolves here. All raw table URLs are
pinned to commits. The single user-supplied entry is explicitly identified.
- **S01:** User-supplied URL; known-good control downloaded and MD5 verified on 2026-10-22. URL: <https://level3.blizzard.com/tpr/odin/config/ec/4d/ec4dc25c00b68ccf203cb0d1eaa77283>.
- **S02:** <https://raw.githubusercontent.com/mdX7/ngdp_data/0c8a49c13c3c1ead138e3fb65174addb71c27d34/US/odin/cdns>
- **S03:** <https://raw.githubusercontent.com/mdX7/ngdp_data/11021154a4696285c379877100c15707e16de53c/US/odin/cdns>
- **S04:** <https://raw.githubusercontent.com/mdX7/ngdp_data/26922160bf5857d8831b84d746c23ce0905d5217/US/odin/cdns>
- **S05:** <https://raw.githubusercontent.com/mdX7/ngdp_data/374528c0a748e7b93da93c44df1ad71b3ddf8111/US/odin/cdns>
- **S06:** <https://raw.githubusercontent.com/mdX7/ngdp_data/3a9c8ae091f70f8becf7b2494acb8c46e1c85412/US/odin/cdns>
- **S07:** <https://raw.githubusercontent.com/mdX7/ngdp_data/43ddfa4f93a393525579d5da566158bfa3ade4a8/US/odin/cdns>
- **S08:** <https://raw.githubusercontent.com/mdX7/ngdp_data/57ef29b541d82f69d9f1304434ac932bd23b869d/US/odin/cdns>
- **S09:** <https://raw.githubusercontent.com/mdX7/ngdp_data/6881d5c666edc56d5a1f9c2cb3e350505e15cd49/US/odin/cdns>
- **S10:** <https://raw.githubusercontent.com/mdX7/ngdp_data/6f58df0c2456e6046d668433d8d52ac3692542c3/US/odin/cdns>
- **S11:** <https://raw.githubusercontent.com/mdX7/ngdp_data/7885b2538dd50476301e09d69dfd89bf6f2ebb9f/US/odin/cdns>
- **S12:** <https://raw.githubusercontent.com/mdX7/ngdp_data/856338e1ac527ffbbac1c4f7a4b9b7ae6d96e60e/US/odin/cdns>
- **S13:** <https://raw.githubusercontent.com/mdX7/ngdp_data/9e5435ce8de7d7cb596a1d7eca05e9d570ef9552/US/odin/cdns>
- **S14:** <https://raw.githubusercontent.com/mdX7/ngdp_data/a834c3d78d1ababa9c7fb686bfae108c5ceb2f77/US/odin/cdns>
- **S15:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d0f40770f6b3b80a3a7e36cbd225676a45702272/US/odin/cdns>
- **S16:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d6b0e36fbb375f8be1c4fe4149b81b956041ac64/US/odin/cdns>
- **S17:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin/cdns>
- **S18:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin2/cdns>
- **S19:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin_qa/cdns>
- **S20:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin_qa2/cdns>
- **S21:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin_vendor/cdns>
- **S22:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/brynhildr_odin_vendor2/cdns>
- **S23:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odin/cdns>
- **S24:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odina/cdns>
- **S25:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinb/cdns>
- **S26:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odindev/cdns>
- **S27:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odine/cdns>
- **S28:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv1/cdns>
- **S29:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv10/cdns>
- **S30:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv11/cdns>
- **S31:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv12/cdns>
- **S32:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv13/cdns>
- **S33:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv14/cdns>
- **S34:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv15/cdns>
- **S35:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv16/cdns>
- **S36:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv2/cdns>
- **S37:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv3/cdns>
- **S38:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv4/cdns>
- **S39:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv5/cdns>
- **S40:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv6/cdns>
- **S41:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv7/cdns>
- **S42:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv8/cdns>
- **S43:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d8bcfafa67e7408c95d33caa3733729aff17c6fe/US/odinv9/cdns>
- **S44:** <https://raw.githubusercontent.com/mdX7/ngdp_data/d930ca588bf37b3918a832ebb0e9fb51e1690745/US/odin/cdns>
- **S45:** <https://raw.githubusercontent.com/mdX7/ngdp_data/e93c2b3080d8f5fbd3fd50945c233aca58e59ffe/US/odin/cdns>
- **S46:** <https://raw.githubusercontent.com/mdX7/ngdp_data/fa2c1962a040e4b8ce391f04154ef4e4fad85c06/US/odin/cdns>

### Recorded results and machine-readable inputs
- [data/cdn-endpoints.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/data/cdn-endpoints.json) — Canonical normalized endpoint inventory with regions/source URLs.
- [data/casc-availability.json.gz](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/data/casc-availability.json.gz) — 35,536-record cache; filter product exactly odin for retail counts.
- [reports/cdn-2026-10-22/expanded/summary.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/summary.json) — Recorded survey totals and remaining gaps.
- [reports/cdn-2026-10-22/expanded/retail-endpoint-matrix.csv](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/retail-endpoint-matrix.csv) — All 4,410 retail config observations.
- [reports/cdn-2026-10-22/expanded/retail-endpoint-matrix.jsonl.gz](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/retail-endpoint-matrix.jsonl.gz) — Raw retail matrix, including errors.
- [reports/cdn-2026-10-22/expanded/endpoint-controls.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/endpoint-controls.json) — 119 controls across 17 distinct retail bases.
- [reports/cdn-2026-10-22/expanded/odin-family-configs.csv](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/odin-family-configs.csv) — 3,917 distinct family config URL observations.
- [reports/cdn-2026-10-22/expanded/additional-retail-configs.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/additional-retail-configs.json) — 14 retail records recovered on historical Akamai.
- [reports/cdn-2026-10-22/expanded/payload-observations.jsonl](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/payload-observations.jsonl) — Original retail/companion payload evidence.
- [reports/cdn-2026-10-22/expanded/akamai-payload-probes.jsonl.gz](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/akamai-payload-probes.jsonl.gz) — Follow-up attempts and the additional successful retail sample.
- [reports/cdn-2026-10-22/expanded/archive-index-probes.jsonl.gz](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/archive-index-probes.jsonl.gz) — Old and newer index attempts.
- [reports/cdn-2026-10-22/expanded/old-akamai-index-probes.jsonl.gz](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/old-akamai-index-probes.jsonl.gz) — 104 historical Akamai archive-index checks.
- [reports/cdn-2026-10-22/expanded/layout-accounting.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/expanded/layout-accounting.json) — Field accounting for 119 locally retained verified configs.
- [reports/cdn-2026-10-22/10296867-diagnostics.json](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/reports/cdn-2026-10-22/10296867-diagnostics.json) — Target build version row including ProductConfig and config failures.
- [docs/CDN_FINDINGS.md](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/docs/CDN_FINDINGS.md) — Narrative findings and original investigation context.
- [tools/casc_cdn_map.py](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/tools/casc_cdn_map.py) — Discovery/matrix tool.
- [tools/casc_download.py](https://github.com/IDARETW/Cilo/blob/bd021013bafd64832a58b614742d7a9dcac61fd5/tools/casc_download.py) — Implemented parser/layout/downloader behavior.

Results above use immutable source-commit links so this standalone Markdown file
can be copied outside the repository. Original scripts did not timestamp every
request: config summaries explicitly use run completion bounds where appropriate.
Payload/newer probe timestamps are recorded separately. Do not treat this document's
creation as a fresh availability observation.

## Copyable endpoint list

Exactly the 30 normalized game-storage bases above, without query parameters.
This list intentionally includes advertised endpoints whose tests failed.

```text
http://blizzard.gcdn.cloudn.co.kr/tpr/odin
http://blzddist1-a.akamaihd.net/tpr/odin
http://blzddistkr1-a.akamaihd.net/tpr/odin
http://eu.cdn.blizzard.com/tpr/odin
http://gcp.cdn.blizzard.com/tpr/odin
http://kr.cdn.blizzard.com/tpr/odin
http://level3.blizzard.com/tpr/odin
http://us.cdn.blizzard.com/tpr/odin
https://blizzard.gcdn.cloudn.co.kr/tpr/odin
https://blzddist1-a.akamaihd.net/tpr/odin
https://blzddistkr1-a.akamaihd.net/tpr/odin
https://eu.cdn.blizzard.com/tpr/odin
https://gcp.cdn.blizzard.com/tpr/odin
https://kr.cdn.blizzard.com/tpr/odin
https://level3.blizzard.com/tpr/odin
https://level3.ssl.blizzard.com/tpr/odin
https://us.cdn.blizzard.com/tpr/odin
http://level3.blizzard.com/tpr/odind
https://level3.ssl.blizzard.com/tpr/odind
https://us.cdn.blizzard.com/tpr/odind
http://blizzard.gcdn.cloudn.co.kr/tpr/odine
http://level3.blizzard.com/tpr/odine
https://blizzard.gcdn.cloudn.co.kr/tpr/odine
https://level3.ssl.blizzard.com/tpr/odine
http://eu.cdn.blizzard.com/tpr/odinv
http://level3.blizzard.com/tpr/odinv
http://us.cdn.blizzard.com/tpr/odinv
https://eu.cdn.blizzard.com/tpr/odinv
https://level3.ssl.blizzard.com/tpr/odinv
https://us.cdn.blizzard.com/tpr/odinv
```
