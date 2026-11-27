# Voorbereiding VM-dry-run (nog niet uitgerold)

De twee nieuwe services zijn opt-in met profiel `qr-dry-run`. Alleen deze services
worden gestart; InvenTree, PostgreSQL, Authentik en hun bestaande proxy blijven zoals ze zijn.
De bestaande `Caddyfile` is ongewijzigd. `deploy/Caddyfile.qr` hoort uitsluitend bij
de nieuwe `qr-proxy` en gebruikt TCP 8443. Geen HTTP-poort wordt gepubliceerd.

| Dienst | Bereikbaar | Gedrag |
|---|---|---|
| InvenTree | http://10.20.225.77:8000 | Bestaande toegang, ongewijzigd |
| QR-proxy | https://10.20.225.77:8443 | TLS met interne CA |
| QR-web | Alleen Docker-netwerk, 8080 | FastAPI, vaste dry-run |
| InvenTree vanuit QR-web | http://inventree-server:8000/api/ | Alleen GET; intern Docker-netwerk |

De QR-code `data/b303-002-qr.png` bevat
`https://10.20.225.77:8443/onderdeel/B303-002`. Dit gebruikt het opgegeven echte
VM-IP; bereikbaarheid vanaf VIVES-wifi en de firewall zijn nog niet bevestigd.
Verander bij een ander IP `QR_BIND_IP` en genereer de QR opnieuw.

## HTTPS op het VIVES-netwerk

Een privaat IP krijgt hier geen publiek ACME-certificaat. `tls internal` laat Caddy
een eigen CA en IP-certificaat maken. Installeer de CA via een door VIVES-IT beheerde
vertrouwde route op de testtelefoon (op iOS ook volledige certificaatvertrouwensinstelling).
Controleer de SHA-256-fingerprint via een onafhankelijk vertrouwd kanaal.
Omzeil certificaatwaarschuwingen niet en gebruik geen `curl -k`.
Alleen het publieke `root.crt` mag worden gedeeld, nooit CA-private keys of `/data`.

VIVES-IT moet bevestigen dat de wifi/VLAN-route naar 10.20.225.77 TCP 8443 bestaat.
Beperk de VM-firewall tot de bedoelde VIVES-subnetten; geen internet-portforwarding
of publieke tunnel. Subnetten zijn niet aangeleverd, dus er zijn geen firewallregels
verzonnen of toegepast. Voor uitrol naar meer telefoons verdient een VIVES-DNS-naam
met organisatiecertificaat/CA de voorkeur. Die naam en certificaten vereisen IT-afstemming.
Zie [Caddy local HTTPS](https://caddyserver.com/docs/automatic-https#local-https).

## Startprocedure na expliciete toestemming voor uitrol op de VM

Voer uit in deze checkout op branch `feat/qr-order-system`, vanuit de repo-root.
Voeg `INVENTREE_API_TOKEN`, `QR_USERNAME`, `QR_PASSWORD` en `QR_BIND_IP=10.20.225.77`
toe aan de bestaande root `.env` zonder Docker/Authentik-waarden te overschrijven.
Gebruik een read-only InvenTree-token voor deze dry-run. `chmod 600 .env` op Ubuntu.
Geen secrets in shellargumenten, QR, Git of uitvoer van `docker compose config`.
Compose geeft uitsluitend de drie bedoelde credentials door aan QR-web, geen databasewachtwoorden.

```bash
docker compose --profile qr-dry-run config --quiet
docker compose --profile qr-dry-run build qr-web
# Alleen nieuwe services: --no-deps voorkomt wijzigingen aan bestaande services.
docker compose --profile qr-dry-run up -d --no-deps qr-web
docker compose --profile qr-dry-run up -d --no-deps qr-proxy
docker compose --profile qr-dry-run ps qr-web qr-proxy
docker compose --profile qr-dry-run logs --tail 30 qr-web qr-proxy
```

Wacht op gezonde QR-web voordat QR-proxy gestart wordt. Root `.env` kan schrijven
niet inschakelen: `QR_DRY_RUN: "true"` staat vast in Compose en de API-client weigert
POST/PATCH/PUT/DELETE. De app-POST met bevestiging voert uitsluitend InvenTree-GETs uit.

Exporteer de publieke CA en controleer de fingerprint:

```bash
docker compose --profile qr-dry-run cp qr-proxy:/data/caddy/pki/authorities/local/root.crt /tmp/vives-qr-root.crt
openssl x509 -in /tmp/vives-qr-root.crt -noout -fingerprint -sha256
```

Stuur dit bestand alleen via een vertrouwde beheerde route naar de telefoon. Daarna:

```bash
curl --cacert /tmp/vives-qr-root.crt https://10.20.225.77:8443/healthz
docker compose --profile qr-dry-run exec -T qr-web python -m scripts.qr_vm_check
```

`qr_vm_check` haalt met GET het installatieschema op, controleert het bestaande
OpenAPI-contract, API v570, IPN/SKU en PO, voert een interne dry-run uit en vergelijkt
PO/regel-snapshots vóór/na. Alleen schema-opslag in het eigen QR-state-volume wordt
geschreven. De controle faalt veilig bij afwijkingen. Het exportpad/schema-contract
kan op de echte installatie nog aanpassing nodig hebben; dat is hier niet bewezen.
Een ongewijzigde snapshot bewijst alleen de betrokken PO en regels, geen volledige DB-audit.

Scan nu de QR op de vertrouwde telefoon, meld aan, controleer Resistor 1K/SKU 9339051,
PO-0001 en druk op bevestigen. Verwacht dry-runmelding of 'staat al op de bestellijst'.
Herhaal `qr_vm_check` en controleer PO-status/regels handmatig in InvenTree.
Er hoort geen bestelregel, aantalsverhoging, PO-statuswijziging of Farnell-order te ontstaan.

## Health, logging en stoppen

QR-web `/healthz` is een openbare liveness-check zonder credentials of InvenTree-data;
dit is geen bewijs van API-bereikbaarheid. QR-proxy controleert de lokale Caddy-admin-API
binnen zijn eigen container. Die adminpoort is niet gepubliceerd. Logs gaan naar stdout/
stderr met rotatie (3 x 5 MB); accesslogs staan uit. Succesvolle dry-run logt alleen
een vaste tekst. SQLite en CA hebben afzonderlijke persistente volumes.

```bash
docker compose --profile qr-dry-run stop qr-web qr-proxy
```

Gebruik geen `docker compose down` of `down -v`: dat kan bestaande services raken.
Alle configuratie is voorbereid in Git; op de VM is niets gestart, herstart of gewijzigd.

## Lokale verificatie en beperkingen

De image is gebouwd en als non-root met read-only filesystem zonder netwerk getest.
Caddy valideert de nieuwe configuratie. Compose is geparst met tijdelijke testwaarden
en een lege env_file: alle bestaande service-definities zijn identiek aan de vorige
commit. De lokale root `.env` bevat standalone labels die Docker Compose niet accepteert;
die is niet gewijzigd. Op de VM moeten labels commentaar zijn (`# ...`) en variabelen
gewone `NAAM=waarde`-regels. Voer daar `config --quiet` uit vóór starten.
De echte v570-specificatie en smartphone/VM-keten zijn hier nog niet geverifieerd:
de VM is niet bereikbaar en er is geen lokaal API-token of schema beschikbaar.
