#!/usr/bin/env python3
"""Refresh evidence for catalog builds without retaining probe downloads.

Probes verify actual bytes and hashes, not HEAD requests. A sampled build is
never labeled fully downloadable. JSONL output can be merged by casc_catalog.py.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import tempfile
import time

import casc_catalog as catalog
import casc_download as casc

ENDPOINT_MAP = casc.ENDPOINT_MAP


def historical_bases(path, endpoint_map=ENDPOINT_MAP):
    return casc.historical_cdn_bases(path, endpoint_map=endpoint_map)


def build_bases(row, timeout=15, endpoint_map=ENDPOINT_MAP):
    """Read actual advertised regions, then append documented historical hosts."""
    source = f"https://raw.githubusercontent.com/mdX7/ngdp_data/master/US/{row['product']}/cdns"
    try:
        bases = casc.cdn_bases(casc.parse_rows(casc.read_source(source, timeout)), row['region'])
    except (OSError, ValueError, casc.CascError):
        # The supplied source survey explicitly used this host and CDN path.
        casc.check(row.get('cdn_path'), 'no catalog CDN path; supply endpoints explicitly')
        path = row['cdn_path']
        casc.check(all(p and p not in ('.', '..') for p in path.split('/')), 'invalid catalog CDN path')
        bases = ['http://us.cdn.blizzard.com/' + path]
    bases = list(dict.fromkeys(bases + historical_bases(row.get('cdn_path'), endpoint_map)))
    observed = (row.get('probe') or {}).get('verified_bases', []) + (row.get('configuration_survey') or {}).get('verified_bases', [])
    # Only reorder endpoints present in the discovered map. Cached evidence
    # does not introduce arbitrary download hosts or blacklist other mirrors.
    preferred = [base for base in observed if base in bases]
    return list(dict.fromkeys(preferred + bases))


def select_samples(files, count, max_bytes):
    eligible = sorted((f for f in files if 0 < f['size'] <= max_bytes
                       and all(s['offset'] == 0 for s in f['spans'])), key=lambda f: (f['size'], f['path']))
    if not eligible:
        return []
    preferred = next((f for f in eligible if f['path'] == 'code_post_gfx.fp'), None)
    candidates = ([preferred] if preferred else []) + [eligible[0], eligible[len(eligible)//2], eligible[-1]] + eligible
    result, seen = [], set()
    for candidate in candidates:
        if candidate['path'] not in seen:
            result.append(candidate)
            seen.add(candidate['path'])
        if len(result) == count:
            break
    return result


def probe_build(row, *, bases=None, samples=3, max_sample_mib=32, timeout=10,
                config_only=False, config_fallback=True, archive_search=False, temp_parent=None):
    started = time.monotonic()
    result = {'product': row['product'], 'build': row['build'], 'build_config': row['build_config'],
              'cdn_config': row.get('cdn_config'), 'checked_at_utc': catalog.utc_now(),
              'stage': 'not_checked', 'scope': 'Configurations, manifests, and bounded samples; not a full-build audit.',
              'archive_search': archive_search, 'samples': []}
    try:
        casc.check(row.get('build_config'), 'catalog record has no build configuration key')
        bases = bases or build_bases(row, timeout)
        result['cdn_bases'] = bases
        # All configs, manifests, .part files and decoded sample files belong
        # to this context and are removed on success and on ordinary failure.
        with tempfile.TemporaryDirectory(prefix='cilo-probe-', dir=temp_parent) as directory:
            fallback = (lambda kind, key: casc.blizztrack_config_url(row['product'], kind, key)) if config_fallback else None
            storage = casc.Storage(bases, directory, row.get('cdn_config'), timeout=timeout,
                                   config_fallback=fallback, attempts=1)
            try:
                build = casc.Build(storage, row['build_config'])
            except (casc.CascError, OSError) as exc:
                result.update(stage='config_unverified' if 'configuration checksum mismatch' in str(exc) else 'not_retrieved', error=str(exc))
                return result
            result['stage'] = 'config_recovered' if storage.recovered_configs else 'config_verified'
            result['recovered_configs'] = storage.recovered_configs
            if row.get('cdn_config'):
                try:
                    storage.fetch('config', row['cdn_config'])
                    result['cdn_config_verified'] = True
                except (casc.CascError, OSError) as exc:
                    result.update(cdn_config_verified=False, cdn_config_error=str(exc))
            if config_only:
                return result
            # A bounded survey can avoid a very expensive archive search, but
            # must explicitly report that limitation for a loose-object miss.
            if not archive_search:
                storage.cdn_key = None
            records = build.download_records()
            result.update(stage='download_manifest_verified', objects=len(records),
                          encoded_bytes=sum(r['size'] for r in records))
            if 'vfs-root' in build.config:
                files = build.files()
                result.update(stage='manifests_verified', files=len(files))
                selected = select_samples(files, samples, max_sample_mib * casc.MIB)
                for file in selected:
                    try:
                        result['samples'].append({'ok': True, **casc.extract_file(storage, file)})
                    except (casc.CascError, OSError) as exc:
                        result['samples'].append({'ok': False, 'path': file['path'], 'error': str(exc)})
            else:
                # Generic games can still verify encoded BLTE bytes without
                # supporting their root format or decrypting their assets.
                eligible = sorted((r for r in records if 0 < r['size'] <= max_sample_mib*casc.MIB), key=lambda r: r['size'])
                result['sample_format'] = 'encoded BLTE; no named extraction assertion'
                for record in eligible[:samples]:
                    try:
                        path = storage.fetch('data', record['key'], record['size'])
                        result['samples'].append({'ok': True, 'key': record['key'], 'bytes': record['size'],
                                                  'sha256': casc.hash_file(path)})
                    except (casc.CascError, OSError) as exc:
                        result['samples'].append({'ok': False, 'key': record['key'], 'error': str(exc)})
            if result['samples'] and all(r['ok'] for r in result['samples']):
                result['stage'] = 'sample_verified'
            elif any(r['ok'] for r in result['samples']):
                result['stage'] = 'sample_partial'
            else:
                result['error'] = 'No sampled payload verified; see sample errors and archive_search scope.'
    except (casc.CascError, OSError, ValueError) as exc:
        result['error'] = str(exc)
    finally:
        result['seconds'] = round(time.monotonic() - started, 3)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=catalog.BUNDLED_CATALOG)
    parser.add_argument('--product', action='append', help='repeat for multiple products; omitted means every catalog record')
    parser.add_argument('--build', action='append')
    parser.add_argument('--output', type=Path, required=True, help='append-only JSONL observations')
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--max-sample-mib', type=int, default=32)
    parser.add_argument('--http-timeout', type=float, default=10)
    parser.add_argument('--cdn-base', action='append')
    parser.add_argument('--config-only', action='store_true')
    parser.add_argument('--archive-search', action='store_true', help='allow full CDN archive-index lookup after loose-object misses')
    parser.add_argument('--config-fallback', choices=['none', 'blizztrack'], default='none',
                        help='public cache requests are opt-in for bulk probes; honor that service\'s limits')
    args = parser.parse_args(argv)
    if not (1 <= args.jobs <= 8 and 1 <= args.samples <= 20 and 1 <= args.max_sample_mib <= 256 and 0 < args.http_timeout <= 120):
        parser.error('invalid probe resource limits')
    if args.config_fallback == 'blizztrack' and (args.jobs != 1 or not args.build or len(args.build) != 1):
        parser.error('public-cache probing requires --jobs 1 and one --build to avoid third-party request bursts')
    rows = [r for r in catalog.load_catalog(args.cache)['builds']
            if (not args.product or r['product'] in args.product) and (not args.build or r['build'] in args.build)]
    if not rows:
        parser.error('no matching catalog records')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def run(row):
        return probe_build(row, bases=args.cdn_base, samples=args.samples, max_sample_mib=args.max_sample_mib,
                           timeout=args.http_timeout, config_only=args.config_only,
                           config_fallback=args.config_fallback == 'blizztrack', archive_search=args.archive_search)
    with args.output.open('a', encoding='utf-8') as output, concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        # Keep a bounded queue even for the 35k-record catalog.
        pending, iterator = {}, iter(rows)
        while True:
            while len(pending) < args.jobs:
                row = next(iterator, None)
                if row is None:
                    break
                pending[pool.submit(run, row)] = row
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for task in done:
                pending.pop(task)
                result = task.result()
                output.write(json.dumps(result) + '\n')
                output.flush()
                print(json.dumps({'product': result['product'], 'build': result['build'], 'stage': result['stage']}), flush=True)


if __name__ == '__main__':
    main()
