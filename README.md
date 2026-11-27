# VIVES_voorraad
Project voor Netwerk Experience '26-'27

## QR-bestelautomatisering (FastAPI)

Zie [installatie, dry-run, QR-code en live-testprocedure](docs/qr_order_system.md).
De nieuwe app gebruikt IPN-URLs en vraagt expliciete bevestiging. Standaard wordt
niets naar InvenTree geschreven. De bestaande Flask-route wordt niet gebruikt.
Voor de voorbereide Docker/HTTPS-uitrol zonder bestaande services te herstarten:
[VM-dry-runprocedure](docs/qr_vm_dry_run.md).

## Documentatie

- [Onderdelencategorieën](docs/Parts.md): categorieën voor de voorraad, met reeds bekende modellen en specificaties.
- [Onderdeelfoto's](docs/screenshots/parts/): originele foto's met beschrijvende bestandsnamen.

## Projectstructuur

```text
VIVES_voorraad/
├── docker-compose.yml
├── Caddyfile
├── README.md
├── .env.example
├── requirements.txt
├── inventree_qr_api.py
├── data/
│   └── b303_components.csv
├── scripts/
│   └── import_components.py
├── tests/
│   └── test_import_components.py
└── docs/
    ├── Parts.md
    ├── component_verification.md
    └── screenshots/
        └── parts/
            ├── joy-it-jt-power-2-0-usb-voeding.png
            ├── antratek-hnp06-090-sonoff-voedingen.png
            └── stm32-nucleo-wb55rg-ontwikkelborden.png
```

## Componentenimport B303

Bij API-fouten toont het script de methode, het endpoint, de HTTP-status en een
vaste, veilige classificatie van de response. Met `--verbose` zie je ook geslaagde
requests. Headers, tokens, willekeurige querywaarden en ruwe response-inhoud
worden nooit gelogd. Alleen numerieke paginatieparameters worden zichtbaar gemaakt.
Een GET verstuurt geen body en geen Content-Type-header.

Ook InvenTree v2-tokens gebruiken `Authorization: Token <volledig token>`:
zet in `.env` alleen het volledige token, zonder `Token `-prefix. Het script kapt
tokens niet af en converteert ze niet. Zie de officiële
[authenticatiedocumentatie](https://docs.inventree.org/en/stable/api/).
Een HTTP 400 is op zichzelf onvoldoende om authenticatie, Host-validatie of
queryvalidatie als oorzaak aan te wijzen; gebruik de nieuwe foutmelding.

Python 3.10 of hoger. Er zijn geen externe Python-packages nodig.

```bash
python -m venv .venv
python -m pip install -r requirements.txt
```

Kopieer `.env.example` naar `.env` en vul `INVENTREE_SITE_URL` (VM-URL) en
`INVENTREE_API_TOKEN` in. Als `.env` al bestaat voor Docker, voeg alleen deze
twee variabelen toe: overschrijf de bestaande instellingen niet. Omgevingsvariabelen
hebben voorrang op `.env`. Gebruik HTTPS voor de tokenverbinding buiten een vertrouwd netwerk.

```bash
# Zonder VM: planning tegen een lege catalogus, geen databasecontrole
python scripts/import_components.py --offline --limit 2
# Met VM: alleen GET/OPTIONS, standaard geen wijzigingen
python scripts/import_components.py --limit 2
python scripts/import_components.py --limit 2 --verbose
# Alleen na expliciete toestemming voor databasewijzigingen:
python scripts/import_components.py --apply --limit 2
python scripts/import_components.py --apply
python -m unittest discover -s tests -v
```

Met `--csv pad/naar/catalogus.csv` kan een ander lokaal worden geïmporteerd.
De volledige CSV wordt gevalideerd vóór `--limit` wordt toegepast.
UTF-8, komma als scheidingsteken; SKU en IPN blijven tekst. De tien Parts
worden onder Electronics aangemaakt. Farnell is een actieve Company met
`is_supplier=true`; alleen geverifieerde SKU's krijgen een SupplierPart.
Fabrikant/MPN blijven in de CSV en in de SupplierPart-beschrijving bewaard.
Er worden geen aparte ManufacturerParts aangemaakt.

Identiteit: Part op IPN, categorie op naam + parent, Company op naam
(hoofdletterongevoelig), SupplierPart op supplier + SKU. Bestaande records worden
hergebruikt; dubbele matches of conflicterende koppelingen stoppen de import.
Het script werkt bestaande beschrijvingen en productgegevens niet automatisch bij.
Ontbrekende SKU's worden overgeslagen, terwijl de Part wel wordt aangemaakt.
Alle lijstpagina's worden opgehaald om duplicaten buiten de eerste pagina te vinden.

De importer verwacht API-versie 570 en controleert vóór de import de POST-velden
via OPTIONS. Het OpenAPI-schema van jullie installatie moet nog worden gecontroleerd
op `/api/schema/` (eventueel via de interactieve documentatie van de VM).
Deze checkout bevat geen VM-URL/token of geëxporteerd schema; een live dry-run
en exacte v570-verificatie zijn daarom nog niet uitgevoerd. De openbare
[InvenTree API-documentatie](https://docs.inventree.org/en/stable/api/schema/company/)
is als referentie gebruikt, niet als bewijs van de geïnstalleerde versie.

Voer één importer tegelijk uit. De API-aanroepen vormen geen database-transactie:
bij een fout kunnen eerdere records al bestaan. Herstarten hergebruikt die records.
POST-aanroepen worden niet automatisch opnieuw geprobeerd. Bij een timeout eerst
een dry-run uitvoeren om de serverstatus te controleren. Fouten geven exitcode 1.
Voorraad, Purchase Orders en bestellingen worden niet aangeraakt.

Voor toekomstige QR-automatisering: gebruik de IPN als stabiele externe sleutel,
zoek daarmee de Part en vervolgens supplier + SKU om de SupplierPart-PK te vinden.
Een lege SKU blokkeert bestellen. Een set jumperdraden telt als één cataloguseenheid;
het aantal draden in die set is niet de SupplierPart-pack_quantity.
De koppeling met Pending Purchase Orders valt buiten deze importer.

Bronnen, verpakking en openstaande gegevens: [verificatierapport](docs/component_verification.md).

## InvenTree-server (Docker)

De InvenTree-server zelf draait op de Ubuntu-VM (`10.20.225.77`) als Docker-stack
(zie `docker-compose.yml`). De componenten:

| Dienst             | Image                        | Functie                                   |
| ------------------ | ---------------------------- | ----------------------------------------- |
| `inventree-db`     | `postgres:16`                | Database                                  |
| `inventree-cache`  | `redis:7-alpine`             | Cache                                     |
| `inventree-server` | `inventree/inventree:stable` | Webserver (intern op poort 8000)          |
| `inventree-worker` | `inventree/inventree:stable` | Achtergrondtaken (`invoke worker`)        |
| `inventree-proxy`  | `caddy:2-alpine`             | Reverse proxy; publiceert poort **8000**  |

Poortstructuur: apparaten op het lokaal netwerk bereiken de server op
`http://10.20.225.77:8000`. Caddy publiceert hostpoort 8000 en stuurt verkeer
door naar de webserver (intern poort 8000); `/static/*` en `/media/*` worden
door Caddy rechtstreeks geserveerd (zie `Caddyfile`). De firewall van de VM
moet poort 8000 openzetten voor lokaal netwerkverkeer.

Opstarten op de VM:

```bash
docker compose up -d
docker compose ps
```

Alle persistente data (database, media, static) staat in het volume
`/opt/inventree/data` (`INVENTREE_EXT_VOLUME` in `.env`).

De Base URL in InvenTree (`INVENTREE_BASE_URL` onder Instellingen) volgt
automatisch `INVENTREE_SITE_URL` uit `.env` en is daardoor read-only. Om de
URL te veranderen: `INVENTREE_SITE_URL` aanpassen in `.env` en daarna
`docker compose up -d` draaien.

## QR-bestel-app (inventree_qr_api.py)

Naast de importer bevat dit project een kleine Flask-app (`inventree_qr_api.py`)
voor het bestellen via QR-code op het telefoon. De app luistert op
`0.0.0.0:5000`:

- `GET /bestel/<part_id>` — zoekt de SupplierPart van het onderdeel op en
  verhoogt het aantal van de bestaande regel met 1 op de openstaande
  bestellijst (PO), of voegt een nieuwe regel toe met aantal 1. Retourneert
  een compacte HTML-statusmelding voor het telefoonscherm.
- `GET /health` — snelle controle of de app draait.

De app leest dezelfde `INVENTREE_SITE_URL` en `INVENTREE_API_TOKEN` uit `.env`,
plus `OPEN_PO_ID` (id van de openstaande bestellijst, standaard `1`).
Aanroepen worden gelogd in `bestellingen.log`.

In tegenstelling tot de import-componenten (puur Python-standaardbibliotheek)
heeft deze app wél externe packages nodig. `requirements.txt` bevat die
niet; installeer ze handmatig:

```bash
python -m pip install flask requests python-dotenv
```

Draaien van de app:

```bash
python inventree_qr_api.py
```
