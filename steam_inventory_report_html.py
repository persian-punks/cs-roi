#!/usr/bin/env python3
"""Generate an elegant HTML report from steam_inventory.json."""

import json
import sys
from collections import Counter
from datetime import datetime, timezone

INPUT_FILE = "data/steam_inventory.json"
OUTPUT_FILE = "reports/steam_inventory_report.html"

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

WEAR_SHORT = {
    "Factory New":   "FN",
    "Minimal Wear":  "MW",
    "Field-Tested":  "FT",
    "Well-Worn":     "WW",
    "Battle-Scarred":"BS",
}


def item_value(item):
    """Return the best estimated value for an item."""
    p = item.get("market_price", {})
    return p.get("lowest_price") or p.get("median_price") or 0


def format_price(price):
    """Format a price value."""
    if price is None:
        return "—"
    return f"${price:,.2f}"


def wear_bar_html(wear):
    """Render a visual wear bar as an HTML element."""
    if wear is None:
        return ""
    pct = wear * 100
    return f"""<div class="wear-bar-container">
        <div class="wear-bar-fill" style="width:{pct:.1f}%"></div>
    </div>
    <span class="wear-value">{wear:.4f}</span>"""


def generate_item_card(item):
    """Generate HTML for a single item card."""
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
    tradable_html = '<span class="badge tradable">✅ Tradable</span>' if tradable else '<span class="badge not-tradable">🔒 Not Tradable</span>'
    wear_html = f"""<div class="detail-row">
            <span class="detail-label">Wear</span>
            {wear_bar_html(wear)}
        </div>""" if wear is not None else ""
    exterior_html = f'<div class="detail-row"><span class="detail-label">Exterior</span><span>{ext}</span></div>' if ext else ""
    pattern_html = f'<div class="detail-row"><span class="detail-label">Pattern</span><code>{item["pattern_template"]}</code></div>' if "pattern_template" in item else ""

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
    """Generate the HTML section for a single profile."""
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
        section += f'<div class="alert">⚠️ Could not fetch inventory: {error}</div></section>'
        return section

    if not items:
        section += '<div class="alert">No items found (inventory may be empty or private).</div></section>'
        return section

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
        section += f"""<div class="rarity-row">
            <span class="rarity-label" style="color:{color}">{emoji} {rarity}</span>
            <div class="rarity-bar-track">
                <div class="rarity-bar-fill" style="width:{bar_pct:.1f}%;background:{color}"></div>
            </div>
            <span class="rarity-stat"><b>{count}</b> item{'s' if count != 1 else ''} — <b>{format_price(value)}</b></span>
        </div>"""
    section += '</div></div>'

    # Categorize items
    priced_items = [it for it in items if item_value(it) > 0]

    if priced_items:
        knives_gloves = [it for it in priced_items if it.get("quality") == "★"]
        weapons = [it for it in priced_items
                   if it.get("quality") != "★"
                   and it.get("weapon")
                   and "Music Kit" not in it.get("name", "")
                   and "Sticker" not in it.get("type", "")
                   and "Graffiti" not in it.get("type", "")
                   and "Collectible" not in it.get("type", "")]
        other = [it for it in priced_items
                 if it not in knives_gloves and it not in weapons]

        def render_category(heading, item_list):
            if not item_list:
                return ""
            cards = "\n".join(generate_item_card(it) for it in item_list)
            return f'<div class="category"><h3>{heading}</h3><div class="item-grid">{cards}</div></div>'

        section += render_category("🗡️ Knives & Gloves", knives_gloves)
        section += render_category("🔫 Weapon Skins", weapons)
        section += render_category("🎵 Music Kits, Stickers & Other", other)

    # No-value items
    no_value_items = [it for it in items if item_value(it) == 0]
    if no_value_items:
        section += '<div class="category"><h3>📦 Other Items (No Market Value)</h3><div class="no-value-list">'
        for it in no_value_items:
            tradable = "✅" if it.get("tradable") else "🔒"
            img_url = it.get("image_url", "")
            img_tag = f'<img src="{img_url}" class="mini-img" />' if img_url else ""
            section += f'<div class="no-value-item">{tradable} {img_tag}<b>{it.get("name", "Unknown")}</b><span class="item-type">{it.get("type", "")}</span></div>'
        section += '</div></div>'

    section += '</section>'
    return section


def generate_html(data, input_file):
    """Generate the full HTML report."""
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    total_profiles = len(data)
    total_items = sum(p.get("total_items", len(p.get("items", []))) for p in data.values())
    grand_total = sum(p.get("estimated_value", 0) for p in data.values())

    sorted_profiles = sorted(data.items(), key=lambda kv: kv[1].get("estimated_value", 0), reverse=True)

    # Leaderboard
    leaderboard_html = ""
    if total_profiles > 1:
        medals = ["🥇", "🥈", "🥉"]
        rows = ""
        for i, (sid, prof) in enumerate(sorted_profiles):
            medal = medals[i] if i < len(medals) else f"#{i+1}"
            uname = prof.get("username") or sid
            val = prof.get("estimated_value", 0)
            count = prof.get("total_items", len(prof.get("items", [])))
            rows += f'<div class="leaderboard-row"><span class="medal">{medal}</span><span class="lb-name">{uname}</span><span class="lb-value">{format_price(val)}</span><span class="lb-count">{count} items</span></div>'
        leaderboard_html = f'<div class="leaderboard"><h3>🏆 Leaderboard</h3>{rows}</div>'

    # Profile sections
    profile_sections = ""
    for rank, (steam_id, profile) in enumerate(sorted_profiles, 1):
        profile_sections += generate_profile_section(steam_id, profile, rank)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Steam CS2 Inventory Report</title>
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
  header {{
    background: var(--header-bg);
    padding: 28px 32px;
    border-bottom: 2px solid var(--border);
    text-align: center;
  }}
  header h1 {{
    font-size: 2em;
    color: #fff;
    margin-bottom: 6px;
  }}
  header p {{
    color: #8f98a0;
    font-size: 0.9em;
  }}
  .container {{
    max-width: 1200px;
    margin: 0 auto;
    padding: 24px;
  }}

  /* ── Summary cards ── */
  .summary {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }}
  .summary-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    text-align: center;
  }}
  .summary-card .value {{
    font-size: 2em;
    font-weight: bold;
    color: #fff;
  }}
  .summary-card .label {{
    color: #8f98a0;
    font-size: 0.85em;
    margin-top: 4px;
  }}
  .accent-green {{ color: var(--accent-green); }}

  /* ── Leaderboard ── */
  .leaderboard {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 32px;
  }}
  .leaderboard h3 {{
    color: #fff;
    margin-bottom: 12px;
  }}
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

  /* ── Profile section ── */
  .profile-section {{
    margin-bottom: 40px;
  }}
  .profile-header {{
    background: var(--header-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
  }}
  .profile-header h2 {{
    color: #fff;
    font-size: 1.5em;
    margin-bottom: 8px;
  }}
  .profile-meta {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    color: #8f98a0;
    font-size: 0.9em;
  }}
  .profile-meta code {{
    background: rgba(0,0,0,0.3);
    padding: 2px 6px;
    border-radius: 3px;
    font-size: 0.9em;
  }}
  .alert {{
    background: rgba(255,200,0,0.1);
    border: 1px solid rgba(255,200,0,0.3);
    border-radius: 6px;
    padding: 12px 16px;
    color: #ffd700;
    font-style: italic;
  }}

  /* ── Rarity breakdown ── */
  .rarity-breakdown {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
  }}
  .rarity-breakdown h3 {{
    color: #fff;
    margin-bottom: 14px;
  }}
  .rarity-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 6px 0;
  }}
  .rarity-label {{
    min-width: 160px;
    font-weight: 600;
    font-size: 0.9em;
  }}
  .rarity-bar-track {{
    flex: 1;
    height: 8px;
    background: rgba(255,255,255,0.05);
    border-radius: 4px;
    overflow: hidden;
  }}
  .rarity-bar-fill {{
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s;
  }}
  .rarity-stat {{
    min-width: 200px;
    text-align: right;
    font-size: 0.85em;
    color: #8f98a0;
  }}

  /* ── Category ── */
  .category {{
    margin-bottom: 24px;
  }}
  .category h3 {{
    color: #fff;
    font-size: 1.2em;
    margin-bottom: 14px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }}

  /* ── Item cards ── */
  .item-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(500px, 1fr));
    gap: 16px;
  }}
  .item-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    overflow: hidden;
    transition: transform 0.2s, border-color 0.2s;
  }}
  .item-card:hover {{
    transform: translateY(-2px);
    border-color: var(--accent);
  }}
  .item-image {{
    width: 160px;
    min-height: 120px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0,0,0,0.2);
    flex-shrink: 0;
  }}
  .item-image img {{
    max-width: 140px;
    max-height: 105px;
    object-fit: contain;
  }}
  .no-image {{
    color: #555;
    font-size: 0.8em;
  }}
  .item-details {{
    flex: 1;
    padding: 14px 16px;
  }}
  .item-name {{
    color: #fff;
    font-size: 1em;
    margin-bottom: 4px;
  }}
  .rarity-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.72em;
    font-weight: 600;
    color: #fff;
    margin-bottom: 8px;
  }}
  .price-row {{
    margin-bottom: 8px;
  }}
  .price-main {{
    font-size: 1.3em;
    font-weight: bold;
    color: var(--accent-green);
    margin-right: 8px;
  }}
  .price-sub {{
    font-size: 0.8em;
    color: #8f98a0;
  }}
  .detail-row {{
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.85em;
    margin-bottom: 4px;
    flex-wrap: wrap;
  }}
  .detail-label {{
    color: #8f98a0;
    min-width: 55px;
  }}
  .detail-row code {{
    background: rgba(0,0,0,0.3);
    padding: 1px 6px;
    border-radius: 3px;
    font-size: 0.9em;
  }}

  /* ── Wear bar ── */
  .wear-bar-container {{
    width: 120px;
    height: 8px;
    background: rgba(255,255,255,0.08);
    border-radius: 4px;
    overflow: hidden;
  }}
  .wear-bar-fill {{
    height: 100%;
    background: linear-gradient(90deg, #a4d007, #ffd700, #ff4444);
    border-radius: 4px;
  }}
  .wear-value {{
    font-size: 0.8em;
    color: #8f98a0;
    font-family: monospace;
  }}

  /* ── Badges ── */
  .badge {{
    font-size: 0.8em;
    padding: 2px 8px;
    border-radius: 3px;
  }}
  .badge.tradable {{
    background: rgba(164,208,7,0.15);
    color: #a4d007;
  }}
  .badge.not-tradable {{
    background: rgba(255,68,68,0.15);
    color: #ff6666;
  }}
  .asset-id {{
    font-size: 0.8em;
    color: #555;
  }}

  /* ── No-value items ── */
  .no-value-list {{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}
  .no-value-item {{
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 0.9em;
  }}
  .no-value-item .mini-img {{
    width: 48px;
    height: 36px;
    object-fit: contain;
  }}
  .item-type {{
    color: #8f98a0;
    font-size: 0.85em;
    margin-left: auto;
  }}

  /* ── Footer ── */
  footer {{
    text-align: center;
    padding: 24px;
    color: #555;
    font-size: 0.8em;
    border-top: 1px solid var(--border);
    margin-top: 40px;
  }}

  @media (max-width: 700px) {{
    .item-grid {{ grid-template-columns: 1fr; }}
    .item-card {{ flex-direction: column; }}
    .item-image {{ width: 100%; min-height: 80px; }}
    .rarity-label {{ min-width: 100px; }}
    .rarity-stat {{ min-width: auto; }}
    header h1 {{ font-size: 1.4em; }}
  }}
</style>
</head>
<body>

<header>
  <h1>🎮 Steam CS2 Inventory Report</h1>
  <p>Generated on {now}</p>
</header>

<div class="container">
  <div class="summary">
    <div class="summary-card">
      <div class="value">{total_profiles}</div>
      <div class="label">Profiles Scanned</div>
    </div>
    <div class="summary-card">
      <div class="value">{total_items}</div>
      <div class="label">Total Items</div>
    </div>
    <div class="summary-card">
      <div class="value accent-green">{format_price(grand_total)}</div>
      <div class="label">Combined Value</div>
    </div>
  </div>

  {leaderboard_html}

  {profile_sections}
</div>

<footer>
  Report generated from <code>{input_file}</code> by steam_inventory_report_html.py
</footer>

</body>
</html>"""

    return html


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    output_file = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    with open(input_file) as f:
        data = json.load(f)

    html = generate_html(data, input_file)
    with open(output_file, "w") as f:
        f.write(html)

    size_kb = len(html) / 1024
    print(f"✅ Report written to {output_file} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
