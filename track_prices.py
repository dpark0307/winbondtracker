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
    ("128Mb",   "NOR",  "Macronix",     "MX25L12833FM2I-10G"),
    ("128Mb",   "NOR",  "GigaDevice",   "GD25Q128ESIGR"),
    ("128Mb",   "NOR",  "ISSI",         "IS25LP128-JBLE"),

    ("64Mb",    "NOR",  "Winbond",      "W25Q64JVSSIQ"),
    ("64Mb",    "NOR",  "Macronix",     "MX25L6433FM2I-08G"),
    ("64Mb",    "NOR",  "GigaDevice",   "GD25Q64ESIGR"),
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
MOUSER_SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"

# ---------------------------------------------------------------------
# AUTH — DigiKey uses client_credentials (2-legged OAuth), no browser
# login needed. Mouser uses a simple API key passed as a query param -
# no token exchange required.
# ---------------------------------------------------------------------
def get_digikey_access_token(client_id, client_secret):
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


def _normalize_part(s):
    """Lowercase and strip spaces/hyphens so trivial formatting
    differences (e.g. a stray space or hyphen) don't cause a real
    matching listing to be excluded."""
    return (s or "").strip().upper().replace("-", "").replace(" ", "")


def _qty1_price(price_breaks):
    """Given a list of price breaks, return the price specifically at
    BreakQuantity == 1, not just whatever happens to be listed first -
    the first entry is not reliably the qty-1 price."""
    if not price_breaks:
        return None
    qty1 = [pb for pb in price_breaks if pb.get("BreakQuantity") == 1]
    if qty1:
        return qty1[0].get("UnitPrice")
    # Fallback: no explicit qty-1 break found, use the lowest-quantity
    # break available (sorted ascending by quantity) rather than index 0.
    sorted_breaks = sorted(price_breaks, key=lambda pb: pb.get("BreakQuantity", 0))
    return sorted_breaks[0].get("UnitPrice") if sorted_breaks else None


def fetch_digikey_price(part_number, access_token, client_id):
    """
    Two bugs fixed here after finding real, confirmed pricing errors
    (e.g. W25Q128JVSIQ showing $3.97 when the live DigiKey page showed
    $1.79 at qty 1):

    1. Exact string matching on ManufacturerProductNumber was too
       strict - any trivial formatting difference from DigiKey's API
       (stray space, hyphen) silently excluded the correct standard
       listing, leaving only pricier oddball listings as candidates.
       Fixed by normalizing both sides (strip spaces/hyphens, uppercase)
       before comparing.

    2. price_breaks[0] assumed the first price break was the qty-1
       price. Not guaranteed. Fixed by explicitly finding the break
       where BreakQuantity == 1.
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

    target = _normalize_part(part_number)
    candidate_prices = []

    for product in products:
        mpn = _normalize_part(product.get("ManufacturerProductNumber"))
        if mpn != target:
            continue  # skip genuinely unrelated parts the keyword search also returned

        variations = product.get("ProductVariations") or []
        sources = variations if variations else [product]

        for source in sources:
            price = _qty1_price(source.get("StandardPricing"))
            if price is not None:
                candidate_prices.append(float(price))

    if not candidate_prices:
        return None, "No pricing found for an exact part number match (likely out of stock)"

    return min(candidate_prices), None


def fetch_mouser_price(part_number, api_key):
    """
    Mouser's Search API takes a manufacturer/Mouser part number and
    returns matching parts with their price breaks. Like DigiKey, a
    search can return multiple matching listings, so we filter to
    exact part number matches and take the lowest qty-1 price across
    all of them.
    """
    url = f"{MOUSER_SEARCH_URL}?apiKey={api_key}"
    body = {
        "SearchByPartRequest": {
            "mouserPartNumber": part_number,
            "partSearchOptions": "string",
        }
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=body, timeout=15)

    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    errors = data.get("Errors") or []
    if errors:
        return None, f"Mouser API error: {errors[0].get('Message', 'unknown error')}"

    results = data.get("SearchResults") or {}
    parts = results.get("Parts") or []
    if not parts:
        return None, "No products found for this part number"

    target = part_number.strip().upper()
    candidate_prices = []

    for part in parts:
        mfr_pn = (part.get("ManufacturerPartNumber") or "").strip().upper()
        if mfr_pn != target:
            continue

        price_breaks = part.get("PriceBreaks") or []
        for pb in price_breaks:
            price_str = pb.get("Price", "")
            # Mouser returns price as a string like "$1.2300" - strip
            # the currency symbol and any commas before converting.
            cleaned = price_str.replace("$", "").replace(",", "").strip()
            if cleaned:
                try:
                    candidate_prices.append(float(cleaned))
                except ValueError:
                    continue

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
    script.
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
    digikey_id = os.environ.get("DIGIKEY_CLIENT_ID")
    digikey_secret = os.environ.get("DIGIKEY_CLIENT_SECRET")

    if not digikey_id or not digikey_secret:
        print("ERROR: DIGIKEY_CLIENT_ID and/or DIGIKEY_CLIENT_SECRET are not set.")
        sys.exit(1)

    print("Getting DigiKey access token...")
    try:
        digikey_token = get_digikey_access_token(digikey_id, digikey_secret)
    except requests.RequestException as e:
        print(f"ERROR: Failed to get DigiKey access token: {e}")
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

        price, error = fetch_digikey_price(part, digikey_token, digikey_id)

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
