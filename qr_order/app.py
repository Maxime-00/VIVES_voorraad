"""QR requests: read-only by default; no purchase order status transitions."""
import hmac
import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode, urljoin

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from scripts.import_components import API, ImportFailure, ROOT, load_env

log = logging.getLogger('qr_order')


@dataclass
class Settings:
    url: str
    token: str
    username: str
    password: str
    po: int = 1
    dry_run: bool = True
    database: Path = ROOT / 'runtime/qr_requests.sqlite3'
    schema: Path | None = None
    quantity: int = 10

    @classmethod
    def from_env(cls):
        load_env()
        keys = ('INVENTREE_SITE_URL', 'INVENTREE_API_TOKEN', 'QR_USERNAME', 'QR_PASSWORD')
        if any(not os.getenv(k) for k in keys):
            raise RuntimeError('QR-configuratie ontbreekt; zie .env.example')
        mode = os.getenv('QR_DRY_RUN', 'true')
        if mode not in ('true', 'false'):
            raise RuntimeError('QR_DRY_RUN moet true of false zijn')
        return cls(*(os.environ[k] for k in keys), po=int(os.getenv('OPEN_PO_ID', '1')),
                   dry_run=mode == 'true', database=Path(os.getenv('QR_DATABASE', str(ROOT / 'runtime/qr_requests.sqlite3'))),
                   schema=Path(os.environ['QR_OPENAPI_FILE']) if os.getenv('QR_OPENAPI_FILE') else None,
                   quantity=int(os.getenv('QR_QUANTITY', '10')))


class Client(API):
    def rows(self, path):
        records, seen = [], set()
        while path:
            if path in seen:
                raise ImportFailure('Paginatielus')
            seen.add(path)
            data = self.request('GET', path)
            if isinstance(data, list):
                return records + data
            records.extend(data['results'])
            path = urljoin(urljoin(self.base, path), data['next']) if data.get('next') else None
        return records


def verify_schema(schema):
    """Validate the exported installation schema before permitting any live POST."""
    if schema is None:
        raise HTTPException(503, 'Live aanvragen geblokkeerd: OpenAPI-schema ontbreekt.')
    try:
        doc = json.loads(schema.read_text(encoding='utf-8-sig'))
        operation = doc['paths']['/api/order/po-line/']['post']
        model = operation['requestBody']['content']['application/json']['schema']
        while '$ref' in model:
            model = doc['components']['schemas'][model['$ref'].split('/')[-1]]
        fields = model['properties']
        expected = {'order': 'integer', 'part': 'integer', 'quantity': 'number', 'merge_items': 'boolean'}
        if any(fields[k].get('type') != v or fields[k].get('readOnly') for k, v in expected.items()):
            raise ValueError
        for path in ('/api/order/po/{id}/', '/api/order/po-line/', '/api/part/', '/api/company/part/', '/api/company/'):
            if 'get' not in doc['paths'][path]:
                raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, 'Live aanvragen geblokkeerd: OpenAPI-contract wijkt af.') from None


class Orders:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        if settings.quantity <= 0 or settings.po <= 0:
            raise ValueError('Aantal en PO-ID moeten positief zijn')
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (po INTEGER, part INTEGER, state TEXT, PRIMARY KEY(po, part))')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.settings.database, timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def resolve(self, ipn):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ipn):
            raise HTTPException(400, 'Ongeldige onderdeelcode.')
        if str(self.client.request('GET', '').get('apiVersion')) != '570':
            raise HTTPException(503, 'InvenTree API-versie moet 570 zijn.')
        parts = [p for p in self.client.rows('part/?' + urlencode({'IPN': ipn, 'limit': 100})) if p['IPN'] == ipn]
        if len(parts) != 1 or not parts[0].get('active', False):
            raise HTTPException(404, 'Geen uniek actief onderdeel gevonden.')
        part = parts[0]
        suppliers = [s for s in self.client.rows('company/?limit=100') if s['name'].casefold() == 'farnell' and s.get('is_supplier') and s.get('active')]
        if len(suppliers) != 1:
            raise HTTPException(409, 'Geen unieke actieve Farnell-leverancier.')
        supplier = suppliers[0]
        links = [s for s in self.client.rows('company/part/?' + urlencode({'part': part['pk'], 'supplier': supplier['pk'], 'limit': 100}))
                 if s['part'] == part['pk'] and s['supplier'] == supplier['pk'] and s.get('SKU') and s.get('active', True)]
        if len(links) != 1:
            raise HTTPException(409, 'Geen unieke geldige Farnell SupplierPart; bestellen geblokkeerd.')
        po = self.client.request('GET', f'order/po/{self.settings.po}/')
        if po.get('status') != 10 or po.get('supplier') != supplier['pk']:
            raise HTTPException(409, 'Bestellijst moet Pending zijn en bij Farnell horen.')
        return part, links[0], po

    def submit(self, ipn):
        part, link, po = self.resolve(ipn)
        lines = self.client.rows('order/po-line/?' + urlencode({'order': self.settings.po, 'limit': 100}))
        if any(line.get('order') == self.settings.po and (line.get('part') == link['pk'] or line.get('internal_part') == part['pk']) for line in lines):
            return {'message': 'Dit onderdeel staat al op de bestellijst.', 'state': 'existing'}
        if self.settings.dry_run:
            return {'message': 'Dry-run geslaagd. Er is geen bestelregel toegevoegd.', 'state': 'dry-run', 'quantity': self.settings.quantity}
        verify_schema(self.settings.schema)
        # Persist intent BEFORE the network call. A timeout must never trigger a second POST.
        try:
            with self.db() as db:
                db.execute('INSERT INTO requests VALUES (?, ?, ?)', (self.settings.po, part['pk'], 'reserved'))
        except sqlite3.IntegrityError:
            return {'message': 'Aanvraag al verwerkt of in behandeling. Bij twijfel: beheerder laten controleren.', 'state': 'reserved'}
        try:
            # Recheck immediately before writing, without modifying PO status.
            current = self.client.request('GET', f'order/po/{self.settings.po}/')
            if current.get('status') != 10 or current.get('supplier') != po['supplier']:
                raise ImportFailure('Bestellijst gewijzigd')
            self.client.request('POST', 'order/po-line/', {'order': self.settings.po, 'part': link['pk'],
                                'quantity': self.settings.quantity, 'merge_items': False})
        except Exception:
            log.warning('QR-aanvraag vereist handmatige controle')
            raise HTTPException(502, 'Uitkomst onzeker of aanvraag mislukt. Niet opnieuw verzonden; neem contact op met de beheerder.') from None
        with self.db() as db:
            db.execute('UPDATE requests SET state=? WHERE po=? AND part=?', ('sent', self.settings.po, part['pk']))
        return {'message': 'Aanvulling aangevraagd op de bestellijst.', 'state': 'sent'}


def create_app(settings=None, client=None):
    settings = settings or Settings.from_env()
    orders = Orders(settings, client or Client(settings.url, settings.token))
    app = FastAPI(title='VIVES Aanvullen', docs_url=None, redoc_url=None, openapi_url=None)
    security = HTTPBasic()

    def authenticate(credentials: HTTPBasicCredentials = Depends(security)):
        user_ok = hmac.compare_digest(credentials.username.encode(), settings.username.encode())
        pass_ok = hmac.compare_digest(credentials.password.encode(), settings.password.encode())
        if not (user_ok and pass_ok):
            raise HTTPException(401, 'Aanmelden vereist.', headers={'WWW-Authenticate': 'Basic'})

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY'})
        return response

    @app.exception_handler(ImportFailure)
    async def api_error(request, exc):
        from fastapi.responses import JSONResponse
        log.warning('InvenTree-aanroep mislukt; geen response-inhoud gelogd')
        return JSONResponse({'detail': 'InvenTree is niet beschikbaar of weigert de aanvraag.'}, status_code=502)

    @app.get('/onderdeel/{ipn}', response_class=HTMLResponse, dependencies=[Depends(authenticate)])
    def page(ipn: str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', ipn):
            raise HTTPException(400, 'Ongeldige onderdeelcode.')
        return (Path(__file__).parent / 'index.html').read_text(encoding='utf-8')

    @app.get('/api/onderdeel/{ipn}', dependencies=[Depends(authenticate)])
    def details(ipn: str):
        part, link, po = orders.resolve(ipn)
        return {'name': part['name'], 'description': part['description'], 'sku': link['SKU'],
                'quantity': settings.quantity, 'dry_run': settings.dry_run, 'reference': po['reference']}

    @app.post('/api/onderdeel/{ipn}/aanvragen', dependencies=[Depends(authenticate)])
    def submit(ipn: str, request: Request):
        # Cross-origin forms cannot supply this header; CORS is intentionally disabled.
        if request.headers.get('X-QR-Confirm') != 'yes':
            raise HTTPException(403, 'Bevestiging via de webpagina vereist.')
        return orders.submit(ipn)

    return app
