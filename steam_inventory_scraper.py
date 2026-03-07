#!/usr/bin/env python3
"""Scrape CS2 inventory items from a Steam profile."""

import re
import requests
import time
import json
import sys
import os
from dotenv import load_dotenv

load_dotenv()
STEAM_ID = os.getenv("STEAM_ID")
STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")

DEFAULT_STEAM_IDS = [STEAM_ID]
APP_ID = 730      # CS2
CONTEXT_ID = 2
BATCH_SIZE = 75   # max items per request
PORTFOLIO_HISTORY_FILE = "data/portfolio_history.json"

INVENTORY_BASE_URL = "https://steamcommunity.com/inventory"
PROFILE_BASE_URL = "https://steamcommunity.com/profiles"
MARKET_PRICE_URL = "https://steamcommunity.com/market/priceoverview/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


PLAYER_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

PERSONA_STATES = {
    0: "Offline", 1: "Online", 2: "Busy",
    3: "Away", 4: "Snooze", 5: "Looking to Trade", 6: "Looking to Play",
}


def fetch_username(steam_id):
    """Fetch the display name for a Steam ID from their profile page."""
    try:
        resp = requests.get(f"{PROFILE_BASE_URL}/{steam_id}", headers=HEADERS, timeout=10)
        if resp.ok:
            match = re.search(r'"personaname":"([^"]+)"', resp.text)
            if match:
                return match.group(1)
    except Exception:
        pass
    return None


def fetch_player_summaries(steam_ids):
    """Fetch rich profile data via Steam Web API (requires STEAM_API_KEY).

    Returns a dict of steam_id -> profile info, or {} if no API key.
    """
    if not STEAM_API_KEY:
        return {}
    try:
        params = {"key": STEAM_API_KEY, "steamids": ",".join(steam_ids)}
        resp = requests.get(PLAYER_SUMMARIES_URL, params=params, headers=HEADERS, timeout=10)
        if not resp.ok:
            print(f"  ⚠️ Steam API returned HTTP {resp.status_code} — skipping profile enrichment.")
            return {}
        data = resp.json()
        result = {}
        for player in data.get("response", {}).get("players", []):
            sid = player.get("steamid", "")
            state = player.get("personastate", 0)
            result[sid] = {
                "avatar_url": player.get("avatarfull", ""),
                "profile_url": player.get("profileurl", ""),
                "persona_state": state,
                "persona_state_text": PERSONA_STATES.get(state, "Offline"),
                "time_created": player.get("timecreated"),
                "country_code": player.get("loccountrycode", ""),
                "real_name": player.get("realname", ""),
                "game_name": player.get("gameextrainfo", ""),
            }
        return result
    except Exception as e:
        print(f"  ⚠️ Failed to fetch player summaries: {e}")
        return {}


def fetch_inventory(steam_id):
    """Fetch all inventory items, handling pagination."""
    inventory_url = f"{INVENTORY_BASE_URL}/{steam_id}/{APP_ID}/{CONTEXT_ID}"
    all_assets = []
    descriptions = {}
    wear_ratings = {}  # assetid -> float wear value
    pattern_templates = {}  # assetid -> pattern template int
    last_assetid = None

    while True:
        params = {"l": "english", "count": BATCH_SIZE}
        if last_assetid:
            params["start_assetid"] = last_assetid

        print(f"  Fetching batch (start={last_assetid or 'beginning'})...")
        resp = requests.get(inventory_url, params=params, headers=HEADERS)

        if resp.status_code == 429:
            print("Rate limited — waiting 30s...")
            time.sleep(30)
            continue

        resp.raise_for_status()
        data = resp.json()

        if not data.get("success"):
            print("Steam returned success=false. Inventory may be private.")
            break

        # Collect asset entries
        for asset in data.get("assets", []):
            all_assets.append(asset)

        # Collect description metadata (keyed by classid+instanceid)
        for desc in data.get("descriptions", []):
            key = (desc["classid"], desc["instanceid"])
            descriptions[key] = desc

        # Collect wear ratings from asset_properties
        for entry in data.get("asset_properties", []):
            aid = entry.get("assetid")
            for prop in entry.get("asset_properties", []):
                if prop.get("name") == "Wear Rating" and "float_value" in prop:
                    wear_ratings[aid] = float(prop["float_value"])
                elif prop.get("name") == "Pattern Template" and "int_value" in prop:
                    pattern_templates[aid] = int(prop["int_value"])

        # Pagination
        if data.get("more_items"):
            last_assetid = data.get("last_assetid")
            time.sleep(1)  # be polite
        else:
            break

    return all_assets, descriptions, wear_ratings, pattern_templates


def build_item_list(assets, descriptions, wear_ratings, pattern_templates):
    """Merge assets with their descriptions into a flat item list."""
    items = []
    for asset in assets:
        key = (asset["classid"], asset["instanceid"])
        desc = descriptions.get(key, {})

        # Build image URL from icon_url_large (preferred) or icon_url
        icon_path = desc.get("icon_url_large") or desc.get("icon_url", "")
        image_url = f"https://community.akamai.steamstatic.com/economy/image/{icon_path}" if icon_path else ""

        item = {
            "assetid": asset["assetid"],
            "name": desc.get("market_hash_name") or desc.get("name", "Unknown"),
            "type": desc.get("type", ""),
            "tradable": bool(desc.get("tradable")),
            "marketable": bool(desc.get("marketable")),
            "amount": asset.get("amount", "1"),
            "image_url": image_url,
        }

        # Extract exterior (e.g. Factory New, Minimal Wear)
        for tag in desc.get("tags", []):
            if tag.get("category") == "Exterior":
                item["exterior"] = tag["localized_tag_name"]
            elif tag.get("category") == "Rarity":
                item["rarity"] = tag["localized_tag_name"]
                item["rarity_color"] = tag.get("color", "")
            elif tag.get("category") == "Quality":
                item["quality"] = tag["localized_tag_name"]
            elif tag.get("category") == "Weapon":
                item["weapon"] = tag["localized_tag_name"]

        # Add wear rating and pattern template if available
        if asset["assetid"] in wear_ratings:
            item["wear_rating"] = wear_ratings[asset["assetid"]]
        if asset["assetid"] in pattern_templates:
            item["pattern_template"] = pattern_templates[asset["assetid"]]

        items.append(item)
    return items


def parse_price(price_str):
    """Parse a price string like '$79.13' into a float."""
    match = re.search(r"[\d,]+\.?\d*", price_str)
    if match:
        return float(match.group().replace(",", ""))
    return None


def fetch_market_price(market_hash_name):
    """Fetch lowest and median price from Steam Community Market."""
    try:
        params = {"appid": APP_ID, "currency": 1, "market_hash_name": market_hash_name}
        resp = requests.get(MARKET_PRICE_URL, params=params, headers=HEADERS, timeout=10)
        if resp.status_code == 429:
            print("    Steam Market rate limited — waiting 30s...")
            time.sleep(30)
            resp = requests.get(MARKET_PRICE_URL, params=params, headers=HEADERS, timeout=10)
        if resp.ok:
            data = resp.json()
            if data.get("success"):
                return {
                    "lowest_price": parse_price(data["lowest_price"]) if "lowest_price" in data else None,
                    "median_price": parse_price(data["median_price"]) if "median_price" in data else None,
                    "volume": data.get("volume"),
                }
    except Exception as e:
        print(f"    Price lookup failed: {e}")
    return None


def enrich_with_prices(items, price_cache):
    """Fetch Steam Market prices for all marketable items, reusing cached prices."""
    marketable = [it for it in items if it["marketable"]]
    # Only query names we haven't cached yet
    unique_names = list(dict.fromkeys(
        it["name"] for it in marketable if it["name"] not in price_cache
    ))
    total = len(unique_names)

    if total > 0:
        print(f"  Fetching market prices for {total} new unique item(s)...")
        for i, name in enumerate(unique_names, 1):
            print(f"    [{i}/{total}] {name}...", end=" ", flush=True)
            price_data = fetch_market_price(name)
            if price_data:
                price_cache[name] = price_data
                lp = price_data.get("lowest_price")
                mp = price_data.get("median_price")
                print(f"${lp or '-'} lowest / ${mp or '-'} median")
            else:
                print("unavailable")
            if i < total:
                time.sleep(5)  # Steam Market is strict on rate limits

    # Apply prices to all items
    for item in items:
        if item["name"] in price_cache:
            item["market_price"] = price_cache[item["name"]]


def print_profile_summary(steam_id, items):
    """Print a formatted summary for one profile's inventory."""
    total_value = 0.0
    for i, item in enumerate(items, 1):
        exterior = item.get("exterior", "")
        rarity = item.get("rarity", "")
        price_info = item.get("market_price", {})
        lowest = price_info.get("lowest_price")
        median = price_info.get("median_price")
        est_value = lowest or median
        if est_value:
            total_value += est_value
        tradable = "Tradable" if item["tradable"] else "Not Tradable"
        print(f"{i:>4}. {item['name']}")
        if item["type"]:
            print(f"        Type: {item['type']}")
        if rarity:
            print(f"        Rarity: {rarity}")
        if exterior:
            wear = item.get("wear_rating")
            wear_str = f" (Wear: {wear})" if wear is not None else ""
            print(f"        Exterior: {exterior}{wear_str}")
        if "pattern_template" in item:
            print(f"        Pattern: {item['pattern_template']}")
        if est_value:
            price_parts = []
            if lowest:
                price_parts.append(f"Lowest: ${lowest:.2f}")
            if median:
                price_parts.append(f"Median: ${median:.2f}")
            print(f"        Price: {' / '.join(price_parts)}")
        print(f"        {tradable} | Asset ID: {item['assetid']}")
        print()

    print(f"  Estimated value for {steam_id}: ${total_value:.2f}")
    return total_value


def save_portfolio_snapshot(all_profiles, grand_total):
    """Append today's portfolio value snapshot to the history file."""
    history = []
    if os.path.exists(PORTFOLIO_HISTORY_FILE):
        try:
            with open(PORTFOLIO_HISTORY_FILE) as f:
                history = json.load(f)
        except Exception:
            history = []

    today = time.strftime("%Y-%m-%d")
    snapshot = {
        "date": today,
        "total_value": round(grand_total, 2),
        "item_count": sum(p.get("total_items", len(p.get("items", []))) for p in all_profiles.values()),
        "profiles": {
            sid: {
                "username": p.get("username"),
                "value": p.get("estimated_value", 0),
                "item_count": p.get("total_items", len(p.get("items", []))),
            }
            for sid, p in all_profiles.items()
        },
    }

    # Replace today's entry if it exists, then keep sorted chronologically
    history = [e for e in history if e.get("date") != today]
    history.append(snapshot)
    history.sort(key=lambda e: e["date"])

    with open(PORTFOLIO_HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print(f"  Portfolio snapshot saved ({today}: ${grand_total:.2f})")


def main():
    steam_ids = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_STEAM_IDS

    print(f"Processing {len(steam_ids)} profile(s)...\n")

    # Fetch rich profile data via Steam API (if key is available)
    player_summaries = fetch_player_summaries(steam_ids)
    if player_summaries:
        print(f"  ✅ Fetched profile data for {len(player_summaries)} player(s) via Steam API.\n")

    all_profiles = {}  # steam_id -> {items, total_value}
    price_cache = {}   # shared across profiles to avoid redundant lookups
    grand_total = 0.0

    for idx, steam_id in enumerate(steam_ids):
        username = fetch_username(steam_id)
        display = f"{username} ({steam_id})" if username else steam_id
        print(f"{'='*60}")
        print(f"[{idx+1}/{len(steam_ids)}] {display}")
        print(f"{'='*60}")

        try:
            assets, descriptions, wear_ratings, pattern_templates = fetch_inventory(steam_id)
        except Exception as e:
            print(f"  Skipping — failed to fetch inventory: {e}\n")
            all_profiles[steam_id] = {"username": username, "items": [], "total_items": 0, "estimated_value": 0.0, "error": str(e)}
            continue

        if not assets:
            print("  No items found (inventory may be empty or private).\n")
            all_profiles[steam_id] = {"username": username, "items": [], "total_items": 0, "estimated_value": 0.0}
            continue

        items = build_item_list(assets, descriptions, wear_ratings, pattern_templates)
        enrich_with_prices(items, price_cache)

        # Sort by price descending (items with no price go to the end)
        def item_sort_key(it):
            p = it.get("market_price", {})
            return p.get("lowest_price") or p.get("median_price") or 0
        items.sort(key=item_sort_key, reverse=True)

        print(f"\n  Total items: {len(items)}\n")
        profile_value = print_profile_summary(steam_id, items)
        grand_total += profile_value

        profile_data = {
            "username": username,
            "items": items,
            "total_items": len(items),
            "estimated_value": round(profile_value, 2),
        }
        # Merge in rich profile data from Steam API
        if steam_id in player_summaries:
            profile_data["profile"] = player_summaries[steam_id]
        all_profiles[steam_id] = profile_data
        print()

    # Grand total
    if len(steam_ids) > 1:
        print(f"{'='*60}")
        print(f"Grand total across {len(steam_ids)} profiles: ${grand_total:.2f}")
        print(f"{'='*60}\n")

    # Save to JSON organized by Steam ID
    output_file = "data/steam_inventory.json"
    with open(output_file, "w") as f:
        json.dump(all_profiles, f, indent=2)
    save_portfolio_snapshot(all_profiles, grand_total)
    print(f"Full data saved to {output_file}")
    print()
    print("=" * 60)
    print("  💰 ETH Donation: 0x89705f4d632E93F8a466683Dc520577Ec08D37e0")
    print("  🐙 GitHub:       https://github.com/persian-punks")
    print("=" * 60)


if __name__ == "__main__":
    main()
