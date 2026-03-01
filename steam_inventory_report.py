#!/usr/bin/env python3
"""Generate an elegant Markdown report from steam_inventory.json."""

import json
import sys
from collections import Counter
from datetime import datetime, timezone

INPUT_FILE = "data/steam_inventory.json"
OUTPUT_FILE = "reports/steam_inventory_report.md"

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


def wear_bar(wear):
    """Render a visual wear bar (0.0 = pristine, 1.0 = destroyed)."""
    if wear is None:
        return ""
    pct = int(wear * 20)  # 20 segments
    filled = "█" * pct
    empty = "░" * (20 - pct)
    return f"`{filled}{empty}` {wear:.4f}"


def format_price(price):
    """Format a price value."""
    if price is None:
        return "—"
    return f"${price:,.2f}"


def rarity_badge(rarity):
    """Return an emoji + rarity label."""
    emoji = RARITY_EMOJI.get(rarity, "❔")
    return f"{emoji} {rarity}"


def generate_profile_section(steam_id, profile, rank):
    """Generate the markdown section for a single profile."""
    lines = []
    username = profile.get("username") or steam_id
    items = profile.get("items", [])
    total_value = profile.get("estimated_value", 0)
    error = profile.get("error")

    # Profile header
    lines.append(f"## {'👤' if not error else '⚠️'} {username}")
    lines.append(f"> **Steam ID:** `{steam_id}` &nbsp;|&nbsp; "
                 f"**Items:** {len(items)} &nbsp;|&nbsp; "
                 f"**Estimated Value:** **{format_price(total_value)}**")
    lines.append("")

    if error:
        lines.append(f"> ⚠️ *Could not fetch inventory: {error}*")
        lines.append("")
        return lines

    if not items:
        lines.append("> *No items found (inventory may be empty or private).*")
        lines.append("")
        return lines

    # ── Rarity breakdown ──
    rarity_counts = Counter()
    rarity_values = Counter()
    for it in items:
        r = it.get("rarity", "Unknown")
        rarity_counts[r] += 1
        rarity_values[r] += item_value(it)

    lines.append("### Breakdown by Rarity")
    for rarity in sorted(rarity_counts, key=lambda r: rarity_values[r], reverse=True):
        count = rarity_counts[rarity]
        value = rarity_values[rarity]
        lines.append(f"- {rarity_badge(rarity)} — **{count}** item{'s' if count != 1 else ''}, "
                     f"worth **{format_price(value)}**")
    lines.append("")

    # ── High-value items (top items with price > $0) ──
    priced_items = [it for it in items if item_value(it) > 0]

    if priced_items:
        # Separate categories
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

        def render_item_table(heading, item_list):
            if not item_list:
                return
            lines.append(f"### {heading}")
            lines.append("")
            for it in item_list:
                val = item_value(it)
                price = it.get("market_price", {})
                lowest = format_price(price.get("lowest_price"))
                median = format_price(price.get("median_price"))
                ext = it.get("exterior", "")
                wear = it.get("wear_rating")
                rarity = it.get("rarity", "")
                tradable = "✅" if it.get("tradable") else "🔒"
                name = it.get("name", "Unknown")
                wear_short = WEAR_SHORT.get(ext, ext)

                # Build details lines
                img_url = it.get("image_url", "")
                detail_lines = []
                detail_lines.append(f"<strong>{name}</strong> &nbsp; {rarity_badge(rarity)}<br>")
                detail_lines.append(f"💰 <strong>{format_price(val)}</strong> &nbsp;(Low: {lowest} / Med: {median})<br>")
                if ext:
                    detail_lines.append(f"🎨 {ext}<br>")
                if wear is not None:
                    detail_lines.append(f"📊 Wear: <code>{'█' * int(wear * 20)}{'░' * (20 - int(wear * 20))}</code> {wear:.4f}<br>")
                if "pattern_template" in it:
                    detail_lines.append(f"🔢 Pattern: <code>{it['pattern_template']}</code><br>")
                detail_lines.append(f"{tradable} {'Tradable' if it.get('tradable') else 'Not Tradable'} &nbsp;|&nbsp; Asset: <code>{it.get('assetid', '—')}</code>")

                details_html = "\n".join(detail_lines)
                img_html = f'<img src="{img_url}" width="128" height="96" alt="{name}" />' if img_url else ""

                lines.append('<table><tr>')
                lines.append(f'<td>{details_html}</td>')
                if img_html:
                    lines.append(f'<td width="140" align="right">{img_html}</td>')
                lines.append('</tr></table>')
                lines.append("")

        render_item_table("🗡️ Knives & Gloves", knives_gloves)
        render_item_table("🔫 Weapon Skins", weapons)
        render_item_table("🎵 Music Kits, Stickers & Other", other)

    # ── Untradable / no-value items ──
    no_value_items = [it for it in items if item_value(it) == 0]
    if no_value_items:
        lines.append("### 📦 Other Items (No Market Value)")
        lines.append("")
        for it in no_value_items:
            tradable = "✅" if it.get("tradable") else "🔒"
            img_url = it.get("image_url", "")
            img_tag = f"<img src=\"{img_url}\" width=\"48\" height=\"36\" /> " if img_url else ""
            lines.append(f"- {tradable} {img_tag}**{it.get('name', 'Unknown')}** — {it.get('type', '')}")
        lines.append("")

    lines.append("---")
    lines.append("")
    return lines


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    output_file = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    with open(input_file) as f:
        data = json.load(f)

    lines = []

    # ── Title ──
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    lines.append("# 🎮 Steam CS2 Inventory Report")
    lines.append(f"*Generated on {now}*")
    lines.append("")

    # ── Grand summary ──
    total_profiles = len(data)
    total_items = sum(p.get("total_items", len(p.get("items", []))) for p in data.values())
    grand_total = sum(p.get("estimated_value", 0) for p in data.values())

    # Sort profiles by value descending
    sorted_profiles = sorted(data.items(), key=lambda kv: kv[1].get("estimated_value", 0), reverse=True)

    lines.append("## 📊 Summary")
    lines.append("")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **Profiles Scanned** | {total_profiles} |")
    lines.append(f"| **Total Items** | {total_items} |")
    lines.append(f"| **Combined Value** | **{format_price(grand_total)}** |")
    lines.append("")

    # Leaderboard
    if total_profiles > 1:
        lines.append("### 🏆 Leaderboard")
        lines.append("")
        medals = ["🥇", "🥈", "🥉"]
        for i, (sid, prof) in enumerate(sorted_profiles):
            medal = medals[i] if i < len(medals) else f"**{i+1}.**"
            uname = prof.get("username") or sid
            val = prof.get("estimated_value", 0)
            count = prof.get("total_items", len(prof.get("items", [])))
            lines.append(f"{medal} **{uname}** — {format_price(val)} ({count} items)")
        lines.append("")

    lines.append("---")
    lines.append("")

    # ── Per-profile sections ──
    for rank, (steam_id, profile) in enumerate(sorted_profiles, 1):
        lines.extend(generate_profile_section(steam_id, profile, rank))

    # ── Footer ──
    lines.append("---")
    lines.append(f"*Report generated from `{input_file}` by steam_inventory_report.py*")

    md = "\n".join(lines) + "\n"
    with open(output_file, "w") as f:
        f.write(md)

    print(f"✅ Report written to {output_file} ({len(md):,} bytes)")


if __name__ == "__main__":
    main()
