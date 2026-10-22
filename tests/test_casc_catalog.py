import gzip
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import casc_catalog as catalog
import casc_download as casc
import casc_gui as gui
import casc_probe as probe
import test_casc_download as fixtures


def fixture_catalog():
    return {'schema': 1, 'generated_at_utc': '2026-10-22T00:00:00+00:00', 'builds': [
        {'product': 'odin', 'build': '123', 'catalog_build': '123', 'region': 'us', 'version': '1.0.123',
         'build_config': '12' * 16, 'cdn_config': None, 'cdn_path': 'tpr/odin',
         'availability': 'config_verified', 'checked_at_utc': '2026-10-22T00:00:00+00:00'}]}


class Catalog(unittest.TestCase):
    def test_all_supplied_records_are_bundled(self):
        data = catalog.load_catalog(catalog.BUNDLED_CATALOG)
        self.assertEqual(len(data['builds']), 35536)
        self.assertEqual(len({r['product'] for r in data['builds']}), 500)
        self.assertEqual(sum(r['product'] == 'odin' for r in data['builds']), 315)
        self.assertTrue(any(r['build'] == '28697R' for r in data['builds']))

    def test_rejects_invalid_or_oversized_cache_without_writing(self):
        data = fixture_catalog()
        data['builds'][0]['product'] = '../wrong'
        with self.assertRaises(ValueError):
            catalog.decode_catalog(gzip.compress(json.dumps(data).encode()))
        with mock.patch.object(catalog, 'MAX_JSON', 10), self.assertRaisesRegex(ValueError, 'expanded'):
            catalog.decode_catalog(gzip.compress(b'a' * 100))

    def test_failed_atomic_update_keeps_previous_cache_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache.gz'
            catalog.save_catalog(fixture_catalog(), path)
            previous = path.read_bytes()
            with mock.patch.object(catalog.os, 'replace', side_effect=OSError('simulated write failure')):
                with self.assertRaises(OSError):
                    catalog.save_catalog(fixture_catalog(), path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_probe_timestamps_and_product_identity_control_merges(self):
        data = fixture_catalog()
        check = {'product': 'odin', 'build_config': '12' * 16, 'stage': 'sample_verified',
                 'checked_at_utc': '2026-10-22T03:00:00+00:00'}
        catalog.merge_probes(data, [check])
        row = data['builds'][0]
        self.assertEqual(row['availability'], 'sample_verified')
        catalog.merge_probes(data, [{**check, 'stage': 'not_retrieved', 'checked_at_utc': '2026-10-22T01:00:00+00:00'}])
        self.assertEqual(row['availability'], 'sample_verified')
        catalog.merge_probes(data, [{**check, 'product': 'brynhildr_odin', 'stage': 'not_retrieved'}])
        self.assertEqual(row['availability'], 'sample_verified')
        catalog.merge_probes(data, [{**check, 'stage': 'not_retrieved', 'checked_at_utc': '2026-10-22T04:00:00+00:00'}])
        self.assertEqual(row['availability'], 'not_retrieved')

    def test_gui_command_preserves_exact_keys_and_literal_paths(self):
        row = fixture_catalog()['builds'][0]
        row['cdn_config'] = '34' * 16
        args = gui.download_command(row, ['https://example/tpr/odin'], Path('A path with spaces'), pattern='english/*.ff')
        self.assertEqual(args[args.index('--build-config') + 1], row['build_config'])
        self.assertEqual(args[args.index('--cdn-config') + 1], row['cdn_config'])
        self.assertEqual(args[args.index('--output') + 1], 'A path with spaces')
        self.assertEqual(args[args.index('--pattern') + 1], 'english/*.ff')
        self.assertNotIn('--build', args)


class CatalogNetwork(unittest.TestCase):
    setUp = fixtures.Network.setUp
    tearDown = fixtures.Network.tearDown

    def test_update_cache_from_github_style_url_and_reject_bad_response(self):
        path = self.root / 'cache/catalog.gz'
        local = fixture_catalog()
        check = {'product': 'odin', 'build_config': '12' * 16, 'stage': 'sample_verified',
                 'checked_at_utc': '2026-10-22T03:00:00+00:00'}
        catalog.merge_probes(local, [check])
        catalog.save_catalog(local, path)
        self.routes['/github/cache.gz'] = gzip.compress(json.dumps(fixture_catalog()).encode())
        result = catalog.update_catalog(path, self.base + '/github/cache.gz')
        self.assertEqual(result['builds'][0]['availability'], 'sample_verified')
        before = path.read_bytes()
        self.routes['/github/cache.gz'] = b'error page'
        with self.assertRaises(OSError):
            catalog.update_catalog(path, self.base + '/github/cache.gz')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def payload_fixture(self):
        raw = b'file content for a real decoded sample' * 10
        payload, ekey = fixtures.blte(raw)
        paths = b'\x07file.ff\xff' + struct.pack('>I', 0)
        spans = b'\x01' + struct.pack('>II', 0, len(raw)) + b'\x00'
        header = b'TVFS\x01\x26\x09\x09' + bytes(4)
        header += struct.pack('>6I', 38, len(paths), 38+len(paths), len(spans), 38+len(paths)+len(spans), 9) + b'\0\x01'
        tvfs = header + paths + spans + bytes.fromhex(ekey)[:9]
        tvfs_encoded, tvfs_key = fixtures.blte(tvfs)
        download = b'DL\x01\x10\x00' + struct.pack('>IH', 1, 0)
        download += bytes.fromhex(ekey) + len(payload).to_bytes(5, 'big') + b'\0'
        dl_encoded, dl_key = fixtures.blte(download)
        cfg = (f'download = {casc.md5(download).hex()} {dl_key}\nvfs-root = {casc.md5(tvfs).hex()} {tvfs_key}\n').encode()
        row = fixture_catalog()['builds'][0]
        row['build_config'] = casc.md5(cfg).hex()
        for kind, key, data in [('config', row['build_config'], cfg), ('data', dl_key, dl_encoded),
                                ('data', tvfs_key, tvfs_encoded), ('data', ekey, payload)]:
            self.routes['/' + casc.key_path(kind, key)] = data
        return row, ekey

    def test_success_and_failed_payload_probes_remove_all_temporary_files(self):
        row, key = self.payload_fixture()
        parent = self.root / 'temporary'
        parent.mkdir()
        result = probe.probe_build(row, bases=[self.base], temp_parent=parent, config_fallback=False)
        self.assertEqual(result['stage'], 'sample_verified')
        self.assertTrue(result['samples'][0]['ok'])
        self.assertEqual(list(parent.iterdir()), [])
        self.routes.pop('/' + casc.key_path('data', key))
        result = probe.probe_build(row, bases=[self.base], temp_parent=parent, config_fallback=False)
        self.assertEqual(result['stage'], 'manifests_verified')
        self.assertFalse(result['samples'][0]['ok'])
        self.assertEqual(list(parent.iterdir()), [])

    def test_probe_configuration_failure_cleans_partial_downloads(self):
        parent = self.root / 'temporary'
        parent.mkdir()
        result = probe.probe_build(fixture_catalog()['builds'][0], bases=[self.base], temp_parent=parent, config_fallback=False)
        self.assertEqual(result['stage'], 'not_retrieved')
        self.assertEqual(list(parent.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
