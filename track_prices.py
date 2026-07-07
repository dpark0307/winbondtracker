#!/usr/bin/env python3
"""
Winbond vs Competitor Flash Price Tracker
--------------------------------------------
Pulls current DigiKey pricing for a basket of NOR/NAND flash parts across
four manufacturers (Winbond, Macronix, GigaDevice, ISSI) so you can track
the competitive price gap over time, not just Winbond's own pricing.

Each part is matched to be the closest same-density, same-interface
equivalent across manufacturers (see PARTS below). A few competitor
slots are intentionally left as None where no confirmed, in-stock
DigiKey SKU could be found yet.

SETUP (one-time):
1. Set these two environment variables on your machine:
     DIGIKEY_CLIENT_ID
     DIGIKEY_CLIENT_SECRET
2. Install the one dependency this script needs:
     pip install requests
   (or: py -m pip install requests)

USAGE:
    python track_prices.py
    (or: py track_prices.py)

Each run appends one row per part to prices.csv in the same folder.
"""

import os
import sys
import csv
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------
# CONFIG — basket of parts, grouped by density/type, tagged by manufacturer
# Edit freely: add/remove rows, or fill in a competitor part number where
# a manufacturer entry is currently None.
# ---------------------------------------------------------------------
PARTS = [
    # density,  type,   manufacturer,   part_number
    ("128Mb",   "NOR",  "Winbond",      "W25Q128JVSIQ"),
    ("128Mb",   "NOR",  "Macronix",     "MX25L12833FZ2I-10G"),
    ("128Mb",   "NOR",  "GigaDevice",   "GD25Q128ESIGR"),
    ("128Mb",   "NOR",  "ISSI",         "IS25LP128-JBLE"),

    ("64Mb",    "NOR",  "Winbond",      "W25Q64JVSSIQ"),
    ("64Mb",    "NOR",  "Macronix",     "MX25L6433FM2I-08G"),
    ("64Mb",    "NOR",  "GigaDevice",   "GD25Q64CSIG"),
    ("64Mb",    "NOR",  "ISSI",         "IS25LP064A-JMLE"),

    ("32Mb",    "NOR",  "Winbond",      "W25Q32JVSSIQ"),
    ("32Mb",    "NOR",  "Macronix",     "MX25L3233FM2I-08G"),
    ("32Mb",    "NOR",  "GigaDevice",   "GD25Q32ESIGR"),
    ("32Mb",    "NOR",  "ISSI",         "IS25LP032D-JNLE"),

    ("1Gb",     "NAND", "Winbond",      "W25N01GVZEIG"),
    ("1Gb",     "NAND", "Macronix",     "MX35LF1GE4AB-Z4I"),
    ("1Gb",     "NAND", "GigaDevice",   None),  # no confirmed in-stock SKU found yet
    ("1Gb",     "NAND", "ISSI",         "IS37SML01G1-LLI"),  # frequently out of stock

    ("2Gb",     "NAND", "Winbond",      "W25N02KVZEIR"),  # currently out of stock on DigiKey
    ("2Gb",     "NAND", "Macronix",     "MX35LF2GE4AD-Z4I"),
    ("2Gb",     "NAND", "GigaDevice",   None),
    ("2Gb",     "NAND", "ISSI",         None),
]

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prices.csv")

DIGIKEY_TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
DIGIKEY_KEYWORD_SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"

# ---------------------------------------------------------------------
# AUTH — client_credentials (2-legged) flow, no browser login needed
# ---------------------------------------------------------------------
def get_access_token(client_id, client_secret):
    resp = requests.post(
        DIGIKEY_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_price(part_number, access_token, client_id):
    """
    IMPORTANT: DigiKey can carry MULTIPLE SEPARATE product listings for
    the exact same manufacturer part number (not just packaging
    variations within one listing) - e.g. a "normally stocked" listing
    and a "not normally stocked" premium listing. Going straight to a
    single product's /productdetails endpoint only sees ONE of these
    listings, which can silently be the more expensive one.

    Fix: use DigiKey's keyword SEARCH endpoint instead, which returns
    every matching listing across DigiKey's catalog for this part
    number. We keep only results whose manufacturer part number
    matches exactly (case-insensitive), then take the lowest
    single-unit price across every variation of every matching listing.
    This should reliably land on the same price a human would see
    searching digikey.com directly.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-DIGIKEY-Client-Id": client_id,
        "X-DIGIKEY-Locale-Site": "US",
        "X-DIGIKEY-Locale-Language": "en",
        "X-DIGIKEY-Locale-Currency": "USD",
        "Content-Type": "application/json",
    }
    body = {
        "Keywords": part_number,
        "Limit": 10,
        "Offset": 0,
    }
    resp = requests.post(DIGIKEY_KEYWORD_SEARCH_URL, headers=headers, json=body, timeout=15)

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    products = data.get("Products") or data.get("products") or []
    if not products:
        return None, "No products found for this part number"

    target = part_number.strip().upper()
    candidate_prices = []

    for product in products:
        mpn = (product.get("ManufacturerProductNumber") or "").strip().upper()
        if mpn != target:
            continue  # skip unrelated parts the keyword search also returned

        variations = product.get("ProductVariations") or []
        sources = variations if variations else [product]

        for source in sources:
            price_breaks = source.get("StandardPricing")
            if not price_breaks:
                continue
            unit_price = price_breaks[0].get("UnitPrice")
            if unit_price is not None:
                candidate_prices.append(float(unit_price))

    if not candidate_prices:
        return None, "No pricing found for an exact part number match (likely out of stock)"

    return min(candidate_prices), None


# ---------------------------------------------------------------------
# STORAGE — append to a plain CSV, one row per part per run
# ---------------------------------------------------------------------
FIELDNAMES = ["date", "density", "type", "manufacturer", "part_number", "price_usd", "pct_change_vs_last_pull"]


def load_last_prices():
    """
    Reads prices.csv to find each part's most recent saved price.
    Skips any malformed/incomplete rows instead of crashing the whole
    script - this can happen if a previous run was interrupted mid-write
    (e.g. OneDrive syncing the file at the same moment), leaving a
    partial last line.
    """
    last = {}
    if not os.path.exists(CSV_PATH):
        return last
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            part = row.get("part_number")
            price = row.get("price_usd")
            if not part or price is None or price == "":
                continue
            try:
                last[part] = float(price)
            except ValueError:
                continue
    return last


def append_rows(rows):
    file_exists = os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------
def main():
    client_id = os.environ.get("DIGIKEY_CLIENT_ID")
    client_secret = os.environ.get("DIGIKEY_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("ERROR: DIGIKEY_CLIENT_ID and/or DIGIKEY_CLIENT_SECRET are not set.")
        print("Set them as environment variables before running this script.")
        sys.exit(1)

    print("Getting access token...")
    try:
        token = get_access_token(client_id, client_secret)
    except requests.RequestException as e:
        print(f"ERROR: Failed to get access token: {e}")
        sys.exit(1)

    last_prices = load_last_prices()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows_to_save = []

    active_parts = [p for p in PARTS if p[3] is not None]
    print(f"\nPulling prices for {len(active_parts)} part(s) on {today}:\n")

    current_group = None
    for density, ptype, manufacturer, part in PARTS:
        if part is None:
            print(f"  [{density} {ptype}] {manufacturer}: SKIPPED (no confirmed part number set)")
            continue

        group = (density, ptype)
        if group != current_group:
            print(f"\n  -- {density} {ptype} --")
            current_group = group

        price, error = fetch_price(part, token, client_id)

        if error:
            print(f"    {manufacturer:<12} {part:<22} FAILED - {error}")
            continue

        prev = last_prices.get(part)
        pct_change_value = ""
        if prev is not None and prev != 0:
            pct_change = ((price - prev) / prev) * 100
            pct_change_value = round(pct_change, 2)
            flag = "  <-- FLAGGED (>3% move)" if abs(pct_change) >= 3 else ""
            print(f"    {manufacturer:<12} {part:<22} ${price:.4f}  ({pct_change:+.2f}% vs last pull){flag}")
        else:
            print(f"    {manufacturer:<12} {part:<22} ${price:.4f}  (first entry)")

        rows_to_save.append({
            "date": today,
            "density": density,
            "type": ptype,
            "manufacturer": manufacturer,
            "part_number": part,
            "price_usd": price,
            "pct_change_vs_last_pull": pct_change_value,
        })

    if rows_to_save:
        append_rows(rows_to_save)
        print(f"\nSaved {len(rows_to_save)} row(s) to {CSV_PATH}")
    else:
        print("\nNo prices were successfully pulled - nothing saved.")


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------
# README - one-time environment variable setup
# ---------------------------------------------------------------------
# Windows (PowerShell), run once, then RESTART your terminal:
#   setx DIGIKEY_CLIENT_ID "your-client-id-here"
#   setx DIGIKEY_CLIENT_SECRET "your-client-secret-here"
#
# Then each day, just run:
#   py track_prices.py
# ---------------------------------------------------------------------
