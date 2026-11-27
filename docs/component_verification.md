# Farnell-verificatie — 27 november 2026

Gegevens zijn opgehaald uit de door de webzoektool ontsloten Farnell België-
productpagina's. Rechtstreeks openen gaf voor verschillende pagina's een cachefout;
dit is geen bevestiging van actuele beschikbaarheid of prijs. Geen bestelling gedaan.
De product-URL per geverifieerd artikel staat in `data/b303_components.csv`.

| IPN | SKU | Fabrikant / MPN | Bevestigde kenmerken | Eenheid / minimum / veelvoud |
|---|---|---|---|---|
| B303-001 | 9339299 | MULTICOMP PRO / MCF 0.25W 220R | 220 ohm, 250 mW, 5%, axiaal | Each / 10 / 10 |
| B303-002 | 9339051 | MULTICOMP PRO / MCF 0.25W 1K | 1 kohm, 250 mW, 5%, axiaal | Each / 10 / 10 |
| B303-003 | 9339060 | MULTICOMP PRO / MCF 0.25W 10K | 10 kohm, 250 mW, 5%, axiaal | Each / 10 / 10 |
| B303-004 | 1703505 | VISHAY / TLHR6400 | rood, 5 mm, THT, 20 mA, 2 V, 625 nm | Each / 5 / 5 |
| B303-005 | 2764981 | WURTH ELEKTRONIK / 151054GS03000 | groen, 5 mm, THT, 20 mA, 3.4 V, 522 nm | Each / 5 / 5 |
| B303-006 | 1712769 | VCC / VAOL-5GCE4 | geel, 5 mm, THT, 20 mA, 2 V, 590 nm | Each / 1 / nog controleren |
| B303-007 | leeg | kandidaat ADAFRUIT / 759, SKU 2816394 | categoriepagina noemt 40 M-M-draden van 75 mm, 28 AWG | productpagina nog bevestigen |
| B303-008 | 2816399 | ADAFRUIT / 826 | set van 40 M-F-draden, 150 mm, 28 AWG | Each (set) / 1 / 1 |
| B303-009 | 3222033 | MOLEX / 90120-0800 | 2.54 mm, 1 rij, 40 contacten, rechte THT-header | Each (headerstrip) / 1 / 1 |
| B303-010 | 4457326 | DFROBOT / FIT0096-C | 400 contacten, 82 x 62 mm, 2 voedingsrails | Each / 1 / 1 |

`pack_quantity=1` volgt uit verkoop per Each, uitgedrukt in cataloguseenheden.
Minimum en bestelveelvoud zijn afzonderlijke bestelvoorwaarden; 10 weerstanden
minimum betekent geen pack_quantity van 10. De toekomstige bestelbackend moet
deze voorwaarden opnieuw controleren. Voor de gele LED is het veelvoud nog open;
dit verandert de bevestigde verkoopeenheid Each niet.

Aanvullende bronnen voor openstaande of aangevulde productgegevens:

- [ADAFRUIT-catalogus bij Farnell](https://be.farnell.com/c/tools-production-supplies/prototyping-tools-breadboards/jumper-wire-assortments?brand=adafruit): kandidaat 759 / 2816394. SKU blijft leeg omdat alleen de categoriepagina betrouwbaar kon worden opgehaald.
- [VCC-catalogus bij Farnell](https://be.farnell.com/en-BE/c/optoelectronics-displays/led-products/standard-single-colour-leds-under-75ma?brand=vcc-visual-communications-company): bevestigt elektrische specificaties, THT en Each van VAOL-5GCE4 aanvullend op de productpagina.

Alle tien interne Parts zijn voorbereid; negen SupplierParts zijn voorbereid.
Ontbreekt nog: productpaginaverificatie voor B303-007, het live OpenAPI-schema
v570, VM-URL/token en een online dry-run. Geen live import uitgevoerd.
