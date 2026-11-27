from datetime import datetime
import logging
from flask import Flask
import requests
import os
from dotenv import load_dotenv

app = Flask(__name__)

load_dotenv()

INVENTREE_URL = os.getenv("INVENTREE_SITE_URL")
API_TOKEN = os.getenv("INVENTREE_API_TOKEN")
OPEN_PO_ID = int(os.getenv("OPEN_PO_ID", 1))

HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# --- LOGGING INSTELLEN ---
logging.basicConfig(
    filename="bestellingen.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


def maak_melding(titel, bericht, kleur="#16a34a"):
    """Genereert een minimale, direct leesbare statusmelding voor op het telefoonscherm."""
    return f"""
    <!DOCTYPE html>
    <html lang="nl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{titel}</title>
    </head>
    <body style="font-family: sans-serif; text-align: center; padding: 40px 20px; background-color: #f8fafc;">
        <div style="background: white; padding: 30px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); max-width: 400px; margin: auto;">
            <h1 style="color: {kleur}; font-size: 32px; margin-bottom: 10px;">{titel}</h1>
            <p style="font-size: 18px; color: #334155; line-height: 1.5;">{bericht}</p>
        </div>
    </body>
    </html>
    """


@app.route("/bestel/<int:part_id>", methods=["GET"])
def bestel_onderdeel(part_id):
    try:
        # STAP 1: Haal de leveranciers-koppeling (SupplierPart) van dit onderdeel op
        sp_url = f"{INVENTREE_URL}/api/company/part/?part={part_id}"
        sp_resp = requests.get(sp_url, headers=HEADERS, timeout=5)
        sp_resp.raise_for_status()
        supplier_parts = sp_resp.json()

        # InvenTree kan een lijst teruggeven of een dict met 'results' (afhankelijk van paginering)
        if isinstance(supplier_parts, dict) and "results" in supplier_parts:
            supplier_parts = supplier_parts["results"]

        if not supplier_parts:
            logging.warning(
                f"Scan mislukt: Geen leverancier gekoppeld aan Part ID {part_id}"
            )
            return (
                maak_melding(
                    "Geen leverancier",
                    f"Onderdeel ID {part_id} heeft nog geen leverancier in InvenTree.",
                    kleur="#dc2626",
                ),
                400,
            )

        supplier_part = supplier_parts[0]
        supplier_part_id = supplier_part["pk"]
        part_name = supplier_part.get("part_detail", {}).get(
            "name", f"Onderdeel #{part_id}"
        )

        # STAP 2: Controleer of dit artikel al op de openstaande bestellijst (PO) staat
        po_lines_url = f"{INVENTREE_URL}/api/order/po-line/?order={OPEN_PO_ID}&part={supplier_part_id}"
        lines_resp = requests.get(po_lines_url, headers=HEADERS, timeout=5)
        lines_resp.raise_for_status()
        existing_lines = lines_resp.json()

        if isinstance(existing_lines, dict) and "results" in existing_lines:
            existing_lines = existing_lines["results"]

        # STAP 3A: Artikel staat al op de lijst -> Aantal verhogen met 1 (PATCH)
        if existing_lines:
            line = existing_lines[0]
            line_id = line["pk"]
            huidig_aantal = float(line["quantity"])
            nieuw_aantal = huidig_aantal + 1

            patch_url = f"{INVENTREE_URL}/api/order/po-line/{line_id}/"
            patch_resp = requests.patch(
                patch_url,
                json={"quantity": nieuw_aantal},
                headers=HEADERS,
                timeout=5,
            )
            patch_resp.raise_for_status()

            logging.info(
                f"Aantal verhoogd: {part_name} (Part {part_id}) -> nieuw aantal: {int(nieuw_aantal)}"
            )
            return (
                maak_melding(
                    "✔ Aantal verhoogd",
                    f"<strong>{part_name}</strong><br>Nieuw aantal op bestellijst: <strong>{int(nieuw_aantal)}</strong>",
                ),
                200,
            )

        # STAP 3B: Artikel staat nog niet op de lijst -> Nieuwe regel toevoegen (POST)
        else:
            post_url = f"{INVENTREE_URL}/api/order/po-line/"
            payload = {
                "order": OPEN_PO_ID,
                "part": supplier_part_id,
                "quantity": 1,
            }
            post_resp = requests.post(
                post_url, json=payload, headers=HEADERS, timeout=5
            )
            post_resp.raise_for_status()

            logging.info(
                f"Toegevoegd aan bestellijst: {part_name} (Part {part_id}) -> aantal: 1"
            )
            return (
                maak_melding(
                    "✔ Toegevoegd",
                    f"<strong>{part_name}</strong><br>Aantal op bestellijst: <strong>1</strong>",
                ),
                201,
            )

    except requests.exceptions.RequestException as e:
        logging.error(f"API/Verbindingsfout bij Part ID {part_id}: {str(e)}")
        return (
            maak_melding(
                "Verbindingsfout",
                f"Kon geen verbinding maken met InvenTree API.<br><small>{str(e)}</small>",
                kleur="#dc2626",
            ),
            500,
        )


@app.route("/health", methods=["GET"])
def health_check():
    """Snelle test-URL om te controleren of het script draait."""
    return {"status": "online", "inventree_url": INVENTREE_URL}, 200


if __name__ == "__main__":
    # Luister op alle netwerkinterfaces (0.0.0.0) op poort 5000
    app.run(host="0.0.0.0", port=5000)