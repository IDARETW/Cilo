import concurrent.futures
import hashlib
import http.server
import importlib.util
import io
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

    def test_corrupt_download_not_published(self):
        encoded, key = blte(b'valid bytes')
        self.routes['/' + casc.key_path('data', key)] = encoded[:-1] + bytes([encoded[-1] ^ 1])
        storage = casc.Storage([self.base], self.root / 'mirror')
        with self.assertRaises(casc.CascError):
            storage.fetch('data', key)
        self.assertFalse(storage.path(casc.key_path('data', key)).exists())


if __name__ == '__main__':
    unittest.main()
