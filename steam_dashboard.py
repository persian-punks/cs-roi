#!/usr/bin/env python3
"""Generate a combined Steam CS2 dashboard with tabbed navigation.

Merges the inventory report and price charts into a single HTML page
with two views toggled by navigation tabs.
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────────
INPUT_FILE = "data/steam_inventory.json"
PRICE_DATA_FILE = "data/steam_price_history.json"
OUTPUT_FILE = "reports/steam_dashboard.html"

RARITY_EMOJI = {
    "Covert":           "🔴",
    "Extraordinary":    "🟡",
    "Classified":       "🟣",
    "Restricted":       "🔵",
    "Mil-Spec Grade":   "🔷",
    "Industrial Grade":  "⚪",
    "Consumer Grade":   "⬜",
    "High Grade":       "🟢",
    "Remarkable":       "🟠",
    "Base Grade":       "⚫",
    "Stock":            "⬛",
}

RARITY_COLOR = {
    "Covert":           "#eb4b4b",
    "Extraordinary":    "#ffd700",
    "Classified":       "#d32ce6",
    "Restricted":       "#8847ff",
    "Mil-Spec Grade":   "#4b69ff",
    "Industrial Grade":  "#5e98d9",
    "Consumer Grade":   "#b0c3d9",
    "High Grade":       "#4b69ff",
    "Remarkable":       "#cf6a32",
    "Base Grade":       "#b0c3d9",
    "Stock":            "#888888",
}


# ═══════════════════════════════════════════════════════════════════════════
#  Inventory tab helpers  (from steam_inventory_report_html.py)
# ═══════════════════════════════════════════════════════════════════════════

def item_value(item):
    p = item.get("market_price", {})
    return p.get("lowest_price") or p.get("median_price") or 0


def format_price(price):
    if price is None:
        return "—"
    return f"${price:,.2f}"


def wear_bar_html(wear):
    if wear is None:
        return ""
    pct = wear * 100
    return (f'<div class="wear-bar-container">'
            f'<div class="wear-bar-fill" style="width:{pct:.1f}%"></div>'
            f'</div><span class="wear-value">{wear:.4f}</span>')


def generate_item_card(item):
    val = item_value(item)
    price = item.get("market_price", {})
    lowest = format_price(price.get("lowest_price"))
    median = format_price(price.get("median_price"))
    ext = item.get("exterior", "")
    wear = item.get("wear_rating")
    rarity = item.get("rarity", "")
    rarity_color = RARITY_COLOR.get(rarity, "#888")
    tradable = item.get("tradable", False)
    name = item.get("name", "Unknown")
    img_url = item.get("image_url", "")

    img_html = f'<img src="{img_url}" alt="{name}" />' if img_url else '<div class="no-image">No Image</div>'
    tradable_html = ('<span class="badge tradable">✅ Tradable</span>' if tradable
                     else '<span class="badge not-tradable">🔒 Not Tradable</span>')
    wear_html = (f'<div class="detail-row"><span class="detail-label">Wear</span>'
                 f'{wear_bar_html(wear)}</div>') if wear is not None else ""
    exterior_html = (f'<div class="detail-row"><span class="detail-label">Exterior</span>'
                     f'<span>{ext}</span></div>') if ext else ""
    pattern_html = (f'<div class="detail-row"><span class="detail-label">Pattern</span>'
                    f'<code>{item["pattern_template"]}</code></div>') if "pattern_template" in item else ""

    return f"""<div class="item-card">
    <div class="item-image">{img_html}</div>
    <div class="item-details">
        <h3 class="item-name">{name}</h3>
        <span class="rarity-badge" style="background:{rarity_color}">{RARITY_EMOJI.get(rarity, '❔')} {rarity}</span>
        <div class="price-row">
            <span class="price-main">{format_price(val)}</span>
            <span class="price-sub">Low: {lowest} / Med: {median}</span>
        </div>
        {exterior_html}
        {wear_html}
        {pattern_html}
        <div class="detail-row">
            {tradable_html}
            <span class="asset-id">Asset: <code>{item.get('assetid', '—')}</code></span>
        </div>
    </div>
</div>"""


def generate_profile_section(steam_id, profile, rank):
    username = profile.get("username") or steam_id
    items = profile.get("items", [])
    total_value = profile.get("estimated_value", 0)
    error = profile.get("error")

    section = f"""<section class="profile-section">
    <div class="profile-header">
        <h2>{'⚠️' if error else '👤'} {username}</h2>
        <div class="profile-meta">
            <span>Steam ID: <code>{steam_id}</code></span>
            <span>Items: <b>{len(items)}</b></span>
            <span>Estimated Value: <b class="accent-green">{format_price(total_value)}</b></span>
        </div>
    </div>"""

    if error:
        return section + f'<div class="alert">⚠️ Could not fetch inventory: {error}</div></section>'
    if not items:
        return section + '<div class="alert">No items found (inventory may be empty or private).</div></section>'

    # Rarity breakdown
    rarity_counts = Counter()
    rarity_values = Counter()
    for it in items:
        r = it.get("rarity", "Unknown")
        rarity_counts[r] += 1
        rarity_values[r] += item_value(it)

    section += '<div class="rarity-breakdown"><h3>Breakdown by Rarity</h3><div class="rarity-bars">'
    for rarity in sorted(rarity_counts, key=lambda r: rarity_values[r], reverse=True):
        count = rarity_counts[rarity]
        value = rarity_values[rarity]
        color = RARITY_COLOR.get(rarity, "#888")
        emoji = RARITY_EMOJI.get(rarity, "❔")
        max_val = max(rarity_values.values()) if rarity_values else 1
        bar_pct = (value / max_val * 100) if max_val else 0
        section += (f'<div class="rarity-row">'
                    f'<span class="rarity-label" style="color:{color}">{emoji} {rarity}</span>'
                    f'<div class="rarity-bar-track"><div class="rarity-bar-fill" style="width:{bar_pct:.1f}%;background:{color}"></div></div>'
                    f'<span class="rarity-stat"><b>{count}</b> item{"s" if count != 1 else ""} — <b>{format_price(value)}</b></span>'
                    f'</div>')
    section += '</div></div>'

    # Categorize items
    priced_items = [it for it in items if item_value(it) > 0]
    if priced_items:
        knives_gloves = [it for it in priced_items if it.get("quality") == "★"]
        weapons = [it for it in priced_items
                   if it.get("quality") != "★" and it.get("weapon")
                   and "Music Kit" not in it.get("name", "")
                   and "Sticker" not in it.get("type", "")
                   and "Graffiti" not in it.get("type", "")
                   and "Collectible" not in it.get("type", "")]
        other = [it for it in priced_items if it not in knives_gloves and it not in weapons]

        def render_category(heading, item_list):
            if not item_list:
                return ""
            cards = "\n".join(generate_item_card(it) for it in item_list)
            return f'<div class="category"><h3>{heading}</h3><div class="item-grid">{cards}</div></div>'

        section += render_category("🗡️ Knives & Gloves", knives_gloves)
        section += render_category("🔫 Weapon Skins", weapons)
        section += render_category("🎵 Music Kits, Stickers & Other", other)

    no_value_items = [it for it in items if item_value(it) == 0]
    if no_value_items:
        section += '<div class="category"><h3>📦 Other Items (No Market Value)</h3><div class="no-value-list">'
        for it in no_value_items:
            tradable = "✅" if it.get("tradable") else "🔒"
            img_url = it.get("image_url", "")
            img_tag = f'<img src="{img_url}" class="mini-img" />' if img_url else ""
            section += (f'<div class="no-value-item">{tradable} {img_tag}'
                        f'<b>{it.get("name", "Unknown")}</b>'
                        f'<span class="item-type">{it.get("type", "")}</span></div>')
        section += '</div></div>'

    section += '</section>'
    return section


def build_inventory_content(data):
    """Build the inner HTML for the Inventory tab."""
    total_profiles = len(data)
    total_items = sum(p.get("total_items", len(p.get("items", []))) for p in data.values())
    grand_total = sum(p.get("estimated_value", 0) for p in data.values())
    sorted_profiles = sorted(data.items(), key=lambda kv: kv[1].get("estimated_value", 0), reverse=True)

    # Leaderboard
    leaderboard = ""
    if total_profiles > 1:
        medals = ["🥇", "🥈", "🥉"]
        rows = ""
        for i, (sid, prof) in enumerate(sorted_profiles):
            medal = medals[i] if i < len(medals) else f"#{i+1}"
            uname = prof.get("username") or sid
            val = prof.get("estimated_value", 0)
            count = prof.get("total_items", len(prof.get("items", [])))
            rows += (f'<div class="leaderboard-row"><span class="medal">{medal}</span>'
                     f'<span class="lb-name">{uname}</span>'
                     f'<span class="lb-value">{format_price(val)}</span>'
                     f'<span class="lb-count">{count} items</span></div>')
        leaderboard = f'<div class="leaderboard"><h3>🏆 Leaderboard</h3>{rows}</div>'

    profiles_html = ""
    for rank, (steam_id, profile) in enumerate(sorted_profiles, 1):
        profiles_html += generate_profile_section(steam_id, profile, rank)

    return f"""
    <div class="summary">
        <div class="summary-card"><div class="value">{total_profiles}</div><div class="label">Profiles Scanned</div></div>
        <div class="summary-card"><div class="value">{total_items}</div><div class="label">Total Items</div></div>
        <div class="summary-card"><div class="value accent-green">{format_price(grand_total)}</div><div class="label">Combined Value</div></div>
    </div>
    {leaderboard}
    {profiles_html}"""


# ═══════════════════════════════════════════════════════════════════════════
#  Price Charts tab helpers  (from steam_price_charts.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_unique_marketable_items(data):
    items = {}
    for profile in data.values():
        for item in profile.get("items", []):
            if item.get("marketable") and item["name"] not in items:
                items[item["name"]] = {
                    "name": item["name"],
                    "type": item.get("type", ""),
                    "rarity": item.get("rarity", ""),
                    "rarity_color": item.get("rarity_color", "ffffff"),
                    "image_url": item.get("image_url", ""),
                    "exterior": item.get("exterior", ""),
                    "market_price": item.get("market_price", {}),
                }
    return items


def build_charts_data(items, price_data):
    """Build the JSON-serializable chart data for the Price Charts tab."""
    chart_items = []
    for name, meta in items.items():
        if name not in price_data or not price_data[name]:
            continue
        chart_items.append({
            "name": name,
            "type": meta.get("type", ""),
            "rarity": meta.get("rarity", ""),
            "rarity_color": meta.get("rarity_color", "ffffff"),
            "image_url": meta.get("image_url", ""),
            "exterior": meta.get("exterior", ""),
            "current_price": (meta.get("market_price") or {}).get("lowest_price"),
            "history": price_data[name],
        })
    chart_items.sort(key=lambda x: x.get("current_price") or 0, reverse=True)
    return chart_items


def build_charts_content():
    """Build the inner HTML for the Price Charts tab (controls + containers)."""
    return """
    <div class="charts-controls controls">
        <label>Sort by:
            <select id="sortSelect">
                <option value="price-desc">Price (High → Low)</option>
                <option value="price-asc">Price (Low → High)</option>
                <option value="name-asc">Name (A → Z)</option>
                <option value="change-desc">Change % (Best → Worst)</option>
                <option value="change-asc">Change % (Worst → Best)</option>
            </select>
        </label>
        <label>Search:
            <input type="text" id="searchInput" placeholder="Filter items..." />
        </label>
        <label>Time Range:
            <select id="globalTimeRange">
                <option value="all">All Time</option>
                <option value="1y">1 Year</option>
                <option value="6m">6 Months</option>
                <option value="3m">3 Months</option>
                <option value="1m">1 Month</option>
                <option value="1w">1 Week</option>
            </select>
        </label>
    </div>
    <div class="summary" id="summarySection"></div>
    <div class="chart-grid" id="chartGrid"></div>"""


# ═══════════════════════════════════════════════════════════════════════════
#  Combined HTML generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_dashboard(data, price_data, input_file):
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    items = get_unique_marketable_items(data)
    charts_json = json.dumps(build_charts_data(items, price_data))
    inventory_content = build_inventory_content(data)
    charts_content = build_charts_content()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>remilio CS2 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<style>
  :root {{
    --bg: #1b2838;
    --card-bg: #1e2a3a;
    --text: #c6d4df;
    --accent: #66c0f4;
    --accent-green: #a4d007;
    --border: #2a475e;
    --header-bg: #171a21;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}

  /* ── Header & Nav ── */
  header {{
    background: var(--header-bg);
    padding: 20px 32px 0;
    border-bottom: 2px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  .header-top {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 16px;
  }}
  header h1 {{
    font-size: 1.8em;
    color: #fff;
  }}
  header .timestamp {{
    color: #8f98a0;
    font-size: 0.85em;
  }}
  .tab-nav {{
    display: flex;
    gap: 0;
  }}
  .tab-nav button {{
    background: transparent;
    color: #8f98a0;
    border: none;
    padding: 10px 24px;
    font-size: 0.95em;
    font-weight: 600;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    transition: all 0.2s;
  }}
  .tab-nav button:hover {{
    color: #fff;
    background: rgba(102,192,244,0.08);
  }}
  .tab-nav button.active {{
    color: var(--accent);
    border-bottom-color: var(--accent);
  }}

  /* ── Tab panels ── */
  .tab-panel {{
    display: none;
  }}
  .tab-panel.active {{
    display: block;
  }}
  .container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
  }}

  /* ── Shared: Summary cards ── */
  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .summary-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px;
    text-align: center;
  }}
  .summary-card .value {{
    font-size: 1.8em;
    font-weight: bold;
    color: #fff;
  }}
  .summary-card .label {{
    color: #8f98a0;
    font-size: 0.85em;
    margin-top: 4px;
  }}
  .accent-green {{ color: var(--accent-green); }}

  /* ── Controls (charts tab) ── */
  .charts-controls {{
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
    flex-wrap: wrap;
    align-items: center;
  }}
  .controls select, .controls input {{
    background: var(--card-bg);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 4px;
    font-size: 0.9em;
  }}
  .controls label {{
    color: #8f98a0;
    font-size: 0.85em;
  }}

  /* ── Inventory tab styles ── */
  .leaderboard {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 32px;
  }}
  .leaderboard h3 {{ color: #fff; margin-bottom: 12px; }}
  .leaderboard-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
  }}
  .leaderboard-row:last-child {{ border-bottom: none; }}
  .medal {{ font-size: 1.4em; }}
  .lb-name {{ flex: 1; color: #fff; font-weight: 600; }}
  .lb-value {{ color: var(--accent-green); font-weight: bold; }}
  .lb-count {{ color: #8f98a0; font-size: 0.85em; }}
  .profile-section {{ margin-bottom: 40px; }}
  .profile-header {{
    background: var(--header-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
  }}
  .profile-header h2 {{ color: #fff; font-size: 1.5em; margin-bottom: 8px; }}
  .profile-meta {{
    display: flex; gap: 20px; flex-wrap: wrap;
    color: #8f98a0; font-size: 0.9em;
  }}
  .profile-meta code {{
    background: rgba(0,0,0,0.3); padding: 2px 6px;
    border-radius: 3px; font-size: 0.9em;
  }}
  .alert {{
    background: rgba(255,200,0,0.1);
    border: 1px solid rgba(255,200,0,0.3);
    border-radius: 6px; padding: 12px 16px;
    color: #ffd700; font-style: italic;
  }}
  .rarity-breakdown {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px; padding: 20px; margin-bottom: 20px;
  }}
  .rarity-breakdown h3 {{ color: #fff; margin-bottom: 14px; }}
  .rarity-row {{
    display: flex; align-items: center; gap: 12px; padding: 6px 0;
  }}
  .rarity-label {{ min-width: 160px; font-weight: 600; font-size: 0.9em; }}
  .rarity-bar-track {{
    flex: 1; height: 8px;
    background: rgba(255,255,255,0.05);
    border-radius: 4px; overflow: hidden;
  }}
  .rarity-bar-fill {{ height: 100%; border-radius: 4px; }}
  .rarity-stat {{
    min-width: 200px; text-align: right;
    font-size: 0.85em; color: #8f98a0;
  }}
  .category {{ margin-bottom: 24px; }}
  .category h3 {{
    color: #fff; font-size: 1.2em;
    margin-bottom: 14px; padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}
  .item-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(500px, 1fr));
    gap: 16px;
  }}
  .item-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px; display: flex;
    overflow: hidden;
    transition: transform 0.2s, border-color 0.2s;
  }}
  .item-card:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
  .item-image {{
    width: 160px; min-height: 120px;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.2); flex-shrink: 0;
  }}
  .item-image img {{ max-width: 140px; max-height: 105px; object-fit: contain; }}
  .no-image {{ color: #555; font-size: 0.8em; }}
  .item-details {{ flex: 1; padding: 14px 16px; }}
  .item-name {{ color: #fff; font-size: 1em; margin-bottom: 4px; }}
  .rarity-badge {{
    display: inline-block; padding: 2px 8px;
    border-radius: 3px; font-size: 0.72em;
    font-weight: 600; color: #fff; margin-bottom: 8px;
  }}
  .price-row {{ margin-bottom: 8px; }}
  .price-main {{
    font-size: 1.3em; font-weight: bold;
    color: var(--accent-green); margin-right: 8px;
  }}
  .price-sub {{ font-size: 0.8em; color: #8f98a0; }}
  .detail-row {{
    display: flex; align-items: center; gap: 8px;
    font-size: 0.85em; margin-bottom: 4px; flex-wrap: wrap;
  }}
  .detail-label {{ color: #8f98a0; min-width: 55px; }}
  .detail-row code {{
    background: rgba(0,0,0,0.3); padding: 1px 6px;
    border-radius: 3px; font-size: 0.9em;
  }}
  .wear-bar-container {{
    width: 120px; height: 8px;
    background: rgba(255,255,255,0.08);
    border-radius: 4px; overflow: hidden;
  }}
  .wear-bar-fill {{
    height: 100%;
    background: linear-gradient(90deg, #a4d007, #ffd700, #ff4444);
    border-radius: 4px;
  }}
  .wear-value {{ font-size: 0.8em; color: #8f98a0; font-family: monospace; }}
  .badge {{ font-size: 0.8em; padding: 2px 8px; border-radius: 3px; }}
  .badge.tradable {{ background: rgba(164,208,7,0.15); color: #a4d007; }}
  .badge.not-tradable {{ background: rgba(255,68,68,0.15); color: #ff6666; }}
  .asset-id {{ font-size: 0.8em; color: #555; }}
  .no-value-list {{ display: flex; flex-direction: column; gap: 6px; }}
  .no-value-item {{
    display: flex; align-items: center; gap: 8px;
    padding: 8px 12px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px; font-size: 0.9em;
  }}
  .no-value-item .mini-img {{ width: 48px; height: 36px; object-fit: contain; }}
  .item-type {{ color: #8f98a0; font-size: 0.85em; margin-left: auto; }}

  /* ── Price Charts tab styles ── */
  .chart-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(600px, 1fr));
    gap: 20px;
  }}
  .chart-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px; overflow: hidden;
    transition: transform 0.2s;
  }}
  .chart-card:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
  .chart-header {{
    display: flex; align-items: center; gap: 12px;
    padding: 16px; border-bottom: 1px solid var(--border);
  }}
  .chart-header img {{
    width: 80px; height: 60px; object-fit: contain;
    background: rgba(0,0,0,0.2); border-radius: 4px;
  }}
  .chart-header .info {{ flex: 1; }}
  .chart-header .info h3 {{ color: #fff; font-size: 1em; margin-bottom: 2px; }}
  .chart-header .info .meta {{ font-size: 0.8em; color: #8f98a0; }}
  .chart-header .price {{ font-size: 1.4em; font-weight: bold; color: #a4d007; }}
  .chart-header .rarity-badge {{ margin-top: 4px; font-size: 0.75em; }}
  .chart-body {{ padding: 12px 16px 16px; position: relative; }}
  .chart-body canvas {{ width: 100% !important; height: 250px !important; }}
  .chart-stats {{
    display: flex; gap: 16px; padding: 0 16px 12px;
    font-size: 0.8em; color: #8f98a0; flex-wrap: wrap;
  }}
  .chart-stats span {{ white-space: nowrap; }}
  .chart-stats .up {{ color: #a4d007; }}
  .chart-stats .down {{ color: #ff4444; }}
  .time-buttons {{ display: flex; gap: 4px; padding: 8px 16px; }}
  .time-buttons button {{
    background: transparent; color: #8f98a0;
    border: 1px solid var(--border);
    padding: 3px 10px; border-radius: 3px;
    cursor: pointer; font-size: 0.75em; transition: all 0.2s;
  }}
  .time-buttons button:hover,
  .time-buttons button.active {{
    background: var(--accent); color: #fff; border-color: var(--accent);
  }}

  /* ── Footer ── */
  footer {{
    text-align: center; padding: 24px;
    color: #555; font-size: 0.8em;
    border-top: 1px solid var(--border); margin-top: 40px;
  }}

  @media (max-width: 700px) {{
    .chart-grid {{ grid-template-columns: 1fr; }}
    .item-grid {{ grid-template-columns: 1fr; }}
    .item-card {{ flex-direction: column; }}
    .item-image {{ width: 100%; min-height: 80px; }}
    .rarity-label {{ min-width: 100px; }}
    .rarity-stat {{ min-width: auto; }}
    header h1 {{ font-size: 1.3em; }}
    .tab-nav button {{ padding: 10px 14px; font-size: 0.85em; }}
  }}
</style>
</head>
<body>

<header>
  <div class="header-top">
    <h1>🎮 Steam CS2 Dashboard</h1>
    <span class="timestamp">Generated on {now}</span>
  </div>
  <nav class="tab-nav">
    <button class="active" data-tab="inventory">📦 Inventory</button>
    <button data-tab="charts">📈 Price Charts</button>
  </nav>
</header>

<!-- ── Inventory Tab ── -->
<div class="tab-panel active" id="tab-inventory">
  <div class="container">
    {inventory_content}
  </div>
</div>

<!-- ── Price Charts Tab ── -->
<div class="tab-panel" id="tab-charts">
  <div class="container">
    {charts_content}
  </div>
</div>

<footer>
  Report generated from <code>{input_file}</code> by steam_dashboard.py
</footer>

<script>
// ═══════════════════════════════════════════════════════════════════
//  Tab navigation
// ═══════════════════════════════════════════════════════════════════
let chartsInitialized = false;

document.querySelectorAll('.tab-nav button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    // Update active button
    document.querySelectorAll('.tab-nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Update active panel
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    // Lazy-init charts on first visit (Chart.js needs visible containers)
    if (btn.dataset.tab === 'charts' && !chartsInitialized) {{
      chartsInitialized = true;
      renderDashboard('price-desc', '');
    }}
  }});
}});

// ═══════════════════════════════════════════════════════════════════
//  Price Charts logic
// ═══════════════════════════════════════════════════════════════════
const ITEMS = {charts_json};

function filterByTime(history, range) {{
  if (range === 'all') return history;
  const now = Date.now();
  const ms = {{
    '1w': 7*86400000, '1m': 30*86400000, '3m': 90*86400000,
    '6m': 180*86400000, '1y': 365*86400000
  }}[range] || 0;
  return history.filter(d => d[0] >= now - ms);
}}

function computeStats(history) {{
  if (!history.length) return {{}};
  const prices = history.map(d => d[1]);
  const allTimeHigh = Math.max(...prices);
  const allTimeLow = Math.min(...prices);
  const first = prices[0], last = prices[prices.length - 1];
  const changePct = first > 0 ? ((last - first) / first * 100) : 0;
  const avgVol = history.reduce((s, d) => s + d[2], 0) / history.length;
  return {{ allTimeHigh, allTimeLow, first, last, changePct, avgVol }};
}}

const charts = {{}};

function createChart(canvasId, history, rarityColor) {{
  const ctx = document.getElementById(canvasId);
  if (!ctx) return null;
  const data = history.map(d => ({{ x: d[0], y: d[1] }}));
  const volData = history.map(d => ({{ x: d[0], y: d[2] }}));
  const color = '#' + (rarityColor || '66c0f4');

  return new Chart(ctx, {{
    type: 'line',
    data: {{
      datasets: [
        {{
          label: 'Price (USD)', data: data,
          borderColor: color, backgroundColor: color + '20',
          fill: true, tension: 0.3, pointRadius: 0,
          pointHitRadius: 8, borderWidth: 2, yAxisID: 'y',
        }},
        {{
          label: 'Volume', data: volData, type: 'bar',
          backgroundColor: 'rgba(102,192,244,0.15)',
          borderColor: 'transparent', yAxisID: 'y1',
          barPercentage: 1, categoryPercentage: 1,
        }}
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1e2a3a', titleColor: '#fff',
          bodyColor: '#c6d4df', borderColor: '#2a475e', borderWidth: 1,
          callbacks: {{
            title(items) {{
              return new Date(items[0].parsed.x).toLocaleDateString('en-US', {{
                year: 'numeric', month: 'short', day: 'numeric'
              }});
            }},
            label(ctx) {{
              if (ctx.datasetIndex === 0) return ' $' + ctx.parsed.y.toFixed(2);
              return ' Vol: ' + ctx.parsed.y;
            }}
          }}
        }}
      }},
      scales: {{
        x: {{
          type: 'time',
          time: {{
            tooltipFormat: 'MMM d, yyyy',
            displayFormats: {{ day: 'MMM d', week: 'MMM d', month: 'MMM yyyy', year: 'yyyy' }}
          }},
          grid: {{ color: 'rgba(42,71,94,0.4)' }},
          ticks: {{ color: '#8f98a0', maxTicksLimit: 8 }},
        }},
        y: {{
          position: 'left',
          grid: {{ color: 'rgba(42,71,94,0.4)' }},
          ticks: {{ color: '#8f98a0', callback: v => '$' + v.toFixed(2) }},
        }},
        y1: {{
          position: 'right',
          grid: {{ drawOnChartArea: false }},
          ticks: {{ color: '#555', maxTicksLimit: 4 }},
        }}
      }}
    }}
  }});
}}

function updateChart(name, range) {{
  const item = ITEMS.find(it => it.name === name);
  if (!item || !charts[name]) return;
  const filtered = filterByTime(item.history, range);
  charts[name].data.datasets[0].data = filtered.map(d => ({{ x: d[0], y: d[1] }}));
  charts[name].data.datasets[1].data = filtered.map(d => ({{ x: d[0], y: d[2] }}));
  charts[name].update('none');
  const statsEl = document.getElementById('stats-' + CSS.escape(name));
  if (statsEl) renderStats(statsEl, computeStats(filtered));
}}

function renderStats(el, stats) {{
  if (!stats.allTimeHigh) {{ el.innerHTML = ''; return; }}
  const cls = stats.changePct >= 0 ? 'up' : 'down';
  const arrow = stats.changePct >= 0 ? '▲' : '▼';
  el.innerHTML = `
    <span>High: <b>$${{stats.allTimeHigh.toFixed(2)}}</b></span>
    <span>Low: <b>$${{stats.allTimeLow.toFixed(2)}}</b></span>
    <span class="${{cls}}">${{arrow}} ${{stats.changePct.toFixed(1)}}%</span>
    <span>Avg Vol: <b>${{Math.round(stats.avgVol)}}</b></span>
  `;
}}

let currentRange = 'all';

function renderDashboard(sortKey, searchTerm) {{
  Object.values(charts).forEach(c => c.destroy());
  for (const k in charts) delete charts[k];

  let items = [...ITEMS];
  if (searchTerm) {{
    const q = searchTerm.toLowerCase();
    items = items.filter(it => it.name.toLowerCase().includes(q) || it.type.toLowerCase().includes(q));
  }}
  items.forEach(it => {{ it._stats = computeStats(filterByTime(it.history, currentRange)); }});

  switch (sortKey) {{
    case 'price-desc': items.sort((a, b) => (b.current_price||0) - (a.current_price||0)); break;
    case 'price-asc':  items.sort((a, b) => (a.current_price||0) - (b.current_price||0)); break;
    case 'name-asc':   items.sort((a, b) => a.name.localeCompare(b.name)); break;
    case 'change-desc': items.sort((a, b) => (b._stats.changePct||0) - (a._stats.changePct||0)); break;
    case 'change-asc':  items.sort((a, b) => (a._stats.changePct||0) - (b._stats.changePct||0)); break;
  }}

  const summaryEl = document.getElementById('summarySection');
  const totalItems = items.length;
  const totalValue = items.reduce((s, it) => s + (it.current_price || 0), 0);
  const avgChange = items.length ? items.reduce((s, it) => s + (it._stats.changePct||0), 0) / items.length : 0;

  summaryEl.innerHTML = `
    <div class="summary-card"><div class="value">${{totalItems}}</div><div class="label">Items Tracked</div></div>
    <div class="summary-card"><div class="value">$${{totalValue.toFixed(2)}}</div><div class="label">Total Current Value</div></div>
    <div class="summary-card"><div class="value" style="color:${{avgChange>=0?'#a4d007':'#ff4444'}}">${{avgChange>=0?'+':''}}${{avgChange.toFixed(1)}}%</div><div class="label">Avg Price Change (${{currentRange==='all'?'All Time':currentRange}})</div></div>
  `;

  const grid = document.getElementById('chartGrid');
  grid.innerHTML = '';

  items.forEach((item, idx) => {{
    const canvasId = 'chart-' + idx;
    const filtered = filterByTime(item.history, currentRange);
    const priceStr = item.current_price ? '$' + item.current_price.toFixed(2) : '—';

    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `
      <div class="chart-header">
        ${{item.image_url ? `<img src="${{item.image_url}}" alt="${{item.name}}" />` : ''}}
        <div class="info">
          <h3>${{item.name}}</h3>
          <div class="meta">${{item.type}}${{item.exterior ? ' • ' + item.exterior : ''}}</div>
          ${{item.rarity ? `<span class="rarity-badge" style="background:#${{item.rarity_color}}">${{item.rarity}}</span>` : ''}}
        </div>
        <div class="price">${{priceStr}}</div>
      </div>
      <div class="time-buttons" data-item="${{item.name.replace(/"/g, '&quot;')}}">
        <button data-range="1w">1W</button>
        <button data-range="1m">1M</button>
        <button data-range="3m">3M</button>
        <button data-range="6m">6M</button>
        <button data-range="1y">1Y</button>
        <button data-range="all" class="active">ALL</button>
      </div>
      <div class="chart-stats" id="stats-${{CSS.escape(item.name)}}"></div>
      <div class="chart-body"><canvas id="${{canvasId}}"></canvas></div>
    `;
    grid.appendChild(card);

    charts[item.name] = createChart(canvasId, filtered, item.rarity_color);
    renderStats(card.querySelector('.chart-stats'), item._stats);

    card.querySelectorAll('.time-buttons button').forEach(btn => {{
      btn.addEventListener('click', () => {{
        card.querySelectorAll('.time-buttons button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateChart(item.name, btn.dataset.range);
      }});
    }});
  }});
}}

// ── Chart control event listeners ──
document.getElementById('sortSelect').addEventListener('change', e => {{
  renderDashboard(e.target.value, document.getElementById('searchInput').value);
}});
document.getElementById('searchInput').addEventListener('input', e => {{
  renderDashboard(document.getElementById('sortSelect').value, e.target.value);
}});
document.getElementById('globalTimeRange').addEventListener('change', e => {{
  currentRange = e.target.value;
  renderDashboard(document.getElementById('sortSelect').value, document.getElementById('searchInput').value);
}});
</script>
</body>
</html>"""

    return html


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    output_file = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    with open(input_file) as f:
        data = json.load(f)

    price_data = {}
    if os.path.exists(PRICE_DATA_FILE):
        with open(PRICE_DATA_FILE) as f:
            price_data = json.load(f)

    html = generate_dashboard(data, price_data, input_file)
    with open(output_file, "w") as f:
        f.write(html)
        f.close()

    with open('docs/index.html', "w") as f:
        f.write(html)
        f.close()    

    size_kb = len(html) / 1024
    print(f"✅ Dashboard written to {output_file} ({size_kb:.0f} KB)")
    print(f"✅ Dashboard written to docs/index.html ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
