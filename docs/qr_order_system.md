# QR-aanvragen B303

Deze afzonderlijke FastAPI-app vervangt voor nieuwe QR-codes de oude Flask-flow.
De bestaande `inventree_qr_api.py`, Docker Compose en Caddyfile zijn niet gewijzigd.
**De oude `/bestel/<id>`-route schrijft bij GET. Publiceer die niet voor deze flow.**

## Lokaal starten

```bash
python -m pip install -r requirements-qr.txt
python -m unittest discover -s tests -v
python -m uvicorn qr_order.app:create_app --factory --host 127.0.0.1 --port 8080 --no-access-log
```

Vul in de bestaande root `.env` `INVENTREE_SITE_URL`, `INVENTREE_API_TOKEN`,
`OPEN_PO_ID=1`, `QR_USERNAME`, `QR_PASSWORD`, `QR_DRY_RUN=true` en `QR_QUANTITY=10`
in. Bestaande procesvariabelen hebben voorrang. Het InvenTree-token blijft uitsluitend
op de server. Kies een sterk leerkrachtwachtwoord. HTTP Basic beschermt alle pagina's
en acties; gebruik HTTPS via de VM-reverseproxy vóór toegang vanaf telefoons.
Geen CORS inschakelen. De POST vereist een extra bevestigingsheader tegen CSRF.

Ga naar `/onderdeel/B303-002`, meld aan en bevestig. In dry-run worden alleen GETs
naar InvenTree gestuurd; er verschijnt een expliciete testmelding.
Scannen voert nooit een POST uit. Hoeveelheid komt uitsluitend uit serverconfiguratie.
De testhoeveelheid 10 is afgestemd op het bekende minimum/veelvoud van SKU 9339051;
controleer bestelvoorwaarden voor andere artikelen vóór live gebruik.

## QR-code

`data/b303-002-qr.png` verwijst naar
`http://localhost:8080/onderdeel/B303-002` voor de lokale demonstratie.
Voor een telefoon moet de definitieve bereikbare HTTPS-app-URL worden ingevuld:

```bash
python scripts/generate_qr.py --base-url https://jullie-qr-host.example.org
```

Dit is de URL van de FastAPI-app, niet de InvenTree-URL. Geen credentials in QR-codes.

## OpenAPI v570 en eerste live test

De exacte installatiespecificatie is in deze checkout niet beschikbaar. Er is dus
nog geen bewijs van een v570-contractvergelijking. De implementatie gebruikt de
[officiële order-API-documentatie](https://docs.inventree.org/en/latest/api/schema/order/)
als voorlopige referentie: PO-detail `order/po/{id}/`, regels `order/po-line/`,
`part` op de bestelregel is de **SupplierPart-PK**, Pending heeft statuscode 10.
Live mode vereist runtime API-versie 570 en een geëxporteerd JSON OpenAPI-schema
via `QR_OPENAPI_FILE` (absoluut pad). Het script controleert GET-endpoints en
schrijfvelden `order`, `part`, `quantity`, `merge_items` in dat schema. Afwijkingen
blokkeren schrijven. Review ook Pending-statuscode 10 tegen jullie installatieschema.

Nog nodig voor de eerste live test:

1. Exporteer/review het echte v570-schema en configureer `QR_OPENAPI_FILE`.
2. Host de app achter HTTPS; stel credentials en een permanent lokaal SQLite-pad in
   met `QR_DATABASE`. Mount deze directory persistent bij eventuele containerhosting.
3. Voer met `QR_DRY_RUN=true` een scan en bevestiging uit voor B303-002. Controleer
   naam, SKU 9339051, PO-0001, Farnell, Pending en bestemming B303 in InvenTree.
4. Vraag expliciete toestemming voordat `QR_DRY_RUN=false` wordt ingesteld.
5. Bevestig één keer en controleer handmatig dat er hoogstens één nieuwe regel is.
   Bestaande regels worden nooit verhoogd. De PO-status verandert niet.

## Gelijktijdigheid en herstel

Een unieke SQLite-sleutel `(PO, Part)` reserveert een aanvraag vóór de POST en werkt
over meerdere Uvicorn-processen met dezelfde lokale database. Een timeout houdt de
reservering vast: controleer InvenTree handmatig vóór een beheerder die vrijgeeft.
Verwijder het register niet bij herstarten. Dry-runs maken geen reservering.
Gebruik één gedeelde lokale SQLite-database voor alle workers; geen replicas met
afzonderlijke databases en geen SQLite op een netwerkschijf.

Een extern proces of handmatige gebruiker kan buiten dit register tegelijk de PO
wijzigen. InvenTree biedt hier geen door deze app bewezen atomair compare-and-create
contract. Spreek daarom af dat deze app de enige schrijver voor QR-aanvragen is.
Ook de Pending-status kan extern veranderen tussen controle en POST; de app controleert
opnieuw vlak vóór schrijven en verandert zelf nooit de status. Voor harde garanties
over andere schrijvers is een server-side InvenTree-transactie/plugin nodig.

Logs bevatten uitsluitend vaste foutteksten, geen credentials of API-response-inhoud.
Gebruik `--no-access-log` om IPN's buiten accesslogs te houden. Voor grotere uitrol:
individuele accounts/Authentik, rate limiting en afspraken over registerbeheer toevoegen.
Er is geen externe Farnell-bestelling of automatische PO-statusovergang geïmplementeerd.
