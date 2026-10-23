"""End-to-end local CDN → BLTE → native archive tests (build native Cilo first)."""
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_casc_download as fixtures
from cilo_bridge import ArchiveStream, ArchiveCancelled, find_cilo, native_json
from cilo_browser import hex_view, text_view
from casc_gui import download_command

blte, casc = fixtures.blte, fixtures.casc


class ArchiveDownload(unittest.TestCase):
    setUp = fixtures.Network.setUp
    tearDown = fixtures.Network.tearDown

    def fixture(self):
        try:
            self.binary = find_cilo()
        except ValueError as exc:
            self.skipTest(str(exc))
        self.files = {'hello.txt': b'hello from the CDN\n' * 20,
                      'binary.ff': bytes(range(256)) * 1024}
        paths, spans, keys, records = b'', b'', b'', b''
        self.objects = {}
        def object(raw):
            encoded, key = blte(raw)
            self.routes['/' + casc.key_path('data', key)] = encoded
            self.objects[key] = encoded
            return key, len(encoded)
        for name, raw in self.files.items():
            key, length = object(raw)
            paths += bytes([len(name)]) + name.encode() + b'\xff' + struct.pack('>I', len(spans))
            spans += b'\x01' + struct.pack('>II', 0, len(raw)) + bytes([len(keys)])
            keys += bytes.fromhex(key)[:9]
            records += bytes.fromhex(key) + length.to_bytes(5, 'big') + b'\x01'
        tvfs = b'TVFS\x01\x26\x09\x09' + bytes(4)
        tvfs += struct.pack('>6I', 38, len(paths), 38 + len(paths), len(spans),
                            38 + len(paths) + len(spans), len(keys)) + b'\0\x01' + paths + spans + keys
        download = b'DL\x01\x10\x00' + struct.pack('>IH', len(self.files), 0) + records
        root_key, _ = object(tvfs)
        download_key, _ = object(download)
        config = (f'build-name = archive-fixture\nvfs-root = {casc.md5(tvfs).hex()} {root_key}\n'
                  f'download = {casc.md5(download).hex()} {download_key}\n').encode()
        self.config_key = casc.md5(config).hex()
        self.routes['/' + casc.key_path('config', self.config_key)] = config
        self.output = self.root / 'result.cilo'
        return ['--build-config', self.config_key, '--cdn-base', self.base, '--config-fallback', 'none',
                '--archive', str(self.output), '--compression', 'fast', '--cilo', self.binary]

    def test_extract_and_mirror_stream_verified_bytes_with_no_staging_left(self):
        args = self.fixture()
        for action in ('extract', 'mirror'):
            with contextlib.redirect_stdout(io.StringIO()) as log:
                casc.main([action, *args])
            self.assertEqual(json.loads(log.getvalue().splitlines()[-1])['state'], 'published')
            self.assertEqual(list(self.root.iterdir()), [self.output])
            native_json('verify', self.output, executable=self.binary)
            metadata = native_json('contents', self.output, executable=self.binary)
            expected = self.files if action == 'extract' else {
                **{'cdn/' + casc.key_path('data', key): value for key, value in self.objects.items()},
                'cdn/' + casc.key_path('config', self.config_key): self.routes['/' + casc.key_path('config', self.config_key)]}
            self.assertEqual(len(metadata['files']), len(expected) + (action == 'mirror'))
            for name, raw in expected.items():
                actual = subprocess.check_output([self.binary, 'cat', str(self.output), '--', name])
                self.assertEqual(actual, raw)
            self.output.unlink()

    def test_corrupt_payload_aborts_archive_and_removes_download_scratch(self):
        args = self.fixture()
        payload_key = next(iter(self.objects))
        route = '/' + casc.key_path('data', payload_key)
        self.routes[route] = self.routes[route][:-1] + bytes([self.routes[route][-1] ^ 1])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(casc.CascError):
            casc.main(['extract', *args])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cancellation_and_short_writer_abort_cleanly(self):
        self.fixture()
        flag = self.root / 'cancel'
        with self.assertRaises(ArchiveCancelled):
            with ArchiveStream(self.output, 'fast', executable=self.binary, cancel=flag) as writer:
                def write(sink):
                    sink.write(b'first')
                    flag.touch()
                    sink.write(b'last')
                writer.add('cancelled.txt', 9, write)
        self.assertEqual(list(self.root.iterdir()), [flag])
        flag.unlink()
        with self.assertRaisesRegex(ValueError, 'length mismatch'):
            with ArchiveStream(self.output, 'fast', executable=self.binary) as writer:
                writer.add('short.txt', 10, lambda sink: sink.write(b'short'))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_existing_archive_and_symlink_are_never_overwritten(self):
        args = self.fixture()
        self.output.write_bytes(b'keep this')
        with self.assertRaises(casc.CascError):
            casc.main(['extract', *args])
        self.assertEqual(self.output.read_bytes(), b'keep this')
        self.output.unlink()
        if os.name != 'nt':
            self.output.symlink_to(self.root / 'missing')
            with self.assertRaises(casc.CascError):
                casc.main(['extract', *args])
            with self.assertRaises(ValueError):
                with ArchiveStream(self.output, executable=self.binary):
                    self.fail('followed dangling symlink')
            self.assertFalse((self.root / 'missing').exists())

    def test_gui_command_and_preview_rendering(self):
        row = {'product': 'odin', 'build_config': '12' * 16}
        args = download_command(row, [self.base], self.root / 'path with spaces.cilo',
                                archive=True, profile='max', cancel=self.root / 'cancel')
        self.assertNotIn('--output', args)
        self.assertIn('--archive', args)
        self.assertEqual(args[args.index('--compression') + 1], 'max')
        self.assertIn('--cancel-file', args)
        self.assertIn('00 41 ff', hex_view(b'\0A\xff'))
        self.assertEqual(text_view(b'\0hello\n\x1b'), '.hello\n.')
