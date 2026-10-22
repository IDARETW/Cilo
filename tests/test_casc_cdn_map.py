import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import casc_catalog as catalog
import casc_cdn_map as mapping
import casc_gui as gui
import casc_probe as probe
from test_casc_catalog import fixture_catalog


class Mapping(unittest.TestCase):
    def test_deduplicates_keys_but_retains_products_and_missing_paths(self):
        row = fixture_catalog()['builds'][0]
        rows = [row, {**row, 'product': 'other'}, {**row, 'cdn_path': 'unknown'}]
        endpoints = {'endpoints': [{'path': 'tpr/odin', 'base': 'https://example/tpr/odin'}]}
        tasks, missing = mapping.matrix_tasks(rows, endpoints, ['build_config'])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(tasks[0][3]), 2)
        self.assertEqual(len(missing), 1)

    def test_transport_failure_and_s3_denial_are_not_deletion_claims(self):
        error = urllib.error.HTTPError('https://example', 503, 'unavailable', {},
                                      io.BytesIO(b'upstream connect error envoy://cloudflare_https_tunnel/'))
        self.assertEqual(mapping.http_failure(error)['error_kind'], 'intermediary_transport')
        error = urllib.error.HTTPError('https://example', 403, 'forbidden', {}, io.BytesIO(b's3:ListBucket'))
        result = mapping.http_failure(error)
        self.assertEqual(result['http_status'], 403)
        self.assertTrue(result['s3_list_bucket_denied'])

    def test_rate_limit_pauses_same_host_and_preserves_other_hosts(self):
        tasks = [(base, '12'*16, 'tpr/odin', []) for base in
                 ['https://one/path', 'https://one/other', 'https://two/path']]
        def check(base, key, timeout):
            return {'base': base, 'verified': False, 'http_status': 429, 'error_kind': 'rate_limited'}
        output = io.StringIO()
        with mock.patch.object(mapping, 'probe_config', side_effect=check) as request:
            mapping.run_matrix(tasks, output, jobs=1)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(json.loads(output.getvalue().splitlines()[1])['error_kind'], 'not_attempted_after_rate_limit')

    def test_observed_hosts_only_reorder_discovered_endpoints(self):
        row = fixture_catalog()['builds'][0]
        row.update(configuration_survey=None, probe={'verified_bases': ['https://unknown', 'https://old']})
        with mock.patch.object(probe.casc, 'read_source', return_value=b''), \
             mock.patch.object(probe.casc, 'parse_rows', return_value=[]), \
             mock.patch.object(probe.casc, 'cdn_bases', return_value=['https://current']), \
             mock.patch.object(probe, 'historical_bases', return_value=['https://old']):
            self.assertEqual(probe.build_bases(row), ['https://old', 'https://current'])

    def test_frozen_command_dispatch_and_auth_redirect(self):
        with mock.patch.object(sys, 'frozen', True, create=True):
            args = gui.download_command(fixture_catalog()['builds'][0], [], Path('output'))
        self.assertEqual(args[1:3], ['--cli', 'extract'])
        request = urllib.request.Request(catalog.CATALOG_API_URL, headers={'Authorization': 'Bearer fixture'})
        redirected = catalog.GitHubRedirect().redirect_request(request, None, 302, '', {}, 'https://other.example/file')
        self.assertIsNone(redirected.get_header('Authorization'))
        redirected = catalog.GitHubRedirect().redirect_request(request, None, 302, '', {}, 'https://api.github.com/file')
        self.assertEqual(redirected.get_header('Authorization'), 'Bearer fixture')

    def test_private_cache_update_retries_transient_without_saving_credentials(self):
        import gzip
        import tempfile
        response = io.BytesIO(gzip.compress(json.dumps(fixture_catalog()).encode()))
        failure = urllib.error.HTTPError(catalog.CATALOG_API_URL, 503, 'temporary', {}, io.BytesIO())
        opener = mock.Mock()
        opener.open.side_effect = [failure, response]
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(catalog.urllib.request, 'build_opener', return_value=opener), \
             mock.patch.object(catalog.time, 'sleep'):
            path = Path(directory) / 'cache.gz'
            result = catalog.update_catalog(path, token='fixture-token')
            self.assertEqual(len(result['builds']), 1)
            self.assertNotIn(b'fixture-token', gzip.decompress(path.read_bytes()))
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, catalog.CATALOG_API_URL)
            self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-token')
            self.assertEqual(opener.open.call_count, 2)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
