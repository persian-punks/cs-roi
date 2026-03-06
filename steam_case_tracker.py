#!/usr/bin/env python3
"""Fetch lifetime Steam Market price history for CS2 weapon cases.

Tracks popular cases for investment analysis.  Data is saved to
data/case_price_history.json and displayed in the main dashboard.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ── Config ──────────────────────────────────────────────────────────────────
CASE_DATA_FILE = "data/case_price_history.json"
APP_ID = 730  # CS2

PRICE_HISTORY_URL = "https://steamcommunity.com/market/pricehistory/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

REQUEST_DELAY = 5  # seconds between requests

# ── Case list ───────────────────────────────────────────────────────────────
# Each entry: (market_hash_name, drop_status)
#   drop_status: "discontinued" = no longer drops, tends to appreciate
#                "rare"         = rare drop pool, slow supply
#                "active"       = currently drops in-game
CASES = [
    # Discontinued / rare — strong investment history
    ("Operation Bravo Case",                "discontinued"),
    ("CS:GO Weapon Case",                   "discontinued"),
    ("CS:GO Weapon Case 2",                 "discontinued"),
    ("CS:GO Weapon Case 3",                 "discontinued"),
    ("eSports 2013 Case",                   "discontinued"),
    ("eSports 2013 Winter Case",            "discontinued"),
    ("eSports 2014 Summer Case",            "discontinued"),
    ("Operation Phoenix Weapon Case",       "discontinued"),
    ("Operation Breakout Weapon Case",      "discontinued"),
    ("Operation Vanguard Weapon Case",      "discontinued"),
    ("Huntsman Weapon Case",                "discontinued"),
    ("Winter Offensive Weapon Case",        "discontinued"),
    ("Chroma Case",                         "rare"),
    ("Chroma 2 Case",                       "rare"),
    ("Chroma 3 Case",                       "rare"),
    ("Falchion Case",                       "rare"),
    ("Shadow Case",                         "rare"),
    ("Revolver Case",                       "rare"),
    ("Wildfire Case",                       "rare"),
    ("Gamma Case",                          "rare"),
    ("Gamma 2 Case",                        "rare"),
    ("Glove Case",                          "rare"),
    ("Spectrum Case",                       "rare"),
    ("Spectrum 2 Case",                     "rare"),

    # Modern cases — active drops
    ("Clutch Case",                         "active"),
    ("Horizon Case",                        "active"),
    ("Danger Zone Case",                    "active"),
    ("Prisma Case",                         "active"),
    ("Prisma 2 Case",                       "active"),
    ("CS20 Case",                           "active"),
    ("Shattered Web Case",                  "active"),
    ("Operation Broken Fang Case",          "active"),
    ("Snakebite Case",                      "active"),
    ("Operation Riptide Case",              "active"),
    ("Dreams & Nightmares Case",            "active"),
    ("Recoil Case",                         "active"),
    ("Revolution Case",                     "active"),
    ("Kilowatt Case",                       "active"),
    ("Gallery Case",                        "active"),
]

session = requests.Session()


def load_case_data():
    if os.path.exists(CASE_DATA_FILE):
        with open(CASE_DATA_FILE) as f:
            return json.load(f)
    return {}


def save_case_data(data):
    os.makedirs(os.path.dirname(CASE_DATA_FILE), exist_ok=True)
    with open(CASE_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_price_history(item_name):
    """Fetch lifetime price history for a single item.

    Returns a list of [timestamp_ms, price, volume] entries, or None on failure.
    """
    params = {"appid": APP_ID, "market_hash_name": item_name, "currency": 1}
    try:
        resp = session.get(PRICE_HISTORY_URL, params=params, timeout=15)

        if resp.status_code == 429:
            print("    ⏳ Rate limited — waiting 60s...")
            time.sleep(60)
            resp = session.get(PRICE_HISTORY_URL, params=params, timeout=15)

        if resp.status_code in (401, 403):
            print("    ❌ Auth failed — check your steamLoginSecure cookie.")
            return None

        if not resp.ok:
            print(f"    ❌ HTTP {resp.status_code}")
            return None

        data = resp.json()
        if not data.get("success"):
            print("    ❌ API returned success=false")
            return None

        entries = []
        for entry in data.get("prices", []):
            date_str, price, volume_str = entry
            try:
                dt = datetime.strptime(date_str.strip(), "%b %d %Y %H: +0")
                ts = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
            except ValueError:
                continue
            entries.append([ts, round(float(price), 2), int(volume_str)])

        return entries

    except Exception as e:
        print(f"    ❌ Error: {e}")
        return None


def main():
    case_data = load_case_data()
    total = len(CASES)
    existing = sum(1 for name, _ in CASES if name in case_data)

    print(f"📦 CS2 Case Investment Tracker")
    print(f"   {total} cases to track, {existing} already fetched.\n")

    # Get Steam cookie
    cookie = os.environ.get("STEAM_LOGIN_SECURE", "").strip()
    if not cookie:
        print("🔑 No STEAM_LOGIN_SECURE env var found.")
        print("   Paste your steamLoginSecure cookie (from browser DevTools → Cookies):")
        cookie = input("   > ").strip()

    if not cookie:
        if existing > 0:
            print("\n⚠️  No cookie provided. Keeping existing data.\n")
        else:
            print("\n❌ No cookie and no existing data. Cannot proceed.")
            sys.exit(1)
    else:
        session.cookies.set("steamLoginSecure", cookie, domain="steamcommunity.com")
        session.headers.update(HEADERS)

        fetched = 0
        for i, (name, status) in enumerate(CASES, 1):
            if name in case_data:
                print(f"  [{i}/{total}] ✅ {name} (already fetched)")
                continue

            print(f"  [{i}/{total}] 📈 {name}...", end=" ", flush=True)
            history = fetch_price_history(name)

            if history is not None:
                case_data[name] = history
                print(f"OK ({len(history)} data points)")
                fetched += 1
                save_case_data(case_data)
            else:
                print("skipped")

            if i < total:
                time.sleep(REQUEST_DELAY)

        print(f"\n  Fetched {fetched} new case(s), {len(case_data)} total.\n")

    # Save metadata alongside history
    meta = {}
    for name, status in CASES:
        meta[name] = {"status": status}

    meta_file = CASE_DATA_FILE.replace(".json", "_meta.json")
    with open(meta_file, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"✅ Case data saved to {CASE_DATA_FILE}")
    print(f"✅ Case metadata saved to {meta_file}")
    print(f"   Run `python steam_dashboard.py` to see the Cases tab.\n")

    print("=" * 60)
    print("  💰 ETH Donation: 0x89705f4d632E93F8a466683Dc520577Ec08D37e0")
    print("  🐙 GitHub:       https://github.com/persian-punks")
    print("=" * 60)


if __name__ == "__main__":
    main()
