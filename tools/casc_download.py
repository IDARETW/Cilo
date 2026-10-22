#!/usr/bin/env python3
"""Resumable NGDP/CASC mirroring and TVFS extraction. Python 3.10+, stdlib only.

Mirror mode is product-independent for supported TACT DOWNLOAD manifests. Named
extraction supports TVFS; a CDN mirror is not a Battle.net-installable directory.
Protocol references: wowdev.wiki/TACT, wowdev.wiki/BLTE, CascLib, TACT.Net.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import fnmatch
import hashlib
import http.client
from html.parser import HTMLParser
import io
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

MIB = 1024 * 1024
META_LIMIT = 512 * MIB
ENDPOINT_MAP = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'data/cdn-endpoints.json'


class CascError(Exception):
    pass


def check(condition, message):
    if not condition:
        raise CascError(message)


def md5(data):
    return hashlib.md5(data).digest()


def key_path(kind, key):
    check(re.fullmatch(r"[0-9a-f]{32}", key), f"invalid CASC key: {key!r}")
    return f"{kind}/{key[:2]}/{key[2:4]}/{key}"


def be(data):
    return int.from_bytes(data, "big")


def exact(stream, n):
    data = stream.read(n)
    check(len(data) == n, "truncated CASC data")
    return data


def safe_name(name):
    check(name and len(name) <= 32760, "invalid filename length")
    for part in name.split("/"):
        check(part not in ("", ".", "..") and not part.endswith((" ", ".")), f"unsafe path: {name!r}")
        check(not any(ord(c) < 32 or c in '\\:<>"|?*' for c in part), f"unsafe path: {name!r}")
        stem = part.split(".")[0].upper()
        check(stem not in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
              and not re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem), f"reserved Windows path: {name}")
    return name


def hash_file(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def http_download(url, path, *, expected=None, limit=8 * 1024**3, start=None, attempts=4, timeout=30):
    """Resume into .part. Never silently accept ignored archive Range requests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    check(not path.is_symlink() and not part.is_symlink(), "symlink in download cache")
    check(expected is None or expected <= limit, "object exceeds download size limit")
    for attempt in range(attempts):
        pos = part.stat().st_size if part.exists() else 0
        if expected is not None and pos == expected:
            return part
        check(pos <= limit and (expected is None or pos < expected), f"oversized partial file: {part}")
        headers = {"User-Agent": "Cilo-CASC/0.1", "Accept-Encoding": "identity"}
        if start is not None:
            check(expected is not None, "archive range requires a known length")
            headers["Range"] = f"bytes={start + pos}-{start + expected - 1}"
        elif pos:
            headers["Range"] = f"bytes={pos}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.status
                if start is not None or (pos and status == 206):
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("Content-Range", ""))
                    check(status == 206 and match is not None, "server ignored or malformed Range response")
                    check(int(match[1]) == (start or 0) + pos, "wrong Content-Range offset")
                    if start is not None:
                        check(int(match[2]) == start + expected - 1, "wrong Content-Range end")
                elif pos:
                    check(status == 200, f"unexpected resume status {status}")
                    pos = 0  # Server ignored resume; safely restart a loose object.
                else:
                    check(status == 200, f"unexpected HTTP status {status}")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    check(pos + int(content_length) <= limit, "HTTP body exceeds download limit")
                with open(part, "ab" if pos else "wb") as target:
                    count = pos
                    while chunk := response.read(MIB):
                        count += len(chunk)
                        check(count <= limit and (expected is None or count <= expected), "download exceeded declared size")
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if expected is not None and count != expected:
                    raise ConnectionError("incomplete HTTP response")
                if content_length and count - pos != int(content_length):
                    raise ConnectionError("truncated HTTP response")
                return part
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and start is None and pos and exc.headers.get('Content-Range') == f'bytes */{pos}':
                return part  # Caller must still verify the complete object's hash.
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 == attempts:
                raise
            delay = exc.headers.get("Retry-After", "")
            time.sleep(min(10, int(delay) if delay.isdigit() else 2**attempt))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.IncompleteRead):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(10, 2**attempt))
    raise CascError("download retries exhausted")


def verify_blte(path, ekey):
    """Verify encoded bytes without needing decryption keys or loading the object."""
    with open(path, "rb") as stream:
        prefix = exact(stream, 8)
        check(prefix[:4] == b"BLTE", "object is not BLTE")
        header_size = be(prefix[4:])
        if not header_size:
            digest = hashlib.md5(prefix)
            for chunk in iter(lambda: stream.read(MIB), b""):
                digest.update(chunk)
            check(digest.hexdigest() == ekey, "BLTE encoding-key mismatch")
            return
        check(12 <= header_size <= 64 * MIB, "invalid BLTE header size")
        header = prefix + exact(stream, header_size - 8)
        check(md5(header).hex() == ekey, "BLTE encoding-key mismatch")
        count = be(header[9:12])
        check(header[8] == 15 and header_size == 12 + count * 24, "invalid BLTE chunk table")
        for i in range(count):
            entry = header[12 + i * 24:36 + i * 24]
            remaining = be(entry[:4])
            check(remaining > 0, "empty encoded BLTE chunk")
            digest = hashlib.md5()
            while remaining:
                chunk = exact(stream, min(MIB, remaining))
                digest.update(chunk)
                remaining -= len(chunk)
            check(digest.digest() == entry[8:24], "BLTE chunk checksum mismatch")
        check(not stream.read(1), "trailing BLTE data")


def decode_blte(path, target, *, ckey=None, limit=META_LIMIT):
    """Stream BLTE N/Z chunks to a file-like object; fail on encryption/unknown modes."""
    total, content_hash = 0, hashlib.md5()
    with open(path, "rb") as stream:
        prefix = exact(stream, 8)
        check(prefix[:4] == b"BLTE", "not BLTE")
        header_size = be(prefix[4:])
        if header_size:
            check(12 <= header_size <= 64 * MIB, "invalid BLTE header")
            header = prefix + exact(stream, header_size - 8)
            count = be(header[9:12])
            check(header[8] == 15 and header_size == 12 + count * 24, "invalid BLTE table")
            entries = [(be(header[p:p + 4]), be(header[p + 4:p + 8])) for p in range(12, header_size, 24)]
        else:
            entries = [(Path(path).stat().st_size - 8, None)]
        for size, expected in entries:
            check(0 < size <= 256 * MIB, "BLTE chunk exceeds decoder limit")
            chunk = exact(stream, size)
            available = limit - total
            if chunk[0] == ord("N"):
                decoded = chunk[1:]
            elif chunk[0] == ord("Z"):
                check(available >= 0, "decoded size limit exceeded")
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(chunk[1:], available + 1)
                check(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, "invalid or oversized zlib chunk")
            else:
                raise CascError(f"unsupported BLTE mode {chr(chunk[0])!r}; encrypted data can be mirrored but not extracted")
            total += len(decoded)
            check(total <= limit and (expected is None or len(decoded) == expected), "BLTE decoded size mismatch/limit")
            content_hash.update(decoded)
            target.write(decoded)
        check(not stream.read(1), "trailing BLTE bytes")
    check(ckey is None or content_hash.hexdigest() == ckey, "decoded content-key mismatch")
    return total


def parse_config(data):
    result = {}
    for line in data.decode("utf-8-sig").splitlines():
        if " = " in line and not line.startswith("#"):
            key, value = line.split(" = ", 1)
            result[key] = value.split()
    return result


def config_from_cache_page(data, key):
    """Recover original bytes from a public config page, never trust its title.

    BlizzTrack wraps configs in <pre> and adds surrounding whitespace. Try a
    bounded set of whitespace/newline variants; only the exact CASC MD5 wins.
    A page for a different build, a login page, or an error page is rejected.
    """
    key_path('config', key)
    if md5(data).hex() == key:
        return data

    class Pre(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts, self.active = [], False

        def handle_starttag(self, tag, attrs):
            if tag == 'pre':
                self.parts.append('')
                self.active = True

        def handle_endtag(self, tag):
            if tag == 'pre':
                self.active = False

        def handle_data(self, text):
            if self.active:
                self.parts[-1] += text

    parser = Pre()
    parser.feed(data.decode('utf-8'))
    for text in parser.parts:
        candidates = [text]
        stripped = text.strip().replace('\r\n', '\n')
        for newline in ('\n', '\r\n'):
            body = stripped.replace('\n', newline)
            candidates.extend(body + newline * n for n in range(3))
        for candidate in candidates:
            encoded = candidate.encode('utf-8')
            if md5(encoded).hex() == key:
                return encoded
    raise CascError('public config cache has no bytes matching the requested configuration MD5')


def blizztrack_config_url(product, kind, key):
    check(re.fullmatch(r'[A-Za-z0-9_-]+', product), 'invalid config-cache product')
    check(kind in ('bc', 'cdn'), 'invalid config-cache kind')
    key_path('config', key)
    return f'https://blizztrack.com/config/{product}/{kind}/{key}'


def parse_rows(data):
    lines = [line for line in data.decode("utf-8-sig").splitlines() if line and not line.startswith("#")]
    check(lines, "empty NGDP table")
    columns = [c.split("!")[0] for c in lines[0].split("|")]
    rows = []
    for line in lines[1:]:
        values = line.split("|")
        check(len(values) == len(columns), "malformed NGDP row")
        rows.append(dict(zip(columns, values)))
    return rows


def parse_download(data):
    check(len(data) >= 11 and data[:2] == b"DL" and data[2] in (1, 2, 3), "unsupported DOWNLOAD manifest version")
    version, key_size, checksums = data[2:5]
    check(key_size == 16 and checksums in (0, 1), "unsupported DOWNLOAD key/checksum format")
    at = 16 if version == 3 else 12 if version == 2 else 11
    check(len(data) >= at, 'truncated DOWNLOAD header')
    flags = data[11] if version >= 2 else 0
    count, tags = be(data[5:9]), be(data[9:11])
    width = key_size + 6 + 4 * checksums + flags
    check(at + count * width <= len(data), "truncated DOWNLOAD entries")
    records = []
    for _ in range(count):
        records.append({"key": data[at:at + 16].hex(), "size": be(data[at + 16:at + 21]), "priority": data[at + 21], "tags": []})
        at += width
    bitmap_size = (count + 7) // 8
    for _ in range(tags):
        end = data.find(b"\0", at)
        check(end >= at and end + 3 + bitmap_size <= len(data), "truncated DOWNLOAD tag")
        name = data[at:end].decode("utf-8")
        at = end + 3  # Null terminator and 16-bit tag type.
        bits = data[at:at + bitmap_size]
        for i, record in enumerate(records):
            if bits[i // 8] & (0x80 >> (i % 8)):
                record["tags"].append(name)
        at += bitmap_size
    check(at == len(data), "unexpected DOWNLOAD trailing bytes")
    check(len({r['key'] for r in records}) == len(records), "duplicate DOWNLOAD encoding keys")
    return records


def parse_tvfs(data):
    check(len(data) >= 38 and data[:5] == b"TVFS\x01", "named extraction requires a TVFS v1 root")
    key_size = data[6]
    check(key_size in (9, 16), "unsupported TVFS key size")
    path_at, path_size, vfs_at, vfs_size, cft_at, cft_size = struct.unpack_from(">6I", data, 12)
    check(all(a + s <= len(data) for a, s in [(path_at, path_size), (vfs_at, vfs_size), (cft_at, cft_size)]), "TVFS table bounds")
    width = max(1, (cft_size.bit_length() + 7) // 8)
    files = []

    def walk(at, end, parent, depth=0):
        check(depth < 128, "TVFS nesting limit")
        prefix = parent
        while at < end:
            if data[at] == 0:
                prefix += "/"
                at += 1
            check(at < end, "truncated TVFS name")
            if data[at] != 255:
                length = data[at]
                at += 1
                check(at + length <= end, "TVFS name bounds")
                prefix += data[at:at + length].decode("utf-8")
                at += length
            if at < end and data[at] == 0:
                prefix += "/"
                at += 1
            check(at < end, "TVFS node missing")
            if data[at] != 255:
                prefix += "/"
                continue
            check(at + 5 <= end, "TVFS node bounds")
            value = be(data[at + 1:at + 5])
            at += 5
            if value & 0x80000000:
                stop = at + (value & 0x7fffffff) - 4
                check(at <= stop <= end, "TVFS directory bounds")
                walk(at, stop, prefix, depth + 1)
                at = stop
            else:
                pos = vfs_at + value
                check(pos < vfs_at + vfs_size, "TVFS span bounds")
                count = data[pos]
                pos += 1
                check(0 < count <= 224 and pos + count * (8 + width) <= vfs_at + vfs_size, "TVFS span table bounds")
                spans = []
                for _ in range(count):
                    offset, size = struct.unpack_from(">II", data, pos)
                    cft = cft_at + be(data[pos + 8:pos + 8 + width])
                    check(cft + key_size <= cft_at + cft_size, "TVFS content table bounds")
                    spans.append({"offset": offset, "size": size, "key": data[cft:cft + key_size].hex()})
                    pos += 8 + width
                files.append({"path": safe_name(prefix.lstrip("/")), "spans": spans, "size": sum(s['size'] for s in spans)})
            prefix = parent
    walk(path_at, path_at + path_size, "")
    check(len({f['path'].casefold() for f in files}) == len(files), "case-insensitive TVFS path collision")
    return files


def parse_cdn_index(data, index_key):
    """Verify modern CDN indexes: loose (0), archive (4), or local group (6) offsets.

    Group indexes are normally generated by the client, not downloaded from the
    CDN. Supporting their format does not assert that a group URL should exist.
    """
    check(len(data) >= 36, "truncated archive index")
    footer = data[-36:]
    offset_bytes = footer[20]
    check(footer[16:19] == b"\x01\0\0" and footer[21:24] == bytes([4, 16, 8])
          and offset_bytes in (0, 4, 6), "unsupported archive index layout")
    check(md5(footer[16:28] + bytes(8))[:8] == footer[28:], "index footer checksum mismatch")
    check(md5(data[-28:]).hex() == index_key, "index identity mismatch")
    page_size, count = footer[19] * 1024, int.from_bytes(footer[24:28], "little")
    entry_size = 20 + offset_bytes
    check(page_size >= entry_size and count > 0, "invalid archive index dimensions")
    per_page = page_size // entry_size
    pages = (count + per_page - 1) // per_page
    toc = pages * page_size
    check(len(data) == toc + pages * 24 + 28, "archive index length mismatch")
    check(md5(data[toc:-28])[:8] == footer[8:16], "index TOC checksum mismatch")
    result = {}
    for page in range(pages):
        raw = data[page * page_size:(page + 1) * page_size]
        checksum = data[toc + pages * 16 + page * 8:toc + pages * 16 + (page + 1) * 8]
        check(md5(raw)[:8] == checksum, "index page checksum mismatch")
        for i in range(min(per_page, count - page * per_page)):
            entry = raw[i * entry_size:(i + 1) * entry_size]
            key = entry[:16].hex()
            check(key != '0' * 32 and key not in result, "invalid/duplicate archive index key")
            result[key] = {'size': be(entry[16:20]), 'offset': be(entry[20:]) if offset_bytes else None}
    return {'kind': {0: 'loose', 4: 'archive', 6: 'group'}[offset_bytes],
            'offset_bytes': offset_bytes, 'page_size': page_size, 'entries': result}


def parse_archive_index(data, archive_key):
    parsed = parse_cdn_index(data, archive_key)
    check(parsed['kind'] == 'archive', 'expected an individual archive index, not a loose/group index')
    return {key: (archive_key, entry['offset'], entry['size']) for key, entry in parsed['entries'].items()}


def describe_layout(build_config, cdn_config=None):
    """Account for config references without inventing object URLs for CKeys/groups."""
    objects, derived, unresolved = [], [], []
    accounted_build, accounted_cdn = set(), set()
    for name, values in build_config.items():
        if name in ('encoding', 'install', 'download', 'size', 'patch-index') or re.fullmatch(r'vfs-(root|\d+)', name):
            accounted_build.add(name)
            accounted_build.add(name + '-size')
            if len(values) >= 2:
                objects.append({'source': name, 'kind': 'BLTE manifest', 'content_key': values[0],
                                'key': values[1], 'relative_path': key_path('data', values[1])})
            elif values:
                unresolved.append({'source': name, 'content_key': values[0], 'reason': 'requires ENCODING CKey-to-EKey resolution'})
        elif name in ('root',):
            accounted_build.add(name)
            unresolved.append({'source': name, 'content_key': values[0] if values else None,
                               'reason': 'content key; not necessarily a CDN object name; TVFS builds use vfs-root for named extraction'})
        elif name == 'patch-config':
            accounted_build.add(name)
            for key in values:
                objects.append({'source': name, 'kind': 'patch configuration', 'key': key,
                                'relative_path': key_path('config', key)})
    if cdn_config is not None:
        for name, namespace in [('archives', 'data'), ('patch-archives', 'patch')]:
            keys = cdn_config.get(name, [])
            sizes = cdn_config.get(name + '-index-size', [])
            check(not sizes or len(keys) == len(sizes), f'{name} index-size count mismatch')
            accounted_cdn.update((name, name + '-index-size'))
            for i, key in enumerate(keys):
                relative = key_path(namespace, key)
                objects.append({'source': name, 'kind': 'archive index', 'key': key,
                                'relative_path': relative + '.index',
                                'declared_bytes': int(sizes[i]) if sizes else None})
                objects.append({'source': name, 'kind': 'archive blob', 'key': key, 'relative_path': relative,
                                'integrity': 'archive name identifies its index; verify requested members, not MD5(blob)==name'})
        for name, namespace in [('file-index', 'data'), ('patch-file-index', 'patch')]:
            accounted_cdn.update((name, name + '-size'))
            for key in cdn_config.get(name, []):
                objects.append({'source': name, 'kind': 'loose-file index', 'key': key,
                                'relative_path': key_path(namespace, key) + '.index'})
        for name in ('archive-group', 'patch-archive-group'):
            accounted_cdn.add(name)
            for key in cdn_config.get(name, []):
                derived.append({'source': name, 'key': key,
                                'meaning': 'client-generated combined index identity; not a required CDN object URL'})
    return {'objects': objects, 'derived_references': derived, 'content_key_references': unresolved,
            'other_build_fields': {k: v for k, v in build_config.items() if k not in accounted_build},
            'other_cdn_fields': {k: v for k, v in (cdn_config or {}).items() if k not in accounted_cdn},
            'scope': 'Declared storage layout, not an assertion of object reachability. Unknown fields remain visible.'}


class Storage:
    def __init__(self, bases, output, cdn_key=None, max_object_gib=8, timeout=30, config_fallback=None,
                 attempts=4, index_jobs=4):
        self.bases = [b.rstrip('/') for b in bases]
        check(self.bases, "no CDN base URLs supplied")
        for base in self.bases:
            url = urllib.parse.urlsplit(base)
            check(url.scheme in ('https', 'http') and url.hostname and not url.username, "invalid CDN URL")
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.limit = max_object_gib * 1024**3
        self.cdn_key = cdn_key
        self.archive_map = {}
        self.archives = None
        self.index_failures = {}
        self.timeout = timeout
        self.config_fallback = config_fallback
        self.recovered_configs = {}
        self.attempts, self.index_jobs = attempts, index_jobs
        check(1 <= attempts <= 4 and 1 <= index_jobs <= 8, 'invalid storage concurrency/retries')
        self.endpoint_lock = threading.Lock()
        self.lock = threading.Lock()
        self.object_locks = {}
        self.object_locks_guard = threading.Lock()

    def ordered_bases(self):
        with self.endpoint_lock:
            return self.bases.copy()

    def prefer(self, base):
        # Promote a verified working mirror. A per-object 403/404 is not a
        # reason to blacklist that server for unrelated objects.
        with self.endpoint_lock:
            self.bases.remove(base)
            self.bases.insert(0, base)

    def path(self, relative):
        path = self.output / 'cdn' / relative
        check(path.resolve().is_relative_to(self.output), "cache path escapes output directory")
        return path

    def fetch(self, kind, key, size=None):
        relative = key_path(kind, key)
        with self.object_locks_guard:
            lock = self.object_locks.setdefault(relative, threading.Lock())
        with lock:
            return self._fetch(kind, key, size)

    def _fetch(self, kind, key, size):
        relative = key_path(kind, key)
        path = self.path(relative)
        def validate(p):
            check(size is None or p.stat().st_size == size, "object size mismatch")
            if kind == 'config':
                check(hash_file(p, 'md5') == key, "configuration checksum mismatch")
            else:
                verify_blte(p, key)
        if path.exists():
            validate(path)
            return path
        errors = []
        for base in self.ordered_bases():
            url = f'{base}/{relative}'
            try:
                part = http_download(url, path, expected=size, limit=4*MIB if kind == 'config' else self.limit,
                                     timeout=self.timeout, attempts=self.attempts)
                validate(part)
                os.replace(part, path)
                self.prefer(base)
                return path
            except (CascError, urllib.error.URLError, OSError) as exc:
                errors.append(f'{url}: {exc}')
                # Complete bad content must not become a reusable cache entry.
                part = path.with_name(path.name + '.part')
                if isinstance(exc, CascError) and part.exists():
                    part.unlink()
        if kind == 'config' and self.config_fallback:
            url = self.config_fallback('cdn' if key == self.cdn_key else 'bc', key)
            try:
                # HTML is kept separate from the resumable raw CDN object.
                # Make one request: a third-party rate limit must not trigger
                # a retry burst or make a cached error look like missing data.
                with tempfile.TemporaryDirectory(dir=self.output) as directory:
                    page = http_download(url, Path(directory) / 'page', limit=4*MIB,
                                         timeout=self.timeout, attempts=1)
                    raw = config_from_cache_page(page.read_bytes(), key)
                    recovered = Path(directory) / 'config'
                    recovered.write_bytes(raw)
                    validate(recovered)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(recovered, path)
                self.recovered_configs[key] = url
                print(f'Recovered config {key} from {url}; MD5 verified', file=sys.stderr)
                return path
            except (CascError, UnicodeError, urllib.error.URLError, OSError) as exc:
                errors.append(f'{url}: {exc}')
        if kind == 'data' and self.cdn_key:
            location = self.locate(key)
            if location:
                archive, offset, length = location
                check(size is None or size == length, "archive index disagrees with DOWNLOAD size")
                for base in self.ordered_bases():
                    url = f'{base}/{key_path("data", archive)}'
                    try:
                        part = http_download(url, path, expected=length, start=offset, limit=self.limit,
                                             timeout=self.timeout, attempts=self.attempts)
                        validate(part)
                        os.replace(part, path)
                        self.prefer(base)
                        return path
                    except (CascError, urllib.error.URLError, OSError) as exc:
                        errors.append(f'{url} (range {offset}+{length}): {exc}')
                        if isinstance(exc, CascError):
                            path.with_name(path.name + '.part').unlink(missing_ok=True)
        raise CascError(f'could not retrieve {kind}/{key} from the tested endpoints: ' + '; '.join(errors))

    def archive_index(self, archive):
        relative = key_path('data', archive) + '.index'
        path = self.path(relative)
        if path.exists():
            return parse_archive_index(path.read_bytes(), archive)
        failures = []
        for base in self.ordered_bases():
            url = f'{base}/{relative}'
            try:
                part = http_download(url, path, limit=64*MIB, timeout=self.timeout, attempts=self.attempts)
                entries = parse_archive_index(part.read_bytes(), archive)
                os.replace(part, path)
                self.prefer(base)
                return entries
            except (CascError, urllib.error.URLError, OSError) as exc:
                failures.append(f'{url}: {exc}')
                if isinstance(exc, CascError):
                    path.with_name(path.name + '.part').unlink(missing_ok=True)
        raise CascError('; '.join(failures))

    def locate(self, key):
        with self.lock:
            retry_indexes = [a for a, (last, _) in self.index_failures.items() if time.monotonic() - last >= 30]
            if self.archives is None:
                try:
                    cfg = parse_config(self.fetch('config', self.cdn_key).read_bytes())
                    self.archives = iter(cfg.get('archives', []))
                except (CascError, urllib.error.URLError) as exc:
                    raise CascError(f'loose-object retrieval failed and CDN configuration could not be retrieved: {exc}') from exc
            def load_index(archive):
                try:
                    self.archive_map.update(self.archive_index(archive))
                    self.index_failures.pop(archive, None)
                except (CascError, urllib.error.URLError, OSError) as exc:
                    self.index_failures[archive] = (time.monotonic(), str(exc))

            # Bounded parallel index lookup avoids serially waiting for every
            # regional failure on every unrelated archive. Keep failed indexes
            # retryable, and never turn an incomplete search into "missing".
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.index_jobs) as pool:
                while key not in self.archive_map:
                    batch = list(itertools.islice(self.archives, self.index_jobs))
                    if not batch:
                        break
                    pending = {pool.submit(self.archive_index, archive): archive for archive in batch}
                    for future in concurrent.futures.as_completed(pending):
                        archive = pending[future]
                        try:
                            self.archive_map.update(future.result())
                            self.index_failures.pop(archive, None)
                        except (CascError, urllib.error.URLError, OSError) as exc:
                            self.index_failures[archive] = (time.monotonic(), str(exc))
            if key in self.archive_map:
                return self.archive_map[key]
            # Keep failed indexes retryable, with a cooldown to avoid hammering
            # unavailable mirrors for every object in a multi-file download.
            for archive in retry_indexes:
                if archive in self.index_failures:
                    load_index(archive)
                    if key in self.archive_map:
                        return self.archive_map[key]
            if self.index_failures:
                examples = '; '.join(f'{k}: {v[1]}' for k, v in list(self.index_failures.items())[:3])
                raise CascError(f'archive search incomplete: {len(self.index_failures)} indexes could not be read; '
                                f'this does not establish that object {key} is missing. {examples}')
            return None


def read_source(source, timeout=30):
    if urllib.parse.urlsplit(source).scheme in ('http', 'https'):
        with tempfile.TemporaryDirectory() as directory:
            part = http_download(source, Path(directory) / 'metadata', limit=16*MIB, timeout=timeout)
            return part.read_bytes()
    return Path(source).read_bytes()


def metadata_source(root, relative, timeout=30):
    source = root.rstrip('/') + '/' + relative if urllib.parse.urlsplit(root).scheme in ('http', 'https') else str(Path(root) / PurePosixPath(relative))
    try:
        return read_source(source, timeout)
    except urllib.error.HTTPError as exc:
        raise CascError(f'NGDP catalog request returned HTTP {exc.code}: {source}. '
                        'Check the product, build directory, and catalog revision; this is not a game-CDN response.') from exc


def cdn_bases(rows, preferred='us', regions=None):
    """Prefer the version region, then try every advertised CDN region by default."""
    names = regions or [preferred] + [r['Name'] for r in rows if r['Name'] != preferred]
    bases = []
    for name in dict.fromkeys(names):
        row = next((r for r in rows if r.get('Name') == name), None)
        if row is None:
            check(not regions, f'CDN table has no {name!r} row')
            continue
        # Servers' query parameters are client routing hints, not object URL
        # queries. Hosts can advertise additional mirrors absent from Servers.
        endpoints = row.get('Servers', '').split()
        represented = {urllib.parse.urlsplit(u).netloc for u in endpoints}
        endpoints += [f'http://{h}' for h in row.get('Hosts', '').split() if h not in represented]
        for endpoint in endpoints:
            u = urllib.parse.urlsplit(endpoint)
            check(u.scheme in ('http', 'https') and u.hostname and not u.username, 'invalid CDN server URL')
            bases.append(urllib.parse.urlunsplit((u.scheme, u.netloc, '/' + row['Path'].strip('/'), '', '')))
    check(bases, 'no CDN endpoints; supply --cdn-base or --cdn-table')
    return list(dict.fromkeys(bases))


def historical_cdn_bases(path, regions=None, endpoint_map=ENDPOINT_MAP):
    if not Path(endpoint_map).exists():
        return []
    data = json.loads(Path(endpoint_map).read_text(encoding='utf-8'))
    check(data.get('schema') == 1 and isinstance(data.get('endpoints'), list), 'invalid historical CDN map')
    return [r['base'] for r in data['endpoints'] if r['path'] == path
            and (not regions or set(regions).intersection(r['regions']))]


def resolve_build(args):
    if args.build_config:
        check(args.cdn_base, '--build-config requires --cdn-base')
        return args.build_config.lower(), args.cdn_config, args.cdn_base, {}
    check(args.product and args.build, 'provide --product and --build, or --build-config and --cdn-base')
    for component in (args.product, args.build, args.catalog_region):
        check(re.fullmatch(r'[A-Za-z0-9_.-]+', component) and component not in ('.', '..'), 'invalid NGDP path component')
    prefix = f'{args.catalog_region}/{args.product}'
    rows = parse_rows(metadata_source(args.ngdp_root, f'{prefix}/{args.build}/versions', args.http_timeout))
    row = next((r for r in rows if r.get('Region') == args.region), None)
    if row is None:
        row = next((r for r in rows if r.get('Region') == 'xx'), None)
    check(row is not None, f'no {args.region}/xx version row')
    if args.cdn_base:
        bases = args.cdn_base
    else:
        data = read_source(args.cdn_table, args.http_timeout) if args.cdn_table else metadata_source(args.ngdp_root, f'{prefix}/cdns', args.http_timeout)
        bases = cdn_bases(parse_rows(data), args.region, args.cdn_region)
        if not args.current_cdns_only:
            for path in dict.fromkeys(urllib.parse.urlsplit(base).path.strip('/') for base in bases.copy()):
                bases.extend(historical_cdn_bases(path, args.cdn_region, args.cdn_map))
    return row['BuildConfig'], args.cdn_config or row.get('CDNConfig') or None, list(dict.fromkeys(bases)), row


def diagnose(storage, key, row, jobs):
    """Probe each configuration URL directly, without trusting local cache hits."""
    configs = [('build_config', key)] + ([('cdn_config', storage.cdn_key)] if storage.cdn_key else [])
    tasks = [(name, value, f'{base}/{key_path("config", value)}') for name, value in configs for base in storage.ordered_bases()]
    def probe(item):
        name, value, url = item
        result = {'kind': name, 'key': value, 'url': url, 'verified': False}
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as directory:
                part = http_download(url, Path(directory) / 'probe', limit=4*MIB, attempts=1, timeout=storage.timeout)
                digest = hash_file(part, 'md5')
                result.update(http_status=200, bytes=part.stat().st_size, md5=digest, verified=digest == value)
                if digest != value:
                    result['error'] = 'HTTP success but configuration MD5 does not match; not accepted as usable configuration'
        except urllib.error.HTTPError as exc:
            result.update(http_status=exc.code, error=str(exc))
        except (CascError, urllib.error.URLError, OSError) as exc:
            result['error'] = str(exc)
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        checks = list(pool.map(probe, tasks))
    report = {'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'build_config': key, 'cdn_config': storage.cdn_key, 'version_row': row,
              'scope': 'Configuration URLs only. Success does not prove all payloads are available; failure does not prove they were deleted.',
              'build_config_verified': any(c['kind'] == 'build_config' and c['verified'] for c in checks),
              'cdn_config_verified': any(c['kind'] == 'cdn_config' and c['verified'] for c in checks), 'checks': checks}
    path = storage.output / 'diagnostics.json'
    check(not path.is_symlink(), 'symlink diagnostic report')
    path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    check(report['build_config_verified'], f'no verified build configuration from the tested endpoints; details saved to {path}')


class Build:
    def __init__(self, storage, key):
        self.storage, self.key = storage, key
        self.config = parse_config(storage.fetch('config', key).read_bytes())
        self.records = None

    def manifest(self, name):
        values = self.config.get(name, [])
        check(len(values) >= 2, f'{name} has no direct encoding key; this legacy build requires an encoding-table resolver')
        ckey, ekey = values[:2]
        path = self.storage.fetch('data', ekey)
        output = io.BytesIO()
        decode_blte(path, output, ckey=ckey)
        return output.getvalue()

    def download_records(self):
        if self.records is None:
            self.records = parse_download(self.manifest('download'))
        return self.records

    def files(self):
        check('vfs-root' in self.config, 'this build has no TVFS root; use mirror for product-independent encoded content')
        files = parse_tvfs(self.manifest('vfs-root'))
        lookup = {}
        for record in self.download_records():
            for key in (record['key'], record['key'][:18]):
                check(key not in lookup, 'ambiguous encoding-key prefix')
                lookup[key] = record
        for file in files:
            for span in file['spans']:
                check(span['key'] in lookup, f"unresolved encoding key for {file['path']}")
                span.update(encoding_key=lookup[span['key']]['key'], encoded_size=lookup[span['key']]['size'])
        return files


def extract_file(storage, record):
    destination = storage.output / 'files' / safe_name(record['path'])
    check(destination.resolve().is_relative_to(storage.output), 'extraction path escapes output')
    destination.parent.mkdir(parents=True, exist_ok=True)
    check(not destination.is_symlink(), 'symlink extraction target')
    # Existing content is compared against freshly verified bytes, never trusted by size alone.
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.casc-', delete=False) as output:
        temporary = Path(output.name)
        try:
            for span in record['spans']:
                check(span['offset'] == 0, 'nonzero TVFS span source offsets are not supported')
                encoded = storage.fetch('data', span['encoding_key'], span['encoded_size'])
                count = decode_blte(encoded, output, limit=span['size'])
                check(count == span['size'], 'TVFS span length mismatch')
            output.flush()
            os.fsync(output.fileno())
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        digest = hash_file(temporary)
        if destination.exists():
            check(destination.is_file() and hash_file(destination) == digest, f'refusing to overwrite different file: {destination}')
        else:
            # Exclusive publication also refuses a destination created concurrently.
            try:
                os.link(temporary, destination)
            except FileExistsError as exc:
                raise CascError(f'destination appeared during extraction: {destination}') from exc
        return {'path': record['path'], 'bytes': record['size'], 'sha256': digest}
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ['gui']:
        from casc_gui import main as gui_main
        gui_main()
        return
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['info', 'list', 'extract', 'mirror', 'diagnose', 'layout'])
    parser.add_argument('--product')
    parser.add_argument('--build', help='build directory in mdX7/ngdp_data, e.g. 11521872')
    parser.add_argument('--region', default='us', help='version row and preferred CDN region')
    parser.add_argument('--cdn-region', action='append', help='limit CDN regions; repeat for fallback order (default: all advertised regions)')
    parser.add_argument('--cdn-table', help='override cdns table with a URL or local file, e.g. live or historical metadata')
    parser.add_argument('--catalog-region', default='US')
    parser.add_argument('--ngdp-root', default='https://raw.githubusercontent.com/mdX7/ngdp_data/master', help='catalog URL (can pin a commit) or local clone root')
    parser.add_argument('--build-config')
    parser.add_argument('--cdn-config')
    parser.add_argument('--cdn-base', action='append', help='full CDN product base URL; repeat for mirrors')
    parser.add_argument('--cdn-map', type=Path, default=ENDPOINT_MAP, help='historical endpoint map (bundled by default)')
    parser.add_argument('--current-cdns-only', action='store_true', help='do not append historical endpoints to advertised CDN rows')
    parser.add_argument('--config-fallback', choices=['blizztrack', 'none'], default='blizztrack',
                        help='public metadata cache to try after CDN config failures (default: blizztrack; requires --product)')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pattern', action='append', default=[], help='TVFS filename glob; repeat to OR patterns')
    parser.add_argument('--tag', action='append', default=[], help='mirror only objects with ALL these DOWNLOAD tags')
    parser.add_argument('--limit', type=int, default=0, help='maximum selected files/objects; 0 means all')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--http-timeout', type=float, default=30, help='socket timeout in seconds; diagnose makes one attempt per URL')
    parser.add_argument('--max-object-gib', type=int, default=8)
    parser.add_argument('--max-total-gib', type=float, default=0, help='refuse a larger selection; 0 means no cap')
    parser.add_argument('--dry-run', action='store_true', help='resolve manifests and print selection without downloading payloads')
    args = parser.parse_args(argv)
    check(1 <= args.jobs <= 32 and args.limit >= 0 and 1 <= args.max_object_gib <= 1024
          and args.max_total_gib >= 0 and 0 < args.http_timeout <= 600, 'invalid resource limits')
    check(not args.tag or args.command in ('mirror', 'info'), '--tag only applies to mirror/info')
    check(not args.pattern or args.command in ('list', 'extract'), '--pattern only applies to list/extract')
    key, cdn_key, bases, row = resolve_build(args)
    fallback = None
    if args.product and args.config_fallback == 'blizztrack':
        fallback = lambda kind, value: blizztrack_config_url(args.product, kind, value)
    storage = Storage(bases, args.output, cdn_key, args.max_object_gib, args.http_timeout, fallback)
    if args.command == 'diagnose':
        diagnose(storage, key, row, args.jobs)
        return
    build = Build(storage, key)
    if args.command == 'layout':
        cdn_config, error = None, None
        if cdn_key:
            try:
                cdn_config = parse_config(storage.fetch('config', cdn_key).read_bytes())
            except (CascError, OSError, ValueError) as exc:
                error = str(exc)
        report = describe_layout(build.config, cdn_config)
        report.update(build_config=key, cdn_config=cdn_key, cdn_bases=bases, cdn_config_error=error)
        print(json.dumps(report, indent=2))
        return
    records = build.download_records()
    report = {'build_config': key, 'cdn_config': cdn_key, 'cdn_bases': bases, 'version_row': row,
              'recovered_configs': storage.recovered_configs,
              'build_name': build.config.get('build-name', []), 'objects': len(records),
              'encoded_bytes': sum(r['size'] for r in records), 'tags': sorted({t for r in records for t in r['tags']})}
    report_path = storage.output / 'build.json'
    check(not report_path.is_symlink(), 'symlink build report')
    if report_path.exists():
        previous = json.loads(report_path.read_text(encoding='utf-8'))
        check(previous.get('build_config') == key, 'output belongs to another build; use a separate output directory')
    report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.command == 'info':
        print(json.dumps(report, indent=2))
        return
    if args.command in ('list', 'extract'):
        selection = [f for f in build.files() if not args.pattern or any(fnmatch.fnmatchcase(f['path'], p) for p in args.pattern)]
    else:
        selection = sorted((r for r in records if all(t in r['tags'] for t in args.tag)), key=lambda r: (r['size'], r['key']))
    if args.limit:
        selection = selection[:args.limit]
    check(selection, 'selection matched no files/objects')
    total = sum(r['size'] for r in selection)
    check(not args.max_total_gib or total <= args.max_total_gib * 1024**3, 'selection exceeds --max-total-gib')
    if args.command == 'list' or args.dry_run:
        print(json.dumps({'selected': len(selection), 'bytes': total, 'entries': selection}, indent=2))
        return
    if args.command == 'mirror':
        # Preserve core manifests as well as DOWNLOAD-listed payloads. Keep encoded
        # bytes, so mirroring does not depend on decryption or a game's root format.
        for name, values in build.config.items():
            if name in ('encoding', 'install', 'download', 'size') or name.startswith('vfs-') and not name.endswith('-size'):
                if len(values) >= 2 and all(re.fullmatch(r'[0-9a-f]{32}', v) for v in values[:2]):
                    storage.fetch('data', values[1])
    # Keep a durable per-item journal, including failures, for interrupted large mirrors.
    completed, failures = 0, []
    journal = storage.output / f'{args.command}-journal.jsonl'
    check(not journal.is_symlink(), 'symlink journal')
    def process(record):
        if args.command == 'extract':
            return extract_file(storage, record)
        path = storage.fetch('data', record['key'], record['size'])
        return {'key': record['key'], 'bytes': record['size'], 'sha256': hash_file(path)}
    with journal.open('a', encoding='utf-8') as log, concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        remaining, pending = iter(selection), {}
        while True:
            while len(pending) < args.jobs * 2:
                item = next(remaining, None)
                if item is None:
                    break
                pending[pool.submit(process, item)] = item
            if not pending:
                break
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for task in done:
                item = pending.pop(task)
                try:
                    result = task.result()
                    completed += 1
                    record = {'ok': True, **result}
                except Exception as exc:
                    record = {'ok': False, 'item': item.get('path', item.get('key')), 'error': str(exc)}
                    failures.append(record)
                log.write(json.dumps(record) + '\n')
                log.flush()
                print(json.dumps(record), flush=True)
    print(json.dumps({'completed': completed, 'failed': len(failures), 'selected': len(selection), 'journal': str(journal)}))
    check(not failures, f'{len(failures)} objects/files failed; see {journal}; rerunning verifies and reuses completed objects')


if __name__ == '__main__':
    try:
        main()
    except (CascError, OSError, ValueError, urllib.error.URLError, zlib.error) as error:
        print(f'casc_download: {error}', file=sys.stderr)
        sys.exit(1)
