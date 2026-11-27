"""Read-only installation contract + PO snapshot check, run inside qr-web.

The browser confirmation is tested separately. No POSTs are sent by this script.
"""
import hashlib
import json
import sys
from pathlib import Path
from fastapi import HTTPException
from qr_order.app import Client, Settings, Orders, verify_schema
from scripts.import_components import ImportFailure


def check(settings, client):
    orders = Orders(settings, client)
    if not settings.dry_run:
        raise ImportFailure('Controle vereist QR_DRY_RUN=true')
    schema = client.request('GET', 'schema/?format=json')
    target = settings.database.parent / 'openapi-v570.json'
    target.write_text(json.dumps(schema), encoding='utf-8')
    verify_schema(target)
    part, link, po = orders.resolve('B303-002')
    if link['SKU'] != '9339051' or po['reference'] != 'PO-0001':
        raise ImportFailure('Testcomponent of PO wijkt af')

    def snapshot():
        data = [client.request('GET', 'order/po/1/'), client.rows('order/po-line/?order=1&limit=100')]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    before = snapshot()
    result = orders.submit('B303-002')
    if snapshot() != before:
        raise ImportFailure('PO of regels gewijzigd tijdens controle; onderzoek externe schrijvers')
    print('PASS: v570-contract, B303-002, SKU, PO en dry-run; PO-snapshot ongewijzigd; status=' + result['state'])


if __name__ == '__main__':
    try:
        settings = Settings.from_env()
        check(settings, Client(settings.url, settings.token, read_only=True))
    except (ImportFailure, HTTPException, OSError, ValueError, KeyError, RuntimeError):
        print('FAIL: controle niet geslaagd; controleer configuratie, bereikbaarheid en v570-contract. Geen secrets gelogd.', file=sys.stderr)
        sys.exit(1)
