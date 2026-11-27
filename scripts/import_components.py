"""Safe, repeatable catalogue import. Python 3.10+, standard library only."""
import argparse
import csv
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qsl
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
PATHS = {'category': 'part/category/', 'part': 'part/',
         'company': 'company/', 'supplier': 'company/part/'}


class ImportFailure(Exception):
    pass


def load_env(path=None):
    """Read repository .env, independent of cwd, without executing its contents.

    Supports UTF-8 BOM, optional export, quotes and trailing comments.
    Values are literal: no shell evaluation or variable interpolation.
    Existing environment entries (including empty ones) take precedence.
    """
    path = ROOT / '.env' if path is None else Path(path)
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)', line)
        if not match:
            # Shared Docker .env files can contain standalone section labels.
            # They are never evaluated as code or used as configuration.
            continue
        key, value = match.groups()
        if value.startswith(('"', "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith('#')):
                raise ImportFailure(f'.env regel {number}: ongeldige aanhalingstekens')
            value = value[1:end]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0].rstrip()
        os.environ.setdefault(key, value)


def read_rows(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        required = {'ipn', 'name', 'description', 'category', 'sku', 'verified',
                    'manufacturer', 'mpn', 'pack_quantity', 'product_url'}
        if not required.issubset(reader.fieldnames or []):
            raise ImportFailure('CSV mist verplichte kolommen')
        rows, ipns, skus = [], set(), set()
        for number, raw in enumerate(reader, 2):
            if None in raw or any(v is None for v in raw.values()):
                raise ImportFailure(f'CSV rij {number}: ongeldig aantal velden')
            row = {k: v.strip() for k, v in raw.items()}
            if any(not row[k] for k in ('ipn', 'name', 'description', 'category')):
                raise ImportFailure(f'CSV rij {number}: verplicht veld leeg')
            if row['ipn'] in ipns or (row['sku'] and row['sku'] in skus):
                raise ImportFailure(f'CSV rij {number}: dubbele IPN of SKU')
            ipns.add(row['ipn'])
            if row['sku']:
                skus.add(row['sku'])
            if row['verified'] not in ('true', 'false'):
                raise ImportFailure(f'CSV rij {number}: verified moet true/false zijn')
            if row['sku']:
                if row['verified'] != 'true' or any(not row[k] for k in
                        ('manufacturer', 'mpn', 'product_url', 'pack_quantity')):
                    raise ImportFailure(f'CSV rij {number}: SKU zonder volledige verificatie')
                url = urlsplit(row['product_url'])
                if url.scheme != 'https' or url.hostname != 'be.farnell.com' or not url.path.endswith('/dp/' + row['sku']):
                    raise ImportFailure(f'CSV rij {number}: ongeldige Farnell-productpagina')
                try:
                    quantity = Decimal(row['pack_quantity'])
                    if not quantity.is_finite() or quantity <= 0:
                        raise InvalidOperation
                except InvalidOperation:
                    raise ImportFailure(f'CSV rij {number}: ongeldige pack_quantity')
            elif row['verified'] == 'true':
                raise ImportFailure(f'CSV rij {number}: verified zonder SKU')
            rows.append(row)
    if not rows:
        raise ImportFailure('CSV is leeg')
    return rows


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def safe_endpoint(url):
    """Never log query values except numeric pagination parameters."""
    parsed = urlsplit(url)
    # Only known static API paths are emitted verbatim.
    known = ('', *PATHS.values(), 'schema/')
    path = next(('/api/' + p for p in known if parsed.path.endswith('/api/' + p)), '/api/[endpoint]')
    params = []
    for key, value in parse_qsl(parsed.query):
        if key in ('limit', 'offset', 'page', 'page_size') and value.isascii() and value.isdigit():
            params.append(key + '=' + value)
        else:
            params.append('[parameter]=[redacted]')
    return path + ('?' + '&'.join(params) if params else '')


def safe_response_summary(body):
    """Classify errors using fixed messages; never echo server-supplied values.

    Servers can reflect credentials, passwords, URLs or debug settings in bodies.
    Even JSON error messages therefore must not be logged verbatim.
    """
    text = body.decode('utf-8', errors='replace').casefold()
    if 'disallowedhost' in text or 'invalid http_host' in text:
        return 'Host geweigerd door Django (controleer ALLOWED_HOSTS op de server)'
    if 'expired' in text and 'token' in text:
        return 'API-token verlopen'
    if 'token' in text and any(word in text for word in ('invalid', 'malformed', 'format', 'version')):
        return 'API-token geweigerd of ongeldig formaat; gebruik het volledige token'
    if 'authentication credentials' in text or 'not authenticated' in text:
        return 'Authenticatie ontbreekt of is geweigerd'
    if 'permission' in text or 'forbidden' in text:
        return 'Toegang geweigerd'
    if 'csrf' in text:
        return 'CSRF-controle geweigerd'
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError):
        return 'Niet-JSON foutresponse; inhoud verborgen'
    if isinstance(data, dict):
        fields = [k for k in ('detail', 'error', 'errors', 'non_field_errors', 'code', 'message') if k in data]
        return 'JSON foutresponse' + (' met velden: ' + ', '.join(fields) if fields else '') + '; waarden verborgen'
    return 'JSON foutresponse; inhoud verborgen'


class API:
    def __init__(self, url, token, verbose=False):
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ImportFailure('Ongeldige INVENTREE_SITE_URL')
        self.base = url.rstrip('/') + '/'
        if not self.base.endswith('/api/'):
            self.base += 'api/'
        self.token = token
        self.verbose = verbose
        if not token or any(c.isspace() for c in token) or not token.isascii():
            raise ImportFailure('Ongeldig API-token: gebruik alleen het volledige token zonder prefix of witruimte')
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, payload=None):
        url = urljoin(self.base, path)
        if not url.startswith(self.base):
            raise ImportFailure('API-paginering verlaat de ingestelde API')
        endpoint = safe_endpoint(url)
        headers = {'Authorization': 'Token ' + self.token, 'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(url, method=method, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
        if self.verbose:
            print(f'API {method} {endpoint}: verzonden', file=sys.stderr)
        try:
            with self.opener.open(req, timeout=30) as response:
                if self.verbose:
                    print(f'API {method} {endpoint}: HTTP {response.status}', file=sys.stderr)
                return json.load(response)
        except HTTPError as exc:
            try:
                summary = safe_response_summary(exc.read(32768))
            except OSError:
                summary = 'Foutresponse niet leesbaar'
            raise ImportFailure(f'API {method} {endpoint}: HTTP {exc.code}; {summary}') from None
        except (URLError, TimeoutError, ValueError, OSError):
            raise ImportFailure(f'API {method} {endpoint}: verbinding of JSON-response ongeldig') from None

    def preflight(self):
        if str(self.request('GET', '').get('apiVersion')) != '570':
            raise ImportFailure('API-versie is niet 570; controleer het installatieschema')
        fields = {'category': {'name', 'parent'}, 'part': {'name', 'IPN', 'description', 'category', 'purchaseable', 'component', 'active'},
                  'company': {'name', 'website', 'is_supplier', 'active'},
                  'supplier': {'part', 'supplier', 'SKU', 'pack_quantity', 'link', 'description'}}
        for kind, needed in fields.items():
            writable = self.request('OPTIONS', PATHS[kind]).get('actions', {}).get('POST', {})
            if not needed.issubset(writable) or any(writable[k].get('read_only') for k in needed):
                raise ImportFailure(f'Schema mismatch voor {kind}; controleer OpenAPI /api/schema/')

    def list(self, kind):
        path, rows, seen = PATHS[kind], [], set()
        while path:
            if path in seen:
                raise ImportFailure('API-paginering bevat een lus')
            seen.add(path)
            data = self.request('GET', path)
            if isinstance(data, list):
                return rows + data
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise ImportFailure('Ongeldige API-lijst')
            rows.extend(data['results'])
            next_path = data.get('next')
            path = urljoin(urljoin(self.base, path), next_path) if next_path else None
        return rows


class Importer:
    def __init__(self, api=None, apply=False):
        if apply and api is None:
            raise ImportFailure('Apply vereist een API')
        self.api, self.apply = api, apply
        self.cache = {k: api.list(k) if api else [] for k in PATHS}
        self.counter = 0

    def ensure(self, kind, key, payload):
        matches = [r for r in self.cache[kind] if all(
            str(r.get(k, '')).casefold() == v.casefold() if kind == 'company' and k == 'name'
            else r.get(k) == v for k, v in key.items())]
        if len(matches) > 1:
            raise ImportFailure(f'Meerdere bestaande {kind} records voor {key}')
        if matches:
            record = matches[0]
            for field in payload:
                if field in ('part', 'category', 'is_supplier', 'active') and record.get(field) != payload[field]:
                    raise ImportFailure(f'Bestaande {kind} conflicteert op {field}: {key}')
            print(f'BESTAAT {kind} {key}')
            return record['pk']
        print(f'{"MAAK" if self.apply else "PLAN"} {kind} {key}')
        self.counter -= 1
        record = self.api.request('POST', PATHS[kind], payload) if self.apply else dict(payload, pk=self.counter)
        self.cache[kind].append(record)
        return record['pk']

    def run(self, rows):
        root = self.ensure('category', {'name': 'Electronics', 'parent': None}, {'name': 'Electronics', 'parent': None})
        company = self.ensure('company', {'name': 'Farnell'}, {'name': 'Farnell', 'website': 'https://be.farnell.com', 'is_supplier': True, 'active': True})
        # Company identity is case-insensitive to avoid a second Farnell record.
        for row in rows:
            cat = self.ensure('category', {'name': row['category'], 'parent': root}, {'name': row['category'], 'parent': root})
            part = self.ensure('part', {'IPN': row['ipn']}, {'IPN': row['ipn'], 'name': row['name'], 'description': row['description'], 'category': cat, 'purchaseable': True, 'component': True, 'active': True})
            if not row['sku']:
                print(f'SKIP SupplierPart {row["ipn"]}: SKU nog niet geverifieerd')
                continue
            self.ensure('supplier', {'supplier': company, 'SKU': row['sku']}, {'supplier': company, 'SKU': row['sku'], 'part': part, 'pack_quantity': row['pack_quantity'], 'link': row['product_url'], 'description': row['manufacturer'] + ' / ' + row['mpn']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, default=ROOT / 'data/b303_components.csv')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Plan tegen lege catalogus, zonder API')
    parser.add_argument('--verbose', action='store_true', help='Log veilige endpoints en HTTP-statussen')
    args = parser.parse_args()
    try:
        if args.limit is not None and args.limit <= 0:
            raise ImportFailure('--limit moet positief zijn')
        if args.apply and args.offline:
            raise ImportFailure('--offline en --apply zijn onverenigbaar')
        rows = read_rows(args.csv)
        load_env()
        api = None
        if not args.offline:
            url, token = os.getenv('INVENTREE_SITE_URL'), os.getenv('INVENTREE_API_TOKEN')
            if not url or not token:
                missing = [key for key in ('INVENTREE_SITE_URL', 'INVENTREE_API_TOKEN')
                           if not os.getenv(key)]
                raise ImportFailure('Ontbrekende of lege configuratie: ' + ', '.join(missing)
                                    + '; controleer .env in de repository-root of gebruik --offline')
            api = API(url, token, verbose=args.verbose)
            api.preflight()
        print('APPLY' if args.apply else 'DRY-RUN' + (' OFFLINE: bestaande database niet gecontroleerd' if args.offline else ''))
        Importer(api, args.apply).run(rows[:args.limit])
        return 0
    except (ImportFailure, OSError) as exc:
        print(f'FOUT: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
