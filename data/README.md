# Availability data

`casc-availability.json.gz` is the GUI's bundled cache and the file fetched by its
**Update availability from GitHub** button. It contains all 35,536 distinct
US/global records from 500 products in the supplied build list, derived from
`mdX7/ngdp_data` revision `d8bcfafa67e7408c95d33caa3733729aff17c6fe`.

The imported observations were completed at `2026-10-22T15:21:47Z` and checked
configuration URLs through the US endpoint. They are explicitly labeled
`imported_us_config_survey`; they are not new independent payload checks. More
recent probes retain their exact product/config identities, time, scope, sample
hashes, and failures. No row claims a fully downloadable game from a sampled
check. The catalog includes launcher components and other non-game products;
inclusion does not imply the downloader supports that product's formats.

The gzip contains schema version 1 JSON. `builds` records include product, build
ID, catalog directory, version, regions, build/CDN keys, storage path, evidence
level, observation time, and provenance. The original source aliases are retained.
`cdn-endpoints.json` records historical endpoint provenance, storage paths and
regions. A listed endpoint is a candidate, not a promise that it retains an object.

## Rebuild and refresh

```sh
python tools/casc_catalog.py --build-list Every-US-CASC-Build-ngdp_data.txt --output data/casc-availability.json.gz

# Verify actual configs, manifests and three bounded payload samples per build.
# Every probe owns a temporary directory, removed on success or failure.
python tools/casc_probe.py --product odin --output probe-results.jsonl --jobs 2
python tools/casc_catalog.py --input data/casc-availability.json.gz --probes probe-results.jsonl --output data/casc-availability.json.gz

# A deeper single-build check allows archive-index fallback and public config recovery.
python tools/casc_probe.py --product brynhildr_odin --build 10885062 --jobs 1 --archive-search --config-fallback blizztrack --output probe-results.jsonl
```

Omit `--product` to probe all records; that can take a long time and considerable
bandwidth. `--config-only` limits the run to configs. The default sample cap is
32 MiB per file and three files per build; metadata has separate bounded limits.
JSONL output is appended as each build finishes. Publish the refreshed gzip in
GitHub only after reviewing the probe scope and errors. The GUI fetches the
published file; it does not trigger a remote cache run.

Bulk probes disable third-party config caches by default. Public-cache probing
is limited to a single selected build and one worker to avoid request bursts.
An HTTP 429 or transient transport error remains unresolved rather than becoming
a missing-file claim. Normal CLI downloads can use public config recovery when
needed. Verified original configs can also be seeded in their output cache.
