"""Generate a QR without credentials; URL must be reachable by the scanning phone."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit
import qrcode

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, default=Path('data/b303-002-qr.png'))
    args = parser.parse_args()
    url = urlsplit(args.base_url)
    if url.scheme not in ('http', 'https') or not url.netloc or url.username or url.query or url.fragment:
        parser.error('Gebruik een basis-URL zonder credentials, query of fragment')
    qrcode.make(args.base_url.rstrip('/') + '/onderdeel/B303-002').save(args.output)
