import concurrent.futures
import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import zlib

SPEC = importlib.util.spec_from_file_location('casc_download', Path(__file__).parents[1] / 'tools/casc_download.py')
casc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(casc)


def blte(raw, mode=b'Z'):
    chunk = mode + (zlib.compress(raw) if mode == b'Z' else raw)
    header = b'BLTE' + struct.pack('>I', 36) + b'\x0f\0\0\x01'
    header += struct.pack('>II', len(chunk), len(raw)) + casc.md5(chunk)
    return header + chunk, casc.md5(header).hex()


def index_fixture(key, offset, size):
    page = bytes.fromhex(key) + struct.pack('>II', size, offset) + bytes(4096 - 24)
    toc = bytes.fromhex(key) + casc.md5(page)[:8]
    fields = bytes([1, 0, 0, 4, 4, 4, 16, 8]) + struct.pack('<I', 1)
    data = page + toc + casc.md5(toc)[:8] + fields + casc.md5(fields + bytes(8))[:8]
    return data, casc.md5(data[-28:]).hex()


class Formats(unittest.TestCase):
    def test_all_regional_cdn_endpoints_and_overrides(self):
        rows = [
            {'Name': 'eu', 'Path': 'tpr/game', 'Hosts': 'eu.example', 'Servers': 'https://eu.example/?fallback=1'},
            {'Name': 'us', 'Path': '/tpr/game/', 'Hosts': 'us.example extra.example',
             'Servers': 'https://us.example/?maxhosts=4 https://eu.example/'},
            {'Name': 'kr', 'Path': 'tpr/game', 'Hosts': 'kr.example', 'Servers': ''},
        ]
        self.assertEqual(casc.cdn_bases(rows), [
            'https://us.example/tpr/game', 'https://eu.example/tpr/game',
            'http://extra.example/tpr/game', 'http://kr.example/tpr/game'])
        self.assertEqual(casc.cdn_bases(rows, regions=['kr', 'eu']), [
            'http://kr.example/tpr/game', 'https://eu.example/tpr/game'])
        with self.assertRaisesRegex(casc.CascError, 'no .* row'):
            casc.cdn_bases(rows, regions=['xx'])

    def test_blte_modes_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'object'
            for mode in (b'N', b'Z'):
                raw = b'asset content\n' * 100
                data, key = blte(raw, mode)
                path.write_bytes(data)
                casc.verify_blte(path, key)
                result = io.BytesIO()
                casc.decode_blte(path, result, ckey=casc.md5(raw).hex())
                self.assertEqual(result.getvalue(), raw)
                with self.assertRaises(casc.CascError):
                    casc.decode_blte(path, io.BytesIO(), limit=10)
                path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                with self.assertRaises(casc.CascError):
                    casc.verify_blte(path, key)

    def test_encryption_can_be_verified_but_not_decoded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'encrypted'
            data, key = blte(b'opaque encrypted bytes', b'E')
            path.write_bytes(data)
            casc.verify_blte(path, key)
            with self.assertRaisesRegex(casc.CascError, 'unsupported BLTE mode'):
                casc.decode_blte(path, io.BytesIO())

    def test_download_versions_and_tags(self):
        key = '12' * 16
        for version in (1, 2, 3):
            header = b'DL' + bytes([version, 16, 1]) + struct.pack('>IH', 2, 1)
            if version >= 2:
                header += b'\x01'
            if version == 3:
                header += bytes(4)
            entries = b''
            for i in range(2):
                entries += bytes.fromhex(key if i == 0 else '34' * 16) + (1234).to_bytes(5, 'big') + b'\x01' + bytes(4)
                if version >= 2:
                    entries += b'\x02'
            data = header + entries + b'enUS\0\0\x01\x80'
            rows = casc.parse_download(data)
            self.assertEqual(rows[0]['tags'], ['enUS'])
            self.assertEqual(rows[1]['tags'], [])
            self.assertEqual(rows[0]['size'], 1234)
            with self.assertRaises(casc.CascError):
                casc.parse_download(data[:-1])
            with self.assertRaises(casc.CascError):
                casc.parse_download(data[:10 if version == 1 else 11])

    def test_index_bounds_and_checksums(self):
        key = '42' * 16
        data, archive = index_fixture(key, 91, 200)
        self.assertEqual(casc.parse_archive_index(data, archive)[key], (archive, 91, 200))
        corrupt = bytearray(data)
        corrupt[8] ^= 1
        with self.assertRaises(casc.CascError):
            casc.parse_archive_index(corrupt, archive)
        with self.assertRaises(casc.CascError):
            casc.parse_archive_index(data[:-1], archive)

    def test_paths(self):
        for name in ('../x', '/x', 'C:/x', 'a\\b', 'a//b', 'NUL', 'com1.exe', 'x.', 'x '):
            with self.assertRaises(casc.CascError):
                casc.safe_name(name)
        self.assertEqual(casc.safe_name('english/file.ff'), 'english/file.ff')

    def test_tvfs_fixture(self):
        key = bytes.fromhex('12' * 9)
        paths = b'\x07file.ff\xff' + struct.pack('>I', 0)
        spans = b'\x01' + struct.pack('>II', 0, 100) + b'\x00'
        header = b'TVFS\x01\x26\x09\x09' + bytes(4)
        header += struct.pack('>6I', 38, len(paths), 38 + len(paths), len(spans), 38 + len(paths) + len(spans), len(key)) + b'\0\x01'
        entries = casc.parse_tvfs(header + paths + spans + key)
        self.assertEqual(entries[0]['path'], 'file.ff')
        self.assertEqual(entries[0]['size'], 100)
        self.assertEqual(entries[0]['spans'][0]['key'], key.hex())


class Network(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.routes = {}
        self.requests = []
        routes, requests = self.routes, self.requests
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                requests.append((self.path, self.headers.get('Range')))
                if self.path not in routes:
                    self.send_error(404)
                    return
                data = routes[self.path]
                byte_range = self.headers.get('Range')
                if byte_range and self.path != '/ignore-range':
                    lo, hi = byte_range.removeprefix('bytes=').split('-')
                    lo, hi = int(lo), int(hi) if hi else len(data) - 1
                    if lo >= len(data):
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{len(data)}')
                        self.end_headers()
                        return
                    hi = min(hi, len(data) - 1)
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {lo}-{hi}/{len(data)}')
                    data = data[lo:hi + 1]
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_resume_and_ignored_range(self):
        self.routes['/data'] = self.routes['/ignore-range'] = b'abcdefghij'
        path = self.root / 'download'
        path.with_name('download.part').write_bytes(b'abc')
        part = casc.http_download(self.base + '/data', path, expected=10)
        self.assertEqual(part.read_bytes(), b'abcdefghij')
        self.assertEqual(self.requests[-1][1], 'bytes=3-')
        part.write_bytes(b'abc')
        self.assertEqual(casc.http_download(self.base + '/ignore-range', path, expected=10).read_bytes(), b'abcdefghij')
        part.unlink()
        with self.assertRaisesRegex(casc.CascError, 'ignored'):
            casc.http_download(self.base + '/ignore-range', path, expected=3, start=2)

    def test_archive_fallback_and_concurrent_cache(self):
        encoded, key = blte(b'actual asset payload' * 30)
        index, archive = index_fixture(key, 17, len(encoded))
        cfg = f'archives = {archive}\n'.encode()
        cdn_key = casc.md5(cfg).hex()
        self.routes['/' + casc.key_path('config', cdn_key)] = cfg
        self.routes['/' + casc.key_path('data', archive) + '.index'] = index
        self.routes['/' + casc.key_path('data', archive)] = bytes(17) + encoded + b'ignored'
        storage = casc.Storage([self.base], self.root / 'mirror', cdn_key)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: storage.fetch('data', key, len(encoded)), range(4)))
        self.assertEqual(paths[0].read_bytes(), encoded)
        ranges = [r for r in self.requests if r[1]]
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0][1], f'bytes=17-{16 + len(encoded)}')

    def test_missing_index_does_not_hide_later_archive_and_can_retry(self):
        encoded, key = blte(b'reachable payload')
        later_index, later_archive = index_fixture(key, 17, len(encoded))
        lost_encoded, lost_key = blte(b'temporarily missing index payload')
        lost_index, lost_archive = index_fixture(lost_key, 3, len(lost_encoded))
        cfg = f'archives = {lost_archive} {later_archive}\n'.encode()
        cdn_key = casc.md5(cfg).hex()
        self.routes['/' + casc.key_path('config', cdn_key)] = cfg
        self.routes['/' + casc.key_path('data', later_archive) + '.index'] = later_index
        self.routes['/' + casc.key_path('data', later_archive)] = bytes(17) + encoded
        storage = casc.Storage([self.base], self.root / 'mirror', cdn_key)
        self.assertEqual(storage.fetch('data', key, len(encoded)).read_bytes(), encoded)
        with self.assertRaisesRegex(casc.CascError, 'archive search incomplete'):
            storage.fetch('data', lost_key, len(lost_encoded))
        # Recover the initially missing index after the per-index retry cooldown.
        self.routes['/' + casc.key_path('data', lost_archive) + '.index'] = lost_index
        self.routes['/' + casc.key_path('data', lost_archive)] = bytes(3) + lost_encoded
        storage.index_failures[lost_archive] = (float('-inf'), 'previous 404')
        self.assertEqual(storage.fetch('data', lost_key, len(lost_encoded)).read_bytes(), lost_encoded)

    def test_working_endpoint_is_preferred_without_blacklisting_others(self):
        encoded, key = blte(b'first content')
        other, other_key = blte(b'second content')
        for blob, k in [(encoded, key), (other, other_key)]:
            self.routes['/good/' + casc.key_path('data', k)] = blob
        storage = casc.Storage([self.base + '/missing', self.base + '/good'], self.root / 'mirror')
        storage.fetch('data', key)
        self.assertEqual(storage.ordered_bases(), [self.base + '/good', self.base + '/missing'])
        self.requests.clear()
        storage.fetch('data', other_key)
        self.assertEqual(len(self.requests), 1)
        # A different object is allowed to exist only on the earlier failing endpoint.
        third, third_key = blte(b'third content')
        self.routes['/missing/' + casc.key_path('data', third_key)] = third
        self.assertEqual(storage.fetch('data', third_key).read_bytes(), third)

    def test_diagnose_distinguishes_config_hashes_http_failures_and_catalog_errors(self):
        cfg = b'build-name = example\n'
        key = casc.md5(cfg).hex()
        relative = '/' + casc.key_path('config', key)
        self.routes['/good' + relative] = cfg
        self.routes['/bad' + relative] = b'unverified response'
        output = self.root / 'report'
        args = ['diagnose', '--build-config', key, '--cdn-base', self.base + '/missing',
                '--cdn-base', self.base + '/bad', '--cdn-base', self.base + '/good', '--output', str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            casc.main(args)
        report = json.loads((output / 'diagnostics.json').read_text())
        self.assertTrue(report['build_config_verified'])
        self.assertEqual([r['http_status'] for r in report['checks']], [404, 200, 200])
        self.assertEqual([r['verified'] for r in report['checks']], [False, False, True])
        # A cached good file must not conceal an endpoint outage in diagnostics.
        self.routes.clear()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(casc.CascError, 'no verified build configuration'):
            casc.main(args)
        with self.assertRaisesRegex(casc.CascError, 'NGDP catalog request returned HTTP 404'):
            casc.metadata_source(self.base, 'US/odin/invalid/versions')

    def test_regional_fallback_from_catalog_and_live_table_override(self):
        cfg = b'build-name = regional-test\n'
        key = casc.md5(cfg).hex()
        self.routes['/catalog/US/odin/123/versions'] = (
            'Region!STRING:0|BuildConfig!HEX:16|CDNConfig!HEX:16\nus|' + key + '|\n').encode()
        self.routes['/live/cdns'] = (
            'Name!STRING:0|Path!STRING:0|Hosts!STRING:0|Servers!STRING:0\n'
            f'us|missing||{self.base}/\nkr|present||{self.base}/\n').encode()
        self.routes['/present/' + casc.key_path('config', key)] = cfg
        # Use info with a tiny empty DOWNLOAD fixture, proving normal downloads
        # use regional fallback too, independently of diagnostic probing.
        download = b'DL\x01\x10\x00' + bytes(6)
        blob, ekey = blte(download)
        cfg = f'build-name = regional-test\ndownload = {casc.md5(download).hex()} {ekey}\n'.encode()
        key = casc.md5(cfg).hex()
        self.routes['/catalog/US/odin/123/versions'] = (
            'Region!STRING:0|BuildConfig!HEX:16|CDNConfig!HEX:16\nus|' + key + '|\n').encode()
        self.routes['/present/' + casc.key_path('config', key)] = cfg
        self.routes['/present/' + casc.key_path('data', ekey)] = blob
        args = ['info', '--product', 'odin', '--build', '123', '--ngdp-root', self.base + '/catalog',
                '--cdn-table', self.base + '/live/cdns', '--output', str(self.root / 'regional')]
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            casc.main(args)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report['objects'], 0)
        self.assertEqual(report['cdn_bases'], [self.base + '/missing', self.base + '/present'])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(casc.CascError, 'could not retrieve config'):
            casc.main(args[:-1] + [str(self.root / 'restricted'), '--cdn-region', 'us'])

    def test_corrupt_download_not_published(self):
        encoded, key = blte(b'valid bytes')
        self.routes['/' + casc.key_path('data', key)] = encoded[:-1] + bytes([encoded[-1] ^ 1])
        storage = casc.Storage([self.base], self.root / 'mirror')
        with self.assertRaises(casc.CascError):
            storage.fetch('data', key)
        self.assertFalse(storage.path(casc.key_path('data', key)).exists())


if __name__ == '__main__':
    unittest.main()
