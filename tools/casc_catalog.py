#!/usr/bin/env python3
"""Build/load the compact availability catalog used by Cilo's desktop browser.

The source text is metadata, never executable instructions. Observations imported
from it retain their provenance and do not assert payload completeness.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
import urllib.parse

CATALOG_URL = 'https://raw.githubusercontent.com/IDARETW/Cilo/main/data/casc-availability.json.gz'
CATALOG_API_URL = 'https://api.github.com/repos/IDARETW/Cilo/contents/data/casc-availability.json.gz?ref=main'
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
BUNDLED_CATALOG = RESOURCE_ROOT / 'data/casc-availability.json.gz'
MAX_COMPRESSED = 24 * 1024**2
MAX_JSON = 96 * 1024**2
LABELS = {
    'sample_verified': 'Sample verified',
    'sample_partial': 'Some samples failed',
    'manifests_verified': 'Manifests verified',
    'download_manifest_verified': 'DOWNLOAD verified',
    'config_recovered': 'Config recovered',
    'config_verified': 'Config verified',
    'config_unverified': 'Config hash unverified',
    'not_retrieved': 'Config not retrieved',
    'not_checked': 'Not checked',
}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate_catalog(data):
    if not isinstance(data, dict) or data.get('schema') != 1:
        raise ValueError('unsupported availability catalog schema')
    rows = data.get('builds')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200000:
        raise ValueError('invalid catalog build count')
    seen = set()
    for row in rows:
        if not isinstance(row.get('version'), str) or not isinstance(row.get('checked_at_utc'), str):
            raise ValueError('catalog record lacks version or observation time')
        checked = datetime.datetime.fromisoformat(row['checked_at_utc'])
        if checked.tzinfo is None:
            raise ValueError('catalog observation time must include its timezone')
        for field in ('product', 'build', 'catalog_build', 'region'):
            value = row.get(field, '')
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}', value) or value in ('.', '..'):
                raise ValueError(f'invalid catalog {field}')
        for field in ('build_config', 'cdn_config'):
            if row.get(field) is not None and not re.fullmatch(r'[0-9a-f]{32}', row[field]):
                raise ValueError(f'invalid catalog {field}')
        if row.get('availability') not in LABELS:
            raise ValueError('invalid availability level')
        identity = (row['product'], row['build'], row['catalog_build'], row.get('build_config'), row.get('cdn_config'), row['region'])
        if identity in seen:
            raise ValueError('duplicate catalog identity')
        seen.add(identity)
    return data


def decode_catalog(blob):
    if len(blob) > MAX_COMPRESSED:
        raise ValueError('compressed catalog exceeds size limit')
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError('expanded catalog exceeds size limit')
    return validate_catalog(json.loads(raw))


def load_catalog(path):
    with open(path, 'rb') as stream:
        return decode_catalog(stream.read(MAX_COMPRESSED + 1))


def save_catalog(data, path):
    validate_catalog(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = gzip.compress(json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode(), mtime=0)
    # The temporary directory is removed even when validation, writing, or
    # replacement fails. The previous cache survives an interrupted update.
    with tempfile.TemporaryDirectory(prefix='cilo-cache-', dir=path.parent) as directory:
        temporary = Path(directory) / 'catalog.gz'
        temporary.write_bytes(blob)
        os.replace(temporary, path)


class CatalogAccessError(ValueError):
    """The GitHub cache needs repository access, or has not been published."""


def github_token():
    """Use the caller's existing login; never persist or log its credentials."""
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        return token.strip()
    try:
        result = subprocess.run(['gh', 'auth', 'token', '--hostname', 'github.com'],
                                capture_output=True, text=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(newurl).netloc != 'api.github.com':
            redirected.remove_header('Authorization')
        return redirected


def update_catalog(path, url=CATALOG_URL, timeout=30, token=None):
    headers = {'User-Agent': 'Cilo-CASC/0.1'}
    if url == CATALOG_URL:
        token = token or github_token()
        if token:
            url = CATALOG_API_URL
            headers.update(Authorization='Bearer ' + token, Accept='application/vnd.github.raw+json')
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.build_opener(GitHubRedirect()).open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if url in (CATALOG_URL, CATALOG_API_URL) and exc.code in (401, 403, 404):
            raise CatalogAccessError('GitHub could not provide the cache. This repository may be private; '
                                     'use a GitHub login/token with read access to IDARETW/Cilo. '
                                     'A missing published cache or API limit can also cause this error.') from exc
        raise
    with response:
        blob = response.read(MAX_COMPRESSED + 1)
    data = decode_catalog(blob)
    if Path(path).exists():
        try:
            previous = load_catalog(path)
        except (OSError, ValueError, EOFError):
            previous = None
        if previous:
            merge_probes(data, [r['probe'] for r in previous['builds'] if 'probe' in r])
    save_catalog(data, path)
    return data


def import_build_list(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    revision = re.search(r'(?m)^Snapshot commit: ([0-9a-f]{40})$', text)
    timestamp = re.search(r'(?m)^Completed UTC: (.+)$', text)
    if not revision or not timestamp:
        raise ValueError('build list lacks snapshot provenance')
    rows = []
    pattern = re.compile(
        r'^\d+ \| (.*?) \| BuildID=([A-Za-z0-9_.-]+) \| Region=([\w,]+)\n'
        r'  BuildConfig=([^ |\n]*) \| ([^\n]+)\n'
        r'  CDNConfig=([^ |\n]*) \| ([^\n]+)\n'
        r'  CDNPath=([^ |\n]*) \| Source=([^\n]+)', re.M)
    for section in re.split(r'(?m)^\[', text)[1:]:
        product, body = section.split(']\n', 1)
        for match in pattern.finditer(body):
            version, build, regions, bc, bs, cc, cs, cdn_path, sources = match.groups()
            region = 'us' if 'us' in regions.split(',') else regions.split(',')[0]
            source_paths = sources.split(';')
            source = next((p for p in source_paths if p.endswith('/versions')), source_paths[0])
            parts = source.split('/')
            if len(parts) != 4 or parts[1] != product:
                raise ValueError(f'invalid catalog source {source!r}')
            status = bs.split()[0]
            level = ('config_verified' if status == 'AVAILABLE' else
                     'config_unverified' if status == 'HTTP_200_HASH_UNVERIFIED' else
                     'not_checked' if status.startswith(('NO_', 'NOT_')) else 'not_retrieved')
            rows.append({'product': product, 'build': build, 'catalog_build': parts[2],
                         'version': version, 'region': region, 'catalog_regions': regions.split(','),
                         'build_config': None if bc in ('-', '') else bc.lower(),
                         'cdn_config': None if cc in ('-', '') else cc.lower(),
                         'cdn_path': None if cdn_path in ('-', '') else cdn_path,
                         'availability': level, 'checked_at_utc': timestamp[1],
                         'observation': {'source': 'imported_us_config_survey',
                                         'build_config': status, 'cdn_config': cs.split()[0]},
                         'catalog_sources': source_paths})
    expected = re.search(r'(?m)^Distinct build records: ([\d,]+)$', text)
    if not expected or len(rows) != int(expected[1].replace(',', '')):
        raise ValueError(f'parsed {len(rows)} records; count differs from source header')
    return validate_catalog({'schema': 1, 'generated_at_utc': utc_now(),
                             'catalog_revision': revision[1],
                             'source': 'https://github.com/mdX7/ngdp_data',
                             'scope': 'All distinct US/global records in the supplied catalog snapshot. '
                                      'Configuration reachability is not full-build availability.',
                             'builds': rows})


def merge_probes(catalog, probes):
    """Apply exact product/config observations without substituting build variants."""
    lookup = {}
    for row in catalog['builds']:
        lookup.setdefault((row['product'], row.get('build_config')), []).append(row)
    for probe in probes:
        if probe.get('stage') not in LABELS:
            continue
        for row in lookup.get((probe.get('product'), probe.get('build_config')), []):
            if 'cdn_config' in probe and probe['cdn_config'] != row.get('cdn_config'):
                continue
            if datetime.datetime.fromisoformat(probe['checked_at_utc']) < datetime.datetime.fromisoformat(row['checked_at_utc']):
                continue
            # A fresh failed sample remains a failed sample even if the build
            # had a successful sample earlier; retain the actual stage/error.
            row['availability'] = probe['stage']
            row['checked_at_utc'] = probe['checked_at_utc']
            row['probe'] = probe
    catalog['generated_at_utc'] = utc_now()
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-list', type=Path)
    parser.add_argument('--input', type=Path, help='existing compressed catalog to update')
    parser.add_argument('--probes', type=Path, action='append', default=[], help='JSONL from casc_probe.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.build_list) == bool(args.input):
        parser.error('choose exactly one of --build-list or --input')
    data = import_build_list(args.build_list) if args.build_list else load_catalog(args.input)
    for path in args.probes:
        merge_probes(data, [json.loads(line) for line in path.read_text().splitlines() if line.strip()])
    save_catalog(data, args.output)
    print(json.dumps({'builds': len(data['builds']), 'products': len({r['product'] for r in data['builds']}),
                      'bytes': args.output.stat().st_size, 'output': str(args.output)}))


if __name__ == '__main__':
    main()
