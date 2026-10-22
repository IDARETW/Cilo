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

## All 315 retail odin build configurations

The build/config pairs came from the uploaded list derived from catalog snapshot
`d8bcfafa67e7408c95d33caa3733729aff17c6fe`. Each build config was requested from:

```text
http://us.cdn.blizzard.com/tpr/odin/config/AA/BB/KEY
http://blizzard.gcdn.cloudn.co.kr/tpr/odin/config/AA/BB/KEY
```

Four concurrent requests, an 8-second socket timeout, and at most one retry on
transient errors were used. Successful bodies were checked against the requested
MD5. Final results were identical on both hosts:

| Build configuration result | Builds |
|---|---:|
| HTTP 200 and correct MD5 | 87 |
| HTTP 404 | 228 |
| Total | 315 |

These results matched the uploaded list's build-configuration classifications;
this survey did not recover additional build configs. The complete
[CSV](../reports/cdn-2026-10-22/odin-build-configs.csv) records every build, key,
status, and verification result. **This is not a count of fully downloadable
game builds.** Payload completeness was not checked for all 315 builds.

## Payload checks and limits

The updated downloader downloaded and extracted `code_post_gfx.fp` from builds
`10056273`, `10885062`, and `26668155`. BLTE encoding/chunk verification passed,
TVFS lengths matched, and output SHA-256 hashes were recorded in the
[payload report](../reports/cdn-2026-10-22/payload-checks.json).
An additional extraction of `common.ff` from `11521872` succeeded with CDN
selection restricted to the Korean region (`--cdn-region kr`).

Build `7547738` illustrates why a verified config is insufficient: its build
config, DOWNLOAD manifest, TVFS root, CDN config, and loose-file index were
retrievable. Requests for two selected payloads returned 404 on both the US and
Korean endpoints. Four sampled archive indexes and the archive-group index also
returned 404 on the US endpoint. The full archive search was not completed, so
this is not a claim that every payload is gone.

For a build with an unavailable configuration, a locally cached original config
from an installation or a known working mirror can provide the missing manifest
keys. A config can be placed in `OUTPUT/cdn/config/AA/BB/KEY`; the downloader
requires its MD5 to match `KEY` before using it. That can unblock metadata
resolution, but it does not guarantee the referenced payloads are available.

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
previously missing index. The Python suite contains 14 passing tests.

Reproduce a target-build check with:

```powershell
py tools\casc_download.py diagnose --product odin --build 10296867 --output .\diagnose-10296867 --http-timeout 8
```
