import copy
import csv
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from scripts.import_components import API, Importer, ImportFailure, PATHS, ROOT, read_rows
from scripts.import_components import load_env
from scripts.import_components import safe_endpoint, safe_response_summary


class ConfigurationTests(unittest.TestCase):
    def test_root_env_from_other_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text(
                '\ufeffConfiguratie van de VM\n'
                'export INVENTREE_SITE_URL = "https://example.org" # VM\n'
                "INVENTREE_API_TOKEN='test-only-token'\n", encoding='utf-8')
            other = root / 'elsewhere'
            other.mkdir()
            previous = Path.cwd()
            try:
                os.chdir(other)
                with patch('scripts.import_components.ROOT', root), patch.dict(os.environ, {}, clear=True):
                    load_env()
                    self.assertEqual(os.environ['INVENTREE_SITE_URL'], 'https://example.org')
                    self.assertEqual(os.environ['INVENTREE_API_TOKEN'], 'test-only-token')
                    with patch.dict(os.environ, {'INVENTREE_SITE_URL': 'https://override.example.org'}):
                        load_env()
                        self.assertEqual(os.environ['INVENTREE_SITE_URL'], 'https://override.example.org')
            finally:
                os.chdir(previous)

    def test_env_comments_and_literal_values(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / '.env'
            path.write_text('INVENTREE_API_TOKEN=abc#123 # comment\n'
                            'INVENTREE_SITE_URL=https://example.org # VM\n', encoding='utf-8')
            load_env(path)
            self.assertEqual(os.environ['INVENTREE_API_TOKEN'], 'abc#123')
            self.assertEqual(os.environ['INVENTREE_SITE_URL'], 'https://example.org')

    def test_invalid_env_does_not_disclose_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('INVENTREE_API_TOKEN="private-test-value', encoding='utf-8')
            with self.assertRaises(ImportFailure) as context:
                load_env(path)
            self.assertNotIn('private-test-value', str(context.exception))


class FakeAPI:
    def __init__(self):
        self.rows = {k: [] for k in PATHS}
        self.posts = 0

    def list(self, kind):
        return copy.deepcopy(self.rows[kind])

    def request(self, method, path, payload):
        assert method == 'POST'
        self.posts += 1
        kind = next(k for k, v in PATHS.items() if v == path)
        record = dict(payload, pk=self.posts)
        self.rows[kind].append(record)
        return record


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.rows = read_rows(ROOT / 'data/b303_components.csv')
        self.output = redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def validate(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.csv'
            with path.open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=self.rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            return read_rows(path)

    def test_catalogue(self):
        self.assertEqual(len(self.rows), 10)

    def test_bad_csv(self):
        for change in ({'ipn': ''}, {'verified': 'yes'}, {'pack_quantity': 'NaN'},
                       {'pack_quantity': '-2'}, {'product_url': 'https://example.org'},
                       {'manufacturer': ''}, {'verified': 'false'}):
            with self.subTest(change=change), self.assertRaises(ImportFailure):
                self.validate([dict(self.rows[0], **change)])
        with self.assertRaises(ImportFailure):
            self.validate([self.rows[0], self.rows[0]])

    def test_missing_sku_skipped(self):
        api = FakeAPI()
        Importer(api, True).run([self.rows[6]])
        self.assertEqual(len(api.rows['part']), 1)
        self.assertEqual(api.rows['supplier'], [])

    def test_repeat_import_no_posts(self):
        api = FakeAPI()
        Importer(api, True).run(self.rows)
        count = api.posts
        Importer(api, True).run(self.rows)
        self.assertEqual(api.posts, count)
        self.assertEqual(len(api.rows['part']), 10)
        self.assertEqual(len(api.rows['supplier']), 9)

    def test_dry_run_no_writes(self):
        api = FakeAPI()
        Importer(api).run(self.rows)
        self.assertEqual(api.posts, 0)
        self.assertFalse(any(api.rows.values()))

    def test_case_insensitive_company(self):
        api = FakeAPI()
        api.rows['company'] = [{'pk': 99, 'name': 'FARNELL', 'is_supplier': True, 'active': True}]
        Importer(api, True).run(self.rows[:1])
        self.assertEqual(len(api.rows['company']), 1)

    def test_wrong_supplier_part_conflict(self):
        api = FakeAPI()
        Importer(api, True).run(self.rows[:1])
        api.rows['supplier'][0]['part'] = 999
        with self.assertRaises(ImportFailure):
            Importer(api, True).run(self.rows[:1])

    def test_ambiguous_ipn(self):
        api = FakeAPI()
        Importer(api, True).run(self.rows[:1])
        api.rows['part'].append(dict(api.rows['part'][0], pk=999))
        with self.assertRaises(ImportFailure):
            Importer(api, True).run(self.rows[:1])

    def test_http_failures(self):
        api = API('https://example.org', 'secret')
        for error in (HTTPError(api.base, 403, 'Forbidden', {}, None), URLError('offline')):
            with patch.object(api.opener, 'open', side_effect=error), self.assertRaises(ImportFailure) as context:
                api.request('GET', 'part/')
            self.assertNotIn('secret', str(context.exception))

    def test_pagination_and_origin(self):
        api = API('https://example.org', 'secret')
        with patch.object(api, 'request', side_effect=[{'results': [{'pk': 1}], 'next': '?offset=1'}, {'results': [{'pk': 2}], 'next': None}]):
            self.assertEqual(len(api.list('part')), 2)
        with self.assertRaises(ImportFailure):
            api.request('GET', 'https://evil.example.org/')

    def test_error_reports_endpoint_status_without_secrets(self):
        api = API('https://example.org', 'test-v2-secret', verbose=True)
        body = b'{"detail":"Invalid token test-v2-secret password=another-secret"}'
        error = HTTPError(api.base, 400, 'Bad Request', {}, io.BytesIO(body))
        logs = io.StringIO()
        with patch.object(api.opener, 'open', side_effect=error), redirect_stderr(logs):
            with self.assertRaises(ImportFailure) as context:
                api.request('GET', 'part/?offset=20&token=test-v2-secret')
        result = str(context.exception) + logs.getvalue()
        self.assertIn('GET /api/part/?offset=20', result)
        self.assertIn('HTTP 400', result)
        self.assertNotIn('test-v2-secret', result)
        self.assertNotIn('another-secret', result)

    def test_unknown_response_values_not_logged(self):
        result = safe_response_summary(b'{"detail":"password=never-log-me"}')
        self.assertIn('detail', result)
        self.assertNotIn('never-log-me', result)
        self.assertIn('Host geweigerd', safe_response_summary(b'Invalid HTTP_HOST header: secret'))

    def test_v2_token_sent_unchanged_in_token_header(self):
        token = 'inv-test-v2.secret-material'
        api = API('https://example.org', token)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.object(api.opener, 'open', return_value=response) as opened:
            api.request('GET', 'part/?limit=2&offset=0')
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Token ' + token)
        self.assertIsNone(request.data)
        self.assertIsNone(request.get_header('Content-type'))
        self.assertEqual(request.full_url, 'https://example.org/api/part/?limit=2&offset=0')

    def test_pagination_resolves_against_current_endpoint(self):
        api = API('https://example.org', 'test-token')
        with patch.object(api, 'request', side_effect=[
                {'results': [], 'next': '?offset=20'}, {'results': [], 'next': None}]) as request:
            api.list('part')
        self.assertEqual(request.call_args_list[1].args, ('GET', 'https://example.org/api/part/?offset=20'))

    def test_token_with_prefix_rejected_without_disclosure(self):
        with self.assertRaises(ImportFailure) as context:
            API('https://example.org', 'Token private-test-value')
        self.assertNotIn('private-test-value', str(context.exception))

    def test_schema_guard(self):
        api = API('https://example.org', 'secret')
        for responses in ([{'apiVersion': 569}], [{'apiVersion': 570}, {}]):
            with patch.object(api, 'request', side_effect=responses), self.assertRaises(ImportFailure):
                api.preflight()


if __name__ == '__main__':
    unittest.main()
