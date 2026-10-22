#!/usr/bin/env python3
"""Discover CDN endpoints and audit exact config URLs with reproducible JSON evidence.

Discovery records every returned region and explicit historical revision. Matrix
checks are GET+MD5, keep transport errors distinct, and remove temporary files.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse

import casc_catalog as catalog
import casc_download as casc


def read_once(url, timeout):
    with tempfile.TemporaryDirectory(prefix='cilo-map-') as directory:
        path = casc.http_download(url, Path(directory) / 'response', limit=16*casc.MIB, attempts=1, timeout=timeout)
        return path.read_bytes()


def http_failure(exc):
    body = exc.read(8192).decode('utf-8', errors='replace')
    result = {'http_status': exc.code, 'error': str(exc), 'error_kind': 'http_response'}
    if 'upstream connect error' in body or 'envoy://cloudflare_' in body:
        result['error_kind'] = 'intermediary_transport'
        result['error'] = 'Intermediary failed to reach upstream; this is not evidence of CDN object absence.'
    if 's3:ListBucket' in body:
        result['s3_list_bucket_denied'] = True
    if exc.code == 429:
        result['error_kind'] = 'rate_limited'
        result['retry_after'] = exc.headers.get('Retry-After')
    return result


def probe_config(base, key, timeout=10):
    result = {'base': base, 'key': key, 'url': base.rstrip('/') + '/' + casc.key_path('config', key),
              'checked_at_utc': catalog.utc_now(), 'verified': False}
    started = time.monotonic()
    try:
        body = read_once(result['url'], timeout)
        digest = casc.md5(body).hex()
        result.update(http_status=200, bytes=len(body), md5=digest, verified=digest == key)
        if not result['verified']:
            result['error_kind'] = 'hash_unverified'
    except urllib.error.HTTPError as exc:
        result.update(http_failure(exc))
    except (OSError, casc.CascError) as exc:
        result.update(error=str(exc), error_kind='transport_or_validation')
    result['seconds'] = round(time.monotonic() - started, 3)
    return result


def discover(rows, revision, history_products, timeout=10):
    sources = [(product, revision) for product in sorted({r['product'] for r in rows})]
    history_errors = []
    for product in history_products:
        page = 1
        while True:
            url = f'https://api.github.com/repos/mdX7/ngdp_data/commits?path=US/{product}/cdns&per_page=100&page={page}'
            try:
                commits = json.loads(read_once(url, timeout))
                casc.check(isinstance(commits, list), 'invalid GitHub history response')
                sources.extend((product, commit['sha']) for commit in commits)
                if len(commits) < 100:
                    break
                page += 1
            except (OSError, ValueError, KeyError, casc.CascError) as exc:
                history_errors.append({'product': product, 'url': url, 'error': str(exc)})
                break
    sources = list(dict.fromkeys(sources))
    endpoints, failures = {}, []
    for product, rev in sources:
        url = f'https://raw.githubusercontent.com/mdX7/ngdp_data/{rev}/US/{product}/cdns'
        try:
            table = casc.parse_rows(read_once(url, timeout))
            for row in table:
                for base in casc.cdn_bases([row], preferred=row['Name']):
                    item = endpoints.setdefault(base, {'base': base, 'path': row['Path'].strip('/'), 'regions': [], 'sources': []})
                    if row['Name'] not in item['regions']:
                        item['regions'].append(row['Name'])
                    if url not in item['sources']:
                        item['sources'].append(url)
        except (OSError, ValueError, casc.CascError) as exc:
            failures.append({'url': url, 'error': str(exc)})
    return {'schema': 1, 'observed_at_utc': catalog.utc_now(), 'catalog_revision': revision,
            'scope': 'Advertised endpoint candidates; table presence is not object availability.',
            'requested_tables': len(sources), 'table_failures': failures, 'history_failures': history_errors,
            'endpoints': sorted(endpoints.values(), key=lambda r: r['base'])}


def matrix_tasks(rows, endpoint_map, fields, explicit_bases=None):
    by_path = collections.defaultdict(list)
    for endpoint in endpoint_map['endpoints']:
        by_path[endpoint['path']].append(endpoint['base'])
    refs = collections.defaultdict(list)
    unaddressed = []
    for row in rows:
        if not (explicit_bases or by_path.get(row.get('cdn_path'))):
            unaddressed.append({'product': row['product'], 'build': row['build'], 'path': row.get('cdn_path')})
            continue
        for field in fields:
            if row.get(field):
                refs[(row.get('cdn_path'), row[field])].append({'product': row['product'], 'build': row['build'], 'field': field})
    tasks = []
    for (path, key), references in refs.items():
        for base in dict.fromkeys(explicit_bases or by_path[path]):
            tasks.append((base, key, path, references))
    return tasks, unaddressed


def run_matrix(tasks, output, jobs=4, timeout=10):
    counts = collections.Counter()
    # A rate-limited endpoint is paused for this run, not silently hammered by
    # thousands of queued tasks. Skipped requests remain explicitly unresolved.
    paused = set()
    def run(task):
        base, key, path, references = task
        return {**probe_config(base, key, timeout), 'path': path, 'references': references}
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        pending, iterator = {}, iter(tasks)
        while True:
            while len(pending) < jobs:
                task = next(iterator, None)
                if task is None:
                    break
                if urllib.parse.urlsplit(task[0]).netloc in paused:
                    result = {'base': task[0], 'key': task[1], 'path': task[2], 'references': task[3],
                              'checked_at_utc': catalog.utc_now(), 'verified': False,
                              'error_kind': 'not_attempted_after_rate_limit'}
                    output.write(json.dumps(result) + '\n')
                    counts[result['error_kind']] += 1
                    continue
                pending[pool.submit(run, task)] = task
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                pending.pop(future)
                result = future.result()
                if result.get('http_status') == 429:
                    paused.add(urllib.parse.urlsplit(result['base']).netloc)
                output.write(json.dumps(result) + '\n')
                output.flush()
                counts['verified' if result['verified'] else result.get('error_kind', str(result.get('http_status')))] += 1
    return dict(counts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'matrix', 'inspect'])
    parser.add_argument('--cache', type=Path, default=catalog.BUNDLED_CATALOG)
    parser.add_argument('--product', action='append')
    parser.add_argument('--family', help='select products containing this literal text, e.g. odin')
    parser.add_argument('--all-products', action='store_true')
    parser.add_argument('--history-product', action='append', default=[])
    parser.add_argument('--map', type=Path, default=casc.ENDPOINT_MAP)
    parser.add_argument('--cdn-base', action='append')
    parser.add_argument('--kind', choices=['build_config', 'cdn_config', 'both'], default='both')
    parser.add_argument('--build-config-file', type=Path)
    parser.add_argument('--cdn-config-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--http-timeout', type=float, default=10)
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 8 or not 0 < args.http_timeout <= 120:
        parser.error('invalid concurrency or timeout')
    if args.command == 'inspect':
        if not args.build_config_file and not args.cdn_config_file:
            parser.error('inspect requires a build or CDN config file')
        build = casc.parse_config(args.build_config_file.read_bytes()) if args.build_config_file else {}
        cdn = casc.parse_config(args.cdn_config_file.read_bytes()) if args.cdn_config_file else None
        result = casc.describe_layout(build, cdn)
    else:
        data = catalog.load_catalog(args.cache)
        products = args.product or ([] if args.family or args.all_products else ['odin'])
        rows = [r for r in data['builds'] if args.all_products or r['product'] in products
                or (args.family and args.family in r['product'])]
        if not rows:
            parser.error('no matching catalog rows')
        if args.command == 'discover':
            result = discover(rows, data['catalog_revision'], args.history_product, args.http_timeout)
        else:
            endpoint_map = json.loads(args.map.read_text())
            fields = ['build_config', 'cdn_config'] if args.kind == 'both' else [args.kind]
            tasks, missing = matrix_tasks(rows, endpoint_map, fields, args.cdn_base)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('w', encoding='utf-8') as output:
                output.write(json.dumps({'type': 'scope', 'rows': len(rows), 'requests': len(tasks),
                                         'unaddressed_rows': missing, 'fields': fields}) + '\n')
                result = run_matrix(tasks, output, args.jobs, args.http_timeout)
                output.write(json.dumps({'type': 'summary', 'counts': result}) + '\n')
            print(json.dumps(result))
            return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'output': str(args.output)}))


if __name__ == '__main__':
    main()
