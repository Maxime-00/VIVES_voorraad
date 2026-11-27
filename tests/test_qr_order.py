import copy
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
from qr_order.app import Settings, Orders, create_app, verify_schema


class MockInvenTree:
    def __init__(self):
        self.posts = []
        self.lines = []
        self.status = 10
        self.supplier = 7
        self.links = [{'pk': 8, 'part': 2, 'supplier': 7, 'SKU': '9339051', 'active': True}]
        self.fail_post = False

    def request(self, method, path, payload=None):
        if method == 'POST':
            assert path == 'order/po-line/'
            self.posts.append(payload)
            if self.fail_post:
                raise TimeoutError()
            return {'pk': 42}
        assert method == 'GET'
        if path == '':
            return {'apiVersion': 570}
        if path == 'order/po/1/':
            return {'pk': 1, 'reference': 'PO-0001', 'status': self.status, 'supplier': self.supplier}
        raise AssertionError(path)

    def rows(self, path):
        if path.startswith('part/?'):
            return [{'pk': 2, 'IPN': 'B303-002', 'name': 'Resistor 1K', 'description': '1 kohm', 'active': True}]
        if path.startswith('company/?'):
            return [{'pk': 7, 'name': 'Farnell', 'active': True, 'is_supplier': True}]
        if path.startswith('company/part/?'):
            return copy.deepcopy(self.links)
        if path.startswith('order/po-line/?'):
            return self.lines
        raise AssertionError(path)


class QRTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = Settings('https://example.org', 'server-token', 'teacher', 'test-password', database=Path(tmp.name) / 'db.sqlite')
        self.api = MockInvenTree()
        self.client = TestClient(create_app(self.settings, self.api))
        self.auth = ('teacher', 'test-password')
        self.url = '/api/onderdeel/B303-002'

    def test_authentication_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.post(self.url + '/aanvragen').status_code, 401)

    def test_scan_and_details_never_write(self):
        self.assertEqual(self.client.get('/onderdeel/B303-002', auth=self.auth).status_code, 200)
        response = self.client.get(self.url, auth=self.auth)
        self.assertEqual(response.json()['sku'], '9339051')
        self.assertNotIn('server-token', response.text)
        self.assertEqual(self.api.posts, [])

    def test_confirmation_and_dry_run(self):
        self.assertEqual(self.client.post(self.url + '/aanvragen', auth=self.auth).status_code, 403)
        response = self.client.post(self.url + '/aanvragen', auth=self.auth, headers={'X-QR-Confirm': 'yes'})
        self.assertEqual(response.json()['state'], 'dry-run')
        self.assertEqual(self.api.posts, [])

    def test_existing_line_no_write(self):
        self.api.lines = [{'order': 1, 'part': 8}]
        self.assertEqual(Orders(self.settings, self.api).submit('B303-002')['state'], 'existing')
        self.assertFalse(self.api.posts)

    def test_invalid_po_or_link_blocked(self):
        for status, supplier, links in [(20, 7, self.api.links), (10, 99, self.api.links), (10, 7, []), (10, 7, self.api.links * 2)]:
            self.api.status, self.api.supplier, self.api.links = status, supplier, links
            with self.assertRaises(HTTPException):
                Orders(self.settings, self.api).submit('B303-002')
        self.assertFalse(self.api.posts)

    def test_schema_required_for_live_mode(self):
        self.settings.dry_run = False
        with self.assertRaises(HTTPException):
            Orders(self.settings, self.api).submit('B303-002')
        self.assertFalse(self.api.posts)

    def test_concurrent_live_requests_only_one_post(self):
        self.settings.dry_run = False
        with patch('qr_order.app.verify_schema'):
            def submit(_):
                return Orders(self.settings, self.api).submit('B303-002')['state']
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(submit, range(6)))
        self.assertEqual(results.count('sent'), 1)
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.api.posts[0], {'order': 1, 'part': 8, 'quantity': 10, 'merge_items': False})

    def test_timeout_persists_reservation_across_restarts(self):
        self.settings.dry_run = False
        self.api.fail_post = True
        with patch('qr_order.app.verify_schema'):
            with self.assertRaises(HTTPException):
                Orders(self.settings, self.api).submit('B303-002')
            self.assertEqual(Orders(self.settings, self.api).submit('B303-002')['state'], 'reserved')
        self.assertEqual(len(self.api.posts), 1)

    def test_schema_rejects_missing_contract(self):
        file = self.settings.database.parent / 'schema.json'
        file.write_text('{}')
        with self.assertRaises(HTTPException):
            verify_schema(file)


if __name__ == '__main__':
    unittest.main()
