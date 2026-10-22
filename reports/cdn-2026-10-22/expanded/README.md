# Expanded CDN observations — 2026-10-22 UTC

The retail matrix verified 101 of 315 build configurations across 14 working
endpoint variants (4,410 requests), versus 87 on the original US/Korean pair.
The 14 additional configs were found on advertised historical Akamai hosts.
Configuration success never establishes full-build availability.

The Odin-family inventory contains 2,192 records from 27 products. Its US-only
survey made 3,917 distinct config requests: 119 verified, 187 returned HTTP 200
with a different raw MD5, and the rest returned HTTP/transport failures. No
decryption interpretation is asserted for the unverified bodies.

The original retail payload sweep checked DOWNLOAD/TVFS manifests for 87 builds
and one `code_post_gfx.fp` file per build: 86 verified; build 7547738 failed that
payload sample. The separate companion-product recovery verified one
`brynhildr.sys` file from `brynhildr_odin` 10885062. These are bounded samples.

Files:

- `retail-endpoint-matrix.csv` and `.jsonl.gz`: every initial retail endpoint result.
- `additional-retail-configs.json`: the 14 gains, exact keys and successful bases.
- `odin-family-configs.csv` / `family-config-probes.jsonl.gz`: distinct config checks.
- `payload-observations.jsonl`: original retail and companion payload evidence.
- `akamai-payload-probes.jsonl.gz`: later three-sample attempts for the 14 gains.
- `archive-index-probes.jsonl.gz`: old and newer archive-index checks; failed requests leave gaps.
- `old-akamai-index-probes.jsonl.gz`: follow-up historical archive-index requests.
- `endpoint-controls.json`: known valid, requested, and deliberately invalid keys.
- `7547738-samples.json`: 24 distributed small loose-file checks, without archive fallback.
- `layout-accounting.json`: every field in 119 retained, hash-verified configs.
- `blizztrack-*.jsonl.gz`: public-cache requests, including rate limits and unresolved failures.
- `summary.json`: machine-readable counts and remaining coverage limits.

Earlier scripts did not timestamp each request. Their config observations use
the run's output modification time as a completion bound and say so explicitly.
Payload and newer observations carry their own UTC timestamps. HTTP 503 bodies
were not retained by earlier scripts, so those cannot be retrospectively
attributed to Blizzard versus an intermediary.

During the follow-up run, fresh controls showed the environment proxy returning
`envoy://cloudflare_https_tunnel/` connection errors for multiple unrelated
services. Follow-up failures are not used to erase earlier verified evidence.
Successful bytes still undergo the same hash checks. The catalog retains the
timestamps and scopes of those earlier successes plus later attempt details.

All measurements came from one cloud environment. Regional hostnames do not
constitute measurements from physical machines in each region. The complete
historical-host matrix for vendor/dev paths, all-payload verification, legacy
ENCODING resolution, patch reconstruction, encrypted configs, and nested TVFS
mounting remain open. See [the findings](../../../docs/CDN_FINDINGS.md).
