#!/usr/bin/env python3
"""Generate a combined Steam CS2 dashboard with tabbed navigation.

Merges the inventory report and price charts into a single HTML page
with two views toggled by navigation tabs.
"""

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta

# ── Config ──────────────────────────────────────────────────────────────────
INPUT_FILE = "data/steam_inventory.json"
PRICE_DATA_FILE = "data/steam_price_history.json"
OUTPUT_FILE = "reports/steam_dashboard.html"
PORTFOLIO_HISTORY_FILE = "data/portfolio_history.json"
CASE_DATA_FILE = "data/case_price_history.json"
CASE_META_FILE = "data/case_price_history_meta.json"

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

TRADEUP_RARITY_ORDER = [
    "Consumer Grade", "Industrial Grade", "Mil-Spec Grade",
    "Restricted", "Classified", "Covert",
]


# ═══════════════════════════════════════════════════════════════════════════
#  Sell Signals & Trade-Up helpers
# ═══════════════════════════════════════════════════════════════════════════

def build_sell_signals(items, price_data):
    """Compute 52-week sell signals for each item with price history."""
    now_ms       = datetime.now(timezone.utc).timestamp() * 1000
    year_ago_ms  = (datetime.now(timezone.utc) - timedelta(days=365)).timestamp() * 1000
    month_ago_ms = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp() * 1000
    signals = []
    for name, meta in items.items():
        history = price_data.get(name, [])
        if not history:
            continue
        current_price = (meta.get("market_price") or {}).get("lowest_price")
        if not current_price:
            continue
        year_hist   = [d for d in history if d[0] >= year_ago_ms] or history
        year_high   = max(d[1] for d in year_hist)
        year_low    = min(d[1] for d in year_hist)
        pct_of_high = round(current_price / year_high * 100, 1) if year_high else 0
        month_hist  = [d for d in history if d[0] >= month_ago_ms]
        trend_30d   = 0.0
        if len(month_hist) >= 2:
            trend_30d = round(
                (month_hist[-1][1] - month_hist[0][1]) / month_hist[0][1] * 100, 1
            )
        signals.append({
            "name":          name,
            "image_url":     meta.get("image_url", ""),
            "rarity":        meta.get("rarity", ""),
            "rarity_color":  meta.get("rarity_color", ""),
            "exterior":      meta.get("exterior", ""),
            "current_price": current_price,
            "year_high":     round(year_high, 2),
            "year_low":      round(year_low, 2),
            "pct_of_high":   pct_of_high,
            "trend_30d":     trend_30d,
        })
    signals.sort(key=lambda s: s["pct_of_high"], reverse=True)
    return signals


def build_tradeup_data(data):
    """Build inventory items grouped by rarity for the trade-up calculator."""
    by_rarity = {r: [] for r in TRADEUP_RARITY_ORDER}
    for profile in data.values():
        for item in profile.get("items", []):
            if not item.get("marketable"):
                continue
            rarity = item.get("rarity", "")
            if rarity not in by_rarity:
                continue
            mp    = item.get("market_price") or {}
            price = mp.get("lowest_price") or mp.get("median_price")
            if not price:
                continue
            by_rarity[rarity].append({
                "name":        item["name"],
                "assetid":     item["assetid"],
                "exterior":    item.get("exterior", ""),
                "wear_rating": item.get("wear_rating"),
                "price":       price,
                "image_url":   item.get("image_url", ""),
                "rarity_color": item.get("rarity_color", ""),
            })
    return {r: v for r, v in by_rarity.items() if v}


def linear_regression_predict(history, days_ahead=30, lookback_days=90):
    """Linear regression on recent price history; returns predicted price in days_ahead days."""
    if not history:
        return None
    now_ms      = datetime.now(timezone.utc).timestamp() * 1000
    lookback_ms = lookback_days * 86_400_000
    recent = [d for d in history if d[0] >= now_ms - lookback_ms]
    if len(recent) < 5:
        recent = history if len(history) >= 5 else None
    if not recent or len(recent) < 5:
        return None
    t0 = recent[0][0]
    xs = [(d[0] - t0) / 86_400_000 for d in recent]   # days from first point
    ys = [d[1] for d in recent]
    n  = len(xs)
    xm, ym = sum(xs) / n, sum(ys) / n
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    if den == 0:
        return None
    slope     = num / den
    intercept = ym - slope * xm
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - ym) ** 2 for y in ys)
    r2 = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else 0.0
    pred_price = slope * (xs[-1] + days_ahead) + intercept
    current    = ys[-1]
    change_pct = round((pred_price - current) / current * 100, 1) if current > 0 else 0
    return {
        "predicted_price": round(max(0.01, pred_price), 2),
        "change_pct":      change_pct,
        "r2":              r2,
    }


def build_price_predictions(items, price_data):
    """Build 30-day linear regression predictions for all items with sufficient history."""
    predictions = []
    for name, meta in items.items():
        history       = price_data.get(name, [])
        current_price = (meta.get("market_price") or {}).get("lowest_price")
        if not history or not current_price:
            continue
        pred = linear_regression_predict(history)
        if pred is None:
            continue
        predictions.append({
            "name":            name,
            "image_url":       meta.get("image_url", ""),
            "rarity":          meta.get("rarity", ""),
            "rarity_color":    meta.get("rarity_color", ""),
            "exterior":        meta.get("exterior", ""),
            "current_price":   current_price,
            "predicted_price": pred["predicted_price"],
            "change_pct":      pred["change_pct"],
            "r2":              pred["r2"],
        })
    predictions.sort(key=lambda p: p["change_pct"], reverse=True)
    return predictions


def build_case_investment_data(case_history, case_meta):
    """Build case investment data with appreciation stats."""
    if not case_history:
        return []
    now_ms      = datetime.now(timezone.utc).timestamp() * 1000
    year_ms     = 365 * 86_400_000
    month_ms_90 = 90  * 86_400_000
    cases = []
    for name, history in case_history.items():
        if not history or len(history) < 2:
            continue
        meta   = case_meta.get(name, {})
        status = meta.get("status", "active")
        prices = [d[1] for d in history]
        first_price = history[0][1]
        last_price  = history[-1][1]
        ath = max(prices)
        atl = min(prices)
        all_time_chg = round((last_price - first_price) / first_price * 100, 1) if first_price > 0 else 0
        # 1-year change
        year_hist = [d for d in history if d[0] >= now_ms - year_ms]
        yr_chg = 0.0
        if year_hist and year_hist[0][1] > 0:
            yr_chg = round((last_price - year_hist[0][1]) / year_hist[0][1] * 100, 1)
        # 90-day change
        q_hist = [d for d in history if d[0] >= now_ms - month_ms_90]
        q_chg = 0.0
        if q_hist and q_hist[0][1] > 0:
            q_chg = round((last_price - q_hist[0][1]) / q_hist[0][1] * 100, 1)
        avg_vol = round(sum(d[2] for d in history[-30:]) / max(len(history[-30:]), 1))
        cases.append({
            "name":         name,
            "status":       status,
            "current":      round(last_price, 2),
            "ath":          round(ath, 2),
            "atl":          round(atl, 2),
            "all_time_chg": all_time_chg,
            "yr_chg":       yr_chg,
            "q_chg":        q_chg,
            "avg_vol":      avg_vol,
            "history":      history,
        })
    cases.sort(key=lambda c: c["all_time_chg"], reverse=True)
    return cases


def build_concentration_data(data):
    """Compute portfolio concentration metrics (HHI, per-item %, by-rarity %)."""
    items_flat = []
    by_rarity  = {}
    total      = 0.0
    for profile in data.values():
        for item in profile.get("items", []):
            val = item_value(item)
            if val == 0:
                continue
            total += val
            items_flat.append({
                "name":      item.get("name", "Unknown"),
                "value":     val,
                "image_url": item.get("image_url", ""),
                "rarity":    item.get("rarity", "Unknown"),
            })
            r = item.get("rarity", "Unknown")
            by_rarity[r] = by_rarity.get(r, 0.0) + val
    if total == 0:
        return {}
    items_flat.sort(key=lambda x: x["value"], reverse=True)
    for it in items_flat:
        it["pct"]   = round(it["value"] / total * 100, 1)
        it["value"] = round(it["value"], 2)
    hhi      = round(sum(it["pct"] ** 2 for it in items_flat), 1)
    top1_pct = items_flat[0]["pct"] if items_flat else 0
    top3_pct = round(sum(it["pct"] for it in items_flat[:3]), 1)
    risk     = ("High"   if (hhi > 2500 or top1_pct > 25) else
                "Medium" if (hhi > 1000 or top1_pct > 12) else "Low")
    by_rar_list = sorted(
        [{"rarity": r, "value": round(v, 2), "pct": round(v / total * 100, 1),
          "color": RARITY_COLOR.get(r, "#888888")}
         for r, v in by_rarity.items()],
        key=lambda x: x["value"], reverse=True,
    )
    return {
        "total":      round(total, 2),
        "hhi":        hhi,
        "risk_level": risk,
        "top1_pct":   top1_pct,
        "top3_pct":   top3_pct,
        "items":      items_flat[:20],
        "by_rarity":  by_rar_list,
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


def _profile_extra_html(steam_id, profile):
    """Build optional HTML for enriched profile data (avatar, status, etc.)."""
    p = profile.get("profile", {})
    if not p:
        return "", ""
    avatar = p.get("avatar_url", "")
    url = p.get("profile_url", "")
    state = p.get("persona_state_text", "Offline")
    state_cls = "online" if p.get("persona_state", 0) in (1, 5, 6) else "offline"
    game = p.get("game_name", "")
    country = p.get("country_code", "")
    created = p.get("time_created")
    age_str = ""
    if created:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(created, tz=timezone.utc)
        years = round((datetime.now(timezone.utc) - dt).days / 365.25, 1)
        age_str = f'<span>Account Age: <b>{years} yrs</b> (since {dt.strftime("%b %Y")})</span>'
    avatar_html = (
        f'<a href="{url}" target="_blank" class="profile-avatar-link">'
        f'<img src="{avatar}" class="profile-avatar" alt="" />'
        f'<span class="status-dot {state_cls}"></span></a>'
    ) if avatar else ""
    extra_meta = ""
    if game:
        extra_meta += f'<span class="in-game">🎮 In-Game: <b>{game}</b></span>'
    elif state:
        extra_meta += f'<span class="persona-state {state_cls}">● {state}</span>'
    if country:
        extra_meta += f'<span>🌍 {country}</span>'
    if age_str:
        extra_meta += age_str
    if url:
        extra_meta += f'<a href="{url}" target="_blank" class="profile-link">View Profile ↗</a>'
    return avatar_html, extra_meta


def generate_profile_section(steam_id, profile, rank):
    username = profile.get("username") or steam_id
    items = profile.get("items", [])
    total_value = profile.get("estimated_value", 0)
    error = profile.get("error")
    avatar_html, extra_meta = _profile_extra_html(steam_id, profile)

    section = f"""<section class="profile-section" data-steam-id="{steam_id}">
    <div class="profile-header">
        {avatar_html}
        <div class="profile-header-info">
            <h2>{'⚠️' if error else ''} {username}</h2>
            <div class="profile-meta">
                <span>Steam ID: <code>{steam_id}</code></span>
                <span>Items: <b>{len(items)}</b></span>
                <span>Estimated Value: <b class="accent-green">{format_price(total_value)}</b></span>
                {extra_meta}
            </div>
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
            avatar = prof.get("profile", {}).get("avatar_url", "")
            av_tag = f'<img src="{avatar}" class="lb-avatar" alt="" />' if avatar else ''
            rows += (f'<div class="leaderboard-row"><span class="medal">{medal}</span>'
                     f'{av_tag}'
                     f'<span class="lb-name">{uname}</span>'
                     f'<span class="lb-value">{format_price(val)}</span>'
                     f'<span class="lb-count">{count} items</span></div>')
        leaderboard = f'<div class="leaderboard"><h3>🏆 Leaderboard</h3>{rows}</div>'

    profiles_html = ""
    for rank, (steam_id, profile) in enumerate(sorted_profiles, 1):
        profiles_html += generate_profile_section(steam_id, profile, rank)

    return f"""
    <div class="inventory-summary" id="inventorySummary">
      <div class="summary">
        <div class="summary-card"><div class="value">{total_profiles}</div><div class="label">Profiles Scanned</div></div>
        <div class="summary-card"><div class="value">{total_items}</div><div class="label">Total Items</div></div>
        <div class="summary-card"><div class="value accent-green">{format_price(grand_total)}</div><div class="label">Combined Value</div></div>
      </div>
      {leaderboard}
    </div>
    {profiles_html}"""


# ═══════════════════════════════════════════════════════════════════════════
#  Price Charts tab helpers  (from steam_price_charts.py)
# ═══════════════════════════════════════════════════════════════════════════

def get_unique_items(data):
    """Get unique items across all profiles, including non-marketable (trade-locked).
    
    Non-marketable items still have market prices looked up by name,
    so they can appear in charts, sell signals, and predictions.
    """
    items = {}
    for profile in data.values():
        for item in profile.get("items", []):
            name = item.get("name", "Unknown")
            if name == "Unknown" or name in items:
                continue
            items[name] = {
                "name": name,
                "type": item.get("type", ""),
                "rarity": item.get("rarity", ""),
                "rarity_color": item.get("rarity_color", "ffffff"),
                "image_url": item.get("image_url", ""),
                "exterior": item.get("exterior", ""),
                "market_price": item.get("market_price", {}),
                "marketable": item.get("marketable", False),
            }
    return items


def build_charts_data(items, price_data):
    """Build the JSON-serializable chart data for the Price Charts tab.
    
    History arrays are NOT embedded here — they live in a single global
    PRICE_HISTORIES dict to avoid duplicating them per-account.
    Includes non-marketable (trade-locked) items with a locked flag.
    """
    chart_items = []
    for name, meta in items.items():
        if name not in price_data or not price_data[name]:
            continue
        entry = {
            "name": name,
            "type": meta.get("type", ""),
            "rarity": meta.get("rarity", ""),
            "rarity_color": meta.get("rarity_color", "ffffff"),
            "image_url": meta.get("image_url", ""),
            "exterior": meta.get("exterior", ""),
            "current_price": (meta.get("market_price") or {}).get("lowest_price"),
        }
        if not meta.get("marketable"):
            entry["locked"] = True
        chart_items.append(entry)
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


def downsample_history(history, recent_days=90, max_old_points_per_week=1):
    """Downsample price history for dashboard embedding.
    
    Keeps full resolution for the last `recent_days` days.
    For older data, keeps one point per week (the last point in each week).
    This typically reduces 5000-point histories to ~500 while preserving
    all detail in short time-range views (1W, 1M, 3M).
    """
    if not history or len(history) <= 500:
        return history
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    cutoff_ms = now_ms - recent_days * 86_400_000

    old_points = [d for d in history if d[0] < cutoff_ms]
    recent_points = [d for d in history if d[0] >= cutoff_ms]

    # Downsample old data: one point per week
    sampled = []
    if old_points:
        week_ms = 7 * 86_400_000
        current_week = old_points[0][0] // week_ms
        week_last = old_points[0]
        for pt in old_points:
            w = pt[0] // week_ms
            if w != current_week:
                sampled.append(week_last)
                current_week = w
            week_last = pt
        sampled.append(week_last)  # last point of final week

    return sampled + recent_points


# ═══════════════════════════════════════════════════════════════════════════
#  Combined HTML generation
# ═══════════════════════════════════════════════════════════════════════════

def generate_dashboard(data, price_data, input_file, portfolio_history=None,
                       case_history=None, case_meta=None):
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    # ── Build per-account + combined data for the account switcher ──
    profiles_meta = {sid: prof.get("username") or sid for sid, prof in data.items()}

    def _build_account_data(d):
        """Build all tab data dicts for a given data subset."""
        itms = get_unique_items(d)
        return {
            "charts":        build_charts_data(itms, price_data),
            "signals":       build_sell_signals(itms, price_data),
            "tradeup":       build_tradeup_data(d),
            "concentration": build_concentration_data(d),
            "predictions":   build_price_predictions(itms, price_data),
        }

    account_data = {"all": _build_account_data(data)}
    for steam_id in data:
        account_data[steam_id] = _build_account_data({steam_id: data[steam_id]})

    account_data_json = json.dumps(account_data)
    profiles_meta_json = json.dumps(profiles_meta)
    portfolio_json = json.dumps(portfolio_history or [])

    # Price histories stored ONCE globally (not duplicated per-account)
    # Downsample to reduce HTML size while preserving recent detail
    price_histories = {name: downsample_history(hist)
                       for name, hist in price_data.items() if hist}
    price_histories_json = json.dumps(price_histories)

    case_invest_data = build_case_investment_data(case_history or {}, case_meta or {})
    # Separate case histories from case metadata to avoid large inline arrays
    case_histories = {}
    for c in case_invest_data:
        case_histories[c["name"]] = downsample_history(c.pop("history", []))
    cases_json = json.dumps(case_invest_data)
    case_histories_json = json.dumps(case_histories)
    inventory_content = build_inventory_content(data)
    charts_content = build_charts_content()

    # Build account switcher HTML (avatar buttons instead of <select>)
    profiles_extra = {}
    for sid, prof in data.items():
        profiles_extra[sid] = prof.get("profile", {})
    profiles_extra_json = json.dumps(profiles_extra)

    account_switcher_items = ''
    for sid in data:
        uname = profiles_meta.get(sid, sid)
        avatar = data[sid].get("profile", {}).get("avatar_url", "")
        state = data[sid].get("profile", {}).get("persona_state", 0)
        state_cls = "online" if state in (1, 5, 6) else "offline"
        if avatar:
            account_switcher_items += (
                f'<button class="acct-btn" data-account="{sid}" title="{uname}">'
                f'<img src="{avatar}" alt="{uname}" />'
                f'<span class="acct-dot {state_cls}"></span></button>'
            )
        else:
            account_switcher_items += (
                f'<button class="acct-btn" data-account="{sid}" title="{uname}">'
                f'<span class="acct-initial">{uname[0].upper()}</span></button>'
            )

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
    line-height: 1;
    margin-bottom: 6px;
  }}
  .brand-title {{
    font-size: 1.05em;
    color: #c6d4df;
    font-weight: 700;
  }}
  header .timestamp {{
    color: #8f98a0;
    font-size: 0.85em;
  }}
  .header-right {{
    display: flex;
    align-items: center;
    gap: 16px;
  }}
  /* ── Account Switcher (avatar buttons) ── */
  .acct-switcher {{
    display: flex;
    align-items: center;
    gap: 6px;
  }}
  .acct-btn {{
    position: relative;
    width: 38px; height: 38px;
    border-radius: 50%;
    border: 2px solid var(--border);
    background: var(--card-bg);
    cursor: pointer;
    padding: 0;
    overflow: hidden;
    transition: all 0.2s;
    color: #8f98a0;
    font-size: 0.65em;
    font-weight: 700;
  }}
  .acct-btn img {{
    width: 100%; height: 100%;
    object-fit: cover;
    display: block;
  }}
  .acct-btn:hover {{
    border-color: var(--accent);
    transform: scale(1.1);
  }}
  .acct-btn.active {{
    border-color: var(--accent);
    box-shadow: 0 0 0 2px rgba(102,192,244,0.3);
  }}
  .acct-btn.acct-all {{
    font-size: 0.6em;
    font-weight: 800;
    color: #8f98a0;
    letter-spacing: 0.03em;
  }}
  .acct-btn.acct-all.active {{ color: var(--accent); }}
  .acct-dot, .status-dot {{
    position: absolute;
    bottom: 0; right: 0;
    width: 10px; height: 10px;
    border-radius: 50%;
    border: 2px solid var(--header-bg);
  }}
  .acct-dot.online, .status-dot.online {{ background: #a4d007; }}
  .acct-dot.offline, .status-dot.offline {{ background: #8f98a0; }}
  .acct-initial {{
    display: flex; align-items: center; justify-content: center;
    width: 100%; height: 100%;
    font-size: 1.6em;
    color: #c6d4df;
  }}

  /* ── Enriched profile header ── */
  .profile-header {{
    display: flex;
    align-items: center;
    gap: 16px;
  }}
  .profile-avatar-link {{
    position: relative;
    flex-shrink: 0;
  }}
  .profile-avatar {{
    width: 64px; height: 64px;
    border-radius: 50%;
    border: 2px solid var(--border);
    object-fit: cover;
  }}
  .profile-header-info {{ flex: 1; min-width: 0; }}
  .profile-meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px 16px;
    align-items: center;
    font-size: 0.85em;
    color: #8f98a0;
    margin-top: 4px;
  }}
  .persona-state.online {{ color: #a4d007; }}
  .in-game {{ color: #90ba3c; }}
  .profile-link {{
    color: var(--accent);
    text-decoration: none;
    font-size: 0.85em;
  }}
  .profile-link:hover {{ text-decoration: underline; }}
  .lb-avatar {{
    width: 28px; height: 28px;
    border-radius: 50%;
    object-fit: cover;
    border: 1px solid var(--border);
    vertical-align: middle;
    margin-right: 4px;
  }}
  .tab-nav {{
    display: flex;
    gap: 0;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }}
  .tab-nav::-webkit-scrollbar {{ display: none; }}
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
    white-space: nowrap;
    flex-shrink: 0;
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
  .chart-card.trade-locked {{ border-color: rgba(255,150,0,0.3); }}
  .lock-badge {{
    display: inline-block; padding: 2px 8px; border-radius: 3px;
    font-size: 0.72em; font-weight: 600;
    background: rgba(255,150,0,0.15); color: #ff9600;
    margin-top: 4px;
  }}

  /* ── Footer ── */
  footer {{
    text-align: center; padding: 24px;
    color: #555; font-size: 0.8em;
    border-top: 1px solid var(--border); margin-top: 40px;
  }}

  /* ── Sell Signals tab ── */
  .signal-grid {{ display: flex; flex-direction: column; gap: 10px; }}
  .signal-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    display: flex; align-items: center; gap: 14px; padding: 14px 16px; transition: border-color 0.2s;
  }}
  .signal-card:hover {{ border-color: var(--accent); }}
  .signal-card img {{ width: 72px; height: 54px; object-fit: contain; flex-shrink: 0; background: rgba(0,0,0,0.2); border-radius: 4px; }}
  .signal-info {{ flex: 1; min-width: 0; }}
  .signal-name {{ color: #fff; font-weight: 600; font-size: 0.95em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .signal-prices {{ font-size: 0.78em; color: #8f98a0; margin: 3px 0 6px; }}
  .year-bar-track {{ height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px; overflow: hidden; }}
  .year-bar-fill {{ height: 100%; border-radius: 4px; }}
  .signal-meta {{ display: flex; gap: 12px; font-size: 0.78em; margin-top: 5px; align-items: center; flex-wrap: wrap; }}
  .signal-badge {{ padding: 2px 10px; border-radius: 12px; font-size: 0.76em; font-weight: 600; white-space: nowrap; }}
  .badge-hot     {{ background: rgba(255,100,0,0.2);  color: #ff6400; }}
  .badge-good    {{ background: rgba(164,208,7,0.2);  color: #a4d007; }}
  .badge-low     {{ background: rgba(255,68,68,0.2);  color: #ff4444; }}
  .badge-neutral {{ background: rgba(143,152,160,0.12); color: #8f98a0; }}

  /* ── Trade-Up Calculator ── */
  .tu-rarity-tabs {{ display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 16px; }}
  .tu-rarity-tabs button {{
    background: var(--card-bg); color: #8f98a0; border: 1px solid var(--border);
    padding: 6px 16px; border-radius: 4px; cursor: pointer; font-size: 0.85em; transition: all 0.2s;
  }}
  .tu-rarity-tabs button.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .tu-basket {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
  .tu-basket-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }}
  .tu-basket-header h3 {{ color: #fff; margin: 0; font-size: 1em; }}
  .basket-slots {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
  .basket-slot {{
    width: 72px; height: 58px; border: 1px dashed var(--border); border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.68em; color: #555; overflow: hidden; position: relative;
  }}
  .basket-slot.filled {{ border-style: solid; border-color: var(--accent); cursor: pointer; }}
  .basket-slot img {{ width: 100%; height: 100%; object-fit: contain; }}
  .basket-slot .rm {{ position: absolute; top: 1px; right: 3px; color: #ff4444; font-size: 0.85em; font-weight: bold; line-height: 1; }}
  .tu-summary {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.88em; align-items: center; }}
  .tu-summary span {{ color: #8f98a0; }}
  .tu-summary b {{ color: #fff; }}
  .tu-profit-pos {{ color: #a4d007 !important; font-weight: bold; }}
  .tu-profit-neg {{ color: #ff4444 !important; font-weight: bold; }}
  .tu-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; margin-bottom: 20px; }}
  .tu-item {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 6px; cursor: pointer; transition: all 0.15s; text-align: center; user-select: none;
  }}
  .tu-item:hover {{ border-color: var(--accent); transform: translateY(-1px); }}
  .tu-item.selected {{ border-color: #a4d007; background: rgba(164,208,7,0.07); }}
  .tu-item.maxed {{ opacity: 0.38; cursor: not-allowed; pointer-events: none; }}
  .tu-item img {{ width: 80px; height: 60px; object-fit: contain; }}
  .tu-item .ti-name {{ font-size: 0.7em; color: #c6d4df; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .tu-item .ti-ext {{ font-size: 0.65em; color: #8f98a0; }}
  .tu-item .ti-price {{ font-size: 0.76em; color: #a4d007; font-weight: 600; }}
  .tu-next {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
  .tu-next h3 {{ color: #8f98a0; font-size: 0.9em; margin-bottom: 10px; }}
  .btn-sm {{ background: rgba(255,68,68,0.12); color: #ff6666; border: 1px solid rgba(255,68,68,0.25); padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 0.8em; }}
  .btn-sm:hover {{ background: rgba(255,68,68,0.22); }}

  /* ── Concentration Risk ── */
  .conc-section {{ margin-bottom: 24px; }}
  .conc-section h3 {{ color: #c6d4df; margin-bottom: 10px; font-size: 0.95em; text-transform: uppercase; letter-spacing: 0.05em; }}
  .conc-row {{ display: flex; align-items: center; gap: 10px; padding: 4px 0; }}
  .conc-img {{ width: 48px; height: 36px; object-fit: contain; flex-shrink: 0; background: rgba(0,0,0,0.2); border-radius: 3px; }}
  .conc-name {{ min-width: 200px; max-width: 200px; font-size: 0.8em; color: #c6d4df; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .conc-bar-track {{ flex: 1; height: 8px; background: rgba(255,255,255,0.06); border-radius: 4px; overflow: hidden; }}
  .conc-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s ease; }}
  .conc-pct {{ min-width: 48px; text-align: right; font-size: 0.82em; font-weight: 600; }}
  .conc-val {{ min-width: 76px; text-align: right; font-size: 0.78em; color: #8f98a0; }}
  .risk-badge {{ display: inline-block; padding: 4px 14px; border-radius: 12px; font-size: 0.85em; font-weight: 700; }}
  .risk-high   {{ background: rgba(255,68,68,0.18); color: #ff4444; border: 1px solid rgba(255,68,68,0.3); }}
  .risk-medium {{ background: rgba(255,215,0,0.15); color: #ffd700; border: 1px solid rgba(255,215,0,0.3); }}
  .risk-low    {{ background: rgba(164,208,7,0.15); color: #a4d007; border: 1px solid rgba(164,208,7,0.3); }}

  /* ── Predictions tab ── */
  .pred-disclaimer {{ background: rgba(255,215,0,0.07); border: 1px solid rgba(255,215,0,0.2); border-radius: 6px; padding: 10px 14px; font-size: 0.82em; color: #c8b400; margin-bottom: 20px; }}
  .pred-grid {{ display: flex; flex-direction: column; gap: 10px; }}
  .pred-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; display: flex; align-items: center; gap: 14px; padding: 12px 16px; transition: border-color 0.2s; }}
  .pred-card:hover {{ border-color: var(--accent); }}
  .pred-card img {{ width: 72px; height: 54px; object-fit: contain; flex-shrink: 0; background: rgba(0,0,0,0.2); border-radius: 4px; }}
  .pred-info {{ flex: 1; min-width: 0; }}
  .pred-name {{ color: #fff; font-weight: 600; font-size: 0.92em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .pred-prices {{ font-size: 0.78em; color: #8f98a0; margin-top: 4px; }}
  .pred-change {{ font-size: 1.2em; font-weight: bold; min-width: 90px; text-align: center; }}
  .pred-up   {{ color: #a4d007; }}
  .pred-down {{ color: #ff4444; }}
  .pred-r2 {{ font-size: 0.78em; color: #8f98a0; min-width: 70px; text-align: right; }}

  /* ── Cases tab ── */
  .case-filters {{ display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }}
  .case-filters button {{
    background: rgba(102,192,244,0.08); color: #8f98a0; border: 1px solid var(--border);
    padding: 6px 16px; border-radius: 16px; cursor: pointer; font-size: 0.82em; font-weight: 600; transition: all 0.2s;
  }}
  .case-filters button:hover {{ color: #fff; border-color: var(--accent); }}
  .case-filters button.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .case-card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px; transition: border-color 0.2s; margin-bottom: 12px;
  }}
  .case-card:hover {{ border-color: var(--accent); }}
  .case-top {{ display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }}
  .case-top .case-name {{ flex: 1; color: #fff; font-weight: 600; font-size: 0.95em; }}
  .case-top .case-price {{ color: #a4d007; font-weight: bold; font-size: 1.1em; }}
  .status-pill {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 0.72em; font-weight: 600; margin-left: 8px; }}
  .status-discontinued {{ background: rgba(255,68,68,0.15); color: #ff6666; }}
  .status-rare {{ background: rgba(255,215,0,0.15); color: #ffd700; }}
  .status-active {{ background: rgba(164,208,7,0.12); color: #a4d007; }}
  .case-stats {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 0.8em; color: #8f98a0; margin-bottom: 10px; }}
  .case-stats b {{ color: #fff; }}
  .case-chart-wrap {{ height: 180px; }}
  .case-chart-wrap canvas {{ width: 100% !important; height: 100% !important; }}

  /* ── Portfolio tab styles ── */
  .portfolio-chart-wrap {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 20px;
  }}
  .portfolio-chart-wrap canvas {{
    width: 100% !important;
    height: 400px !important;
  }}

  @media (max-width: 700px) {{
    header {{ padding: 12px 12px 0; }}
    .header-top {{ flex-direction: column; align-items: flex-start; gap: 8px; margin-bottom: 10px; }}
    .header-right {{ width: 100%; justify-content: space-between; }}
    header h1 {{ font-size: 1.3em; }}
    .brand-title {{ font-size: 0.95em; }}
    .tab-nav button {{ padding: 8px 12px; font-size: 0.8em; }}
    .chart-grid {{ grid-template-columns: 1fr; }}
    .item-grid {{ grid-template-columns: 1fr; }}
    .item-card {{ flex-direction: column; }}
    .item-image {{ width: 100%; min-height: 80px; }}
    .rarity-label {{ min-width: 100px; }}
    .rarity-stat {{ min-width: auto; }}
    .container {{ padding: 12px; }}
    .acct-btn {{ width: 32px; height: 32px; }}
    .profile-avatar {{ width: 48px; height: 48px; }}
    .conc-name {{ min-width: 120px; max-width: 120px; }}
  }}
</style>
</head>
<body>

<header>
  <div class="header-top">
    <div class="header-left">
      <h1>▄︻テ══━一💥</h1>
      <div class="brand-title">CS2 Capital Management LLC</div>
      <p>You call it gambling. We call it asymmetric payoff structures.</p>
    </div>
    <div class="header-right">
      <div class="acct-switcher" id="accountSwitcher">
        <button class="acct-btn acct-all active" data-account="all" title="All Accounts">ALL</button>
        {account_switcher_items}
      </div>
      <span class="timestamp">Generated on {now}</span>
    </div>
  </div>
  <nav class="tab-nav">
    <button class="active" data-tab="inventory">📦 Inventory</button>
    <button data-tab="charts">📈 Price Charts</button>
    <button data-tab="portfolio">📊 Portfolio</button>
    <button data-tab="signals">💡 Sell Signals</button>
    <button data-tab="tradeup">🔄 Trade-Up</button>
    <button data-tab="predictions">📉 Predictions</button>
    <button data-tab="cases">📦 Cases</button>
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

<!-- ── Sell Signals Tab ── -->
<div class="tab-panel" id="tab-signals">
  <div class="container">
    <div id="signalsContent"></div>
  </div>
</div>

<!-- ── Trade-Up Tab ── -->
<div class="tab-panel" id="tab-tradeup">
  <div class="container">
    <div id="tradeupContent"></div>
  </div>
</div>

<!-- ── Portfolio Tab ── -->
<div class="tab-panel" id="tab-portfolio">
  <div class="container">
    <div class="summary" id="portfolioSummary"></div>
    <div class="portfolio-chart-wrap">
      <canvas id="portfolioChart"></canvas>
    </div>
    <div id="concentrationSection" style="margin-top:32px"></div>
  </div>
</div>

<!-- ── Predictions Tab ── -->
<div class="tab-panel" id="tab-predictions">
  <div class="container">
    <div id="predictionsContent"></div>
  </div>
</div>

<!-- ── Cases Tab ── -->
<div class="tab-panel" id="tab-cases">
  <div class="container">
    <div id="casesContent"></div>
  </div>
</div>

<footer>
  Report generated from <code>{input_file}</code> by steam_dashboard.py
</footer>

<div id="js-error-banner" style="display:none;background:#ff4444;color:#fff;padding:12px 20px;font-family:monospace;font-size:14px;position:fixed;bottom:0;left:0;right:0;z-index:9999"></div>
<script>
window.onerror = function(msg, url, line, col) {{
  var el = document.getElementById('js-error-banner');
  el.style.display = 'block';
  el.textContent = 'JS Error: ' + msg + ' (line ' + line + ', col ' + col + ')';
}};
</script>
<script>
// ═══════════════════════════════════════════════════════════════════
//  Account data & switcher
// ═══════════════════════════════════════════════════════════════════
const ACCOUNT_DATA    = {account_data_json};
const PROFILES_META   = {profiles_meta_json};
const PRICE_HISTORIES = {price_histories_json};
const CASE_HISTORIES  = {case_histories_json};
let currentAccount    = 'all';

function getAccountData(key) {{ return ACCOUNT_DATA[key || currentAccount] || ACCOUNT_DATA['all']; }}

// ═══════════════════════════════════════════════════════════════════
//  Tab navigation
// ═══════════════════════════════════════════════════════════════════
let chartsInitialized      = false;
let portfolioInitialized   = false;
let signalsInitialized     = false;
let tradeupInitialized     = false;
let predictionsInitialized = false;
let casesInitialized       = false;

document.querySelectorAll('.tab-nav button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    // Update active button
    document.querySelectorAll('.tab-nav button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Update active panel
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    // Lazy-init on first visit (Chart.js needs visible containers)
    if (btn.dataset.tab === 'charts' && !chartsInitialized) {{
      chartsInitialized = true;
      renderDashboard('price-desc', '');
      // Bind chart control listeners once the tab is first shown
      const sortEl = document.getElementById('sortSelect');
      const searchEl = document.getElementById('searchInput');
      const timeEl = document.getElementById('globalTimeRange');
      if (sortEl) sortEl.addEventListener('change', e => {{
        renderDashboard(e.target.value, searchEl ? searchEl.value : '');
      }});
      if (searchEl) searchEl.addEventListener('input', e => {{
        renderDashboard(sortEl ? sortEl.value : 'price-desc', e.target.value);
      }});
      if (timeEl) timeEl.addEventListener('change', e => {{
        currentRange = e.target.value;
        renderDashboard(sortEl ? sortEl.value : 'price-desc', searchEl ? searchEl.value : '');
      }});
    }}
    if (btn.dataset.tab === 'portfolio' && !portfolioInitialized) {{
      portfolioInitialized = true;
      initPortfolioChart();
    }}
    if (btn.dataset.tab === 'signals' && !signalsInitialized) {{
      signalsInitialized = true;
      initSellSignals();
    }}
    if (btn.dataset.tab === 'tradeup' && !tradeupInitialized) {{
      tradeupInitialized = true;
      initTradeUp();
    }}
    if (btn.dataset.tab === 'predictions' && !predictionsInitialized) {{
      predictionsInitialized = true;
      initPredictions();
    }}
    if (btn.dataset.tab === 'cases' && !casesInitialized) {{
      casesInitialized = true;
      initCases();
    }}
  }});
}});

// ═══════════════════════════════════════════════════════════════════
//  Account switcher
// ═══════════════════════════════════════════════════════════════════
function switchAccount(acctKey) {{
  currentAccount = acctKey;
  const ad = getAccountData();

  // Update active button
  document.querySelectorAll('.acct-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('.acct-btn[data-account="' + acctKey + '"]').classList.add('active');

  // Update active data references
  ITEMS         = ad.charts;
  SELL_SIGNALS  = ad.signals;
  TRADEUP_DATA  = ad.tradeup;
  CONCENTRATION = ad.concentration;
  PREDICTIONS   = ad.predictions;

  // ── Inventory tab: show/hide profile sections ──
  document.querySelectorAll('.profile-section').forEach(sec => {{
    if (currentAccount === 'all') {{
      sec.style.display = '';
    }} else {{
      sec.style.display = sec.dataset.steamId === currentAccount ? '' : 'none';
    }}
  }});
  // Update inventory summary
  const sumEl = document.getElementById('inventorySummary');
  if (sumEl) {{
    const sections = document.querySelectorAll('.profile-section' +
      (currentAccount !== 'all' ? '[data-steam-id="' + currentAccount + '"]' : ''));
    let totalItems = 0, totalValue = 0;
    sections.forEach(sec => {{
      if (sec.style.display !== 'none') {{
        const meta = sec.querySelector('.profile-meta');
        if (meta) {{
          const itemsMatch = meta.textContent.match(/Items:\s*(\d+)/);
          const valueMatch = meta.textContent.match(/\$([\d,.]+)/);
          if (itemsMatch) totalItems += parseInt(itemsMatch[1]);
          if (valueMatch) totalValue += parseFloat(valueMatch[1].replace(/,/g, ''));
        }}
      }}
    }});
    const profileCount = currentAccount === 'all' ? Object.keys(PROFILES_META).length : 1;
    const lbEl = sumEl.querySelector('.leaderboard');
    sumEl.querySelector('.summary').innerHTML = `
      <div class="summary-card"><div class="value">${{profileCount}}</div><div class="label">Profiles Shown</div></div>
      <div class="summary-card"><div class="value">${{totalItems}}</div><div class="label">Total Items</div></div>
      <div class="summary-card"><div class="value accent-green">$${{totalValue.toLocaleString('en-US', {{minimumFractionDigits:2, maximumFractionDigits:2}})}}</div><div class="label">Combined Value</div></div>
    `;
    if (lbEl) lbEl.style.display = currentAccount === 'all' ? '' : 'none';
  }}

  // ── Re-render already-initialized tabs ──
  if (chartsInitialized) {{
    renderDashboard(document.getElementById('sortSelect').value, document.getElementById('searchInput').value);
  }}
  if (signalsInitialized) {{
    signalsInitialized = false;
    initSellSignals();
  }}
  if (tradeupInitialized) {{
    tuBasket = [];
    tradeupInitialized = false;
    initTradeUp();
  }}
  if (portfolioInitialized) {{
    renderConcentration();
  }}
  if (predictionsInitialized) {{
    predictionsInitialized = false;
    initPredictions();
  }}
}}

document.querySelectorAll('.acct-btn').forEach(btn => {{
  btn.addEventListener('click', () => switchAccount(btn.dataset.account));
}});

// ═══════════════════════════════════════════════════════════════════
//  Price Charts logic
// ═══════════════════════════════════════════════════════════════════
let ITEMS = getAccountData().charts;

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

function getHistory(name) {{ return PRICE_HISTORIES[name] || []; }}

function updateChart(name, range) {{
  const item = ITEMS.find(it => it.name === name);
  if (!item || !charts[name]) return;
  const filtered = filterByTime(getHistory(name), range);
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
  items.forEach(it => {{ it._stats = computeStats(filterByTime(getHistory(it.name), currentRange)); }});

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
    const filtered = filterByTime(getHistory(item.name), currentRange);
    const priceStr = item.current_price ? '$' + item.current_price.toFixed(2) : '—';

    const card = document.createElement('div');
    card.className = 'chart-card' + (item.locked ? ' trade-locked' : '');
    card.innerHTML = `
      <div class="chart-header">
        ${{item.image_url ? `<img src="${{item.image_url}}" alt="${{item.name}}" />` : ''}}
        <div class="info">
          <h3>${{item.name}}</h3>
          <div class="meta">${{item.type}}${{item.exterior ? ' • ' + item.exterior : ''}}</div>
          ${{item.rarity ? `<span class="rarity-badge" style="background:#${{item.rarity_color}}">${{item.rarity}}</span>` : ''}}
          ${{item.locked ? '<span class="lock-badge">🔒 Trade Locked</span>' : ''}}
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

// ═══════════════════════════════════════════════════════════════════
//  Portfolio chart
// ═══════════════════════════════════════════════════════════════════
const PORTFOLIO_HISTORY = {portfolio_json};
let portfolioChartInst = null;

function initPortfolioChart() {{
  const summaryEl = document.getElementById('portfolioSummary');
  if (!PORTFOLIO_HISTORY.length) {{
    summaryEl.innerHTML = '<p style="color:#8f98a0;padding:20px 0">No portfolio history yet — run the scraper to record snapshots.</p>';
    return;
  }}

  const first     = PORTFOLIO_HISTORY[0].total_value;
  const last      = PORTFOLIO_HISTORY[PORTFOLIO_HISTORY.length - 1].total_value;
  const ath       = Math.max(...PORTFOLIO_HISTORY.map(e => e.total_value));
  const changePct = first > 0 ? ((last - first) / first * 100) : 0;
  const cls       = changePct >= 0 ? '#a4d007' : '#ff4444';
  const arrow     = changePct >= 0 ? '▲' : '▼';

  summaryEl.innerHTML = `
    <div class="summary-card"><div class="value accent-green">$${{last.toFixed(2)}}</div><div class="label">Current Value</div></div>
    <div class="summary-card"><div class="value" style="color:#ffd700">$${{ath.toFixed(2)}}</div><div class="label">All-Time High</div></div>
    <div class="summary-card"><div class="value" style="color:${{cls}}">${{arrow}} ${{Math.abs(changePct).toFixed(1)}}%</div><div class="label">Change (All Time)</div></div>
    <div class="summary-card"><div class="value">${{PORTFOLIO_HISTORY.length}}</div><div class="label">Snapshots</div></div>
  `;

  const data = PORTFOLIO_HISTORY.map(e => ({{ x: new Date(e.date + 'T12:00:00').getTime(), y: e.total_value }}));
  if (portfolioChartInst) portfolioChartInst.destroy();
  portfolioChartInst = new Chart(document.getElementById('portfolioChart'), {{
    type: 'line',
    data: {{
      datasets: [{{
        label: 'Portfolio Value (USD)',
        data,
        borderColor: '#66c0f4',
        backgroundColor: 'rgba(102,192,244,0.1)',
        fill: true, tension: 0.3,
        pointRadius: PORTFOLIO_HISTORY.length < 30 ? 4 : 0,
        pointHoverRadius: 6,
        borderWidth: 2,
      }}]
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
            title: items => new Date(items[0].parsed.x).toLocaleDateString('en-US', {{
              year: 'numeric', month: 'short', day: 'numeric'
            }}),
            label: ctx => ' $' + ctx.parsed.y.toLocaleString('en-US', {{
              minimumFractionDigits: 2, maximumFractionDigits: 2
            }}),
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
          ticks: {{ color: '#8f98a0', maxTicksLimit: 10 }},
        }},
        y: {{
          grid: {{ color: 'rgba(42,71,94,0.4)' }},
          ticks: {{ color: '#8f98a0', callback: v => '$' + v.toLocaleString() }},
        }}
      }}
    }}
  }});
  renderConcentration();
}}

// ═══════════════════════════════════════════════════════════════════
//  Sell Signals
// ═══════════════════════════════════════════════════════════════════
let SELL_SIGNALS = getAccountData().signals;

function initSellSignals() {{
  const el = document.getElementById('signalsContent');
  if (!SELL_SIGNALS.length) {{
    el.innerHTML = '<p style="color:#8f98a0;padding:20px 0">No price history — run steam_price_charts.py first.</p>';
    return;
  }}
  const rows = SELL_SIGNALS.map(s => {{
    const barW  = Math.min(100, s.pct_of_high);
    const barC  = s.pct_of_high >= 80 ? '#ff6400' : s.pct_of_high >= 60 ? '#a4d007' : s.pct_of_high <= 30 ? '#ff4444' : '#66c0f4';
    const badge = s.pct_of_high >= 90 ? '<span class="signal-badge badge-hot">🔥 Near Year High</span>'
                : s.pct_of_high >= 75 ? '<span class="signal-badge badge-good">🟢 Good to Sell</span>'
                : s.pct_of_high <= 30 ? '<span class="signal-badge badge-low">🔴 Near Year Low — Hold</span>'
                : '<span class="signal-badge badge-neutral">⚪ Neutral</span>';
    const trend = s.trend_30d !== 0
      ? `<span style="color:${{s.trend_30d >= 0 ? '#a4d007' : '#ff4444'}}">${{s.trend_30d >= 0 ? '▲' : '▼'}} ${{Math.abs(s.trend_30d).toFixed(1)}}% (30d)</span>`
      : '';
    return `<div class="signal-card">
      ${{s.image_url ? `<img src="${{s.image_url}}" alt="" />` : ''}}
      <div class="signal-info">
        <div class="signal-name">${{s.name}}</div>
        <div class="signal-prices">Current: <b style="color:#a4d007">$${{s.current_price.toFixed(2)}}</b> &nbsp;·&nbsp; 52w High: <b>$${{s.year_high.toFixed(2)}}</b> &nbsp;·&nbsp; 52w Low: <b>$${{s.year_low.toFixed(2)}}</b></div>
        <div class="year-bar-track"><div class="year-bar-fill" style="width:${{barW}}%;background:${{barC}}"></div></div>
        <div class="signal-meta">
          <span style="color:${{barC}};font-weight:600">${{s.pct_of_high}}% of 52w high</span>
          ${{trend}}
          ${{badge}}
        </div>
      </div>
    </div>`;
  }}).join('');
  el.innerHTML = `<div class="signal-grid">${{rows}}</div>`;
}}

// ═══════════════════════════════════════════════════════════════════
//  Trade-Up Calculator
// ═══════════════════════════════════════════════════════════════════
let TRADEUP_DATA    = getAccountData().tradeup;
const RARITY_SEQUENCE = ["Consumer Grade","Industrial Grade","Mil-Spec Grade","Restricted","Classified","Covert"];

let tuRarity = '';
let tuBasket = [];

function initTradeUp() {{
  const el = document.getElementById('tradeupContent');
  const avail = RARITY_SEQUENCE.filter(r => TRADEUP_DATA[r]?.length);
  if (!avail.length) {{
    el.innerHTML = '<p style="color:#8f98a0;padding:20px 0">No marketable items with prices in inventory.</p>';
    return;
  }}
  tuRarity = avail[0];
  el.innerHTML = `
    <div class="tu-rarity-tabs" id="tuTabs"></div>
    <div class="tu-basket">
      <div class="tu-basket-header">
        <h3 id="tuTitle">Selected (0/10)</h3>
        <button class="btn-sm" onclick="clearBasket()">Clear</button>
      </div>
      <div class="basket-slots" id="tuSlots"></div>
      <div class="tu-summary" id="tuSummary"><span>Click items below to build your trade-up.</span></div>
    </div>
    <h3 style="color:#fff;margin-bottom:12px" id="tuAvailTitle"></h3>
    <div class="tu-grid" id="tuAvailGrid"></div>
    <div id="tuNextWrap" style="display:none">
      <div class="tu-next"><h3 id="tuNextTitle"></h3><div class="tu-grid" id="tuNextGrid"></div></div>
    </div>
  `;
  const tabsEl = document.getElementById('tuTabs');
  avail.forEach(r => {{
    const btn = document.createElement('button');
    btn.textContent = r;
    if (r === tuRarity) btn.classList.add('active');
    btn.onclick = () => {{
      tabsEl.querySelectorAll('button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      tuRarity = r; tuBasket = [];
      renderTradeUp();
    }};
    tabsEl.appendChild(btn);
  }});
  renderTradeUp();
}}

function clearBasket() {{ tuBasket = []; renderTradeUp(); }}

function toggleBasket(assetid) {{
  const items = TRADEUP_DATA[tuRarity] || [];
  const item  = items.find(it => it.assetid === assetid);
  if (!item) return;
  const idx = tuBasket.findIndex(it => it.assetid === assetid);
  if (idx >= 0) tuBasket.splice(idx, 1);
  else if (tuBasket.length < 10) tuBasket.push(item);
  renderTradeUp();
}}

function renderTradeUp() {{
  const items     = TRADEUP_DATA[tuRarity] || [];
  const nextIdx   = RARITY_SEQUENCE.indexOf(tuRarity) + 1;
  const nextRar   = nextIdx < RARITY_SEQUENCE.length ? RARITY_SEQUENCE[nextIdx] : null;
  const nextItems = nextRar ? (TRADEUP_DATA[nextRar] || []) : [];
  const basketIds = new Set(tuBasket.map(it => it.assetid));

  document.getElementById('tuTitle').textContent = `Selected (${{tuBasket.length}}/10)`;

  // Basket slots
  document.getElementById('tuSlots').innerHTML = Array.from({{length: 10}}, (_, i) => {{
    const it = tuBasket[i];
    if (it) return `<div class="basket-slot filled" onclick="toggleBasket('${{it.assetid}}')" title="${{it.name}}"><img src="${{it.image_url}}" /><span class="rm">×</span></div>`;
    return '<div class="basket-slot">empty</div>';
  }}).join('');

  // Summary
  const sumEl = document.getElementById('tuSummary');
  if (tuBasket.length > 0) {{
    const totalIn = tuBasket.reduce((s, it) => s + it.price, 0);
    const wears   = tuBasket.filter(it => it.wear_rating != null);
    const avgWear = wears.length ? wears.reduce((s, it) => s + it.wear_rating, 0) / wears.length : null;
    const avgNext = nextItems.length ? nextItems.reduce((s, it) => s + it.price, 0) / nextItems.length : null;
    const parts   = [`<span>Input total: <b style="color:#a4d007">$${{totalIn.toFixed(2)}}</b></span>`];
    if (avgWear != null) parts.push(`<span>Avg wear: <b>${{avgWear.toFixed(4)}}</b></span>`);
    if (avgNext != null) {{
      const diff = avgNext - totalIn;
      parts.push(`<span>Avg next-grade: <b>$${{avgNext.toFixed(2)}}</b></span>`);
      parts.push(`<span class="${{diff >= 0 ? 'tu-profit-pos' : 'tu-profit-neg'}}">${{diff >= 0 ? '▲' : '▼'}} $${{Math.abs(diff).toFixed(2)}} (${{diff >= 0 ? '+' : ''}}${{(diff / totalIn * 100).toFixed(1)}}%)</span>`);
    }}
    sumEl.innerHTML = parts.join(' ');
  }} else {{
    sumEl.innerHTML = '<span>Click items below to build your trade-up.</span>';
  }}

  // Available grid
  document.getElementById('tuAvailTitle').textContent = `${{tuRarity}} — ${{items.length}} item${{items.length !== 1 ? 's' : ''}} available`;
  document.getElementById('tuAvailGrid').innerHTML = items.map(it => {{
    const sel   = basketIds.has(it.assetid);
    const maxed = !sel && tuBasket.length >= 10;
    let h = `<div class="tu-item${{sel ? ' selected' : ''}}${{maxed ? ' maxed' : ''}}" onclick="toggleBasket('${{it.assetid}}')" title="${{it.name}}">`;
    if (it.image_url) h += `<img src="${{it.image_url}}" />`;
    h += `<div class="ti-name">${{it.name.replace(/\s*\([^)]*\)\s*$/, '')}}</div>`;
    if (it.exterior) h += `<div class="ti-ext">${{it.exterior}}</div>`;
    h += `<div class="ti-price">$${{it.price.toFixed(2)}}</div></div>`;
    return h;
  }}).join('');

  // Next-grade reference
  const wrap = document.getElementById('tuNextWrap');
  if (nextItems.length && nextRar) {{
    wrap.style.display = 'block';
    const avg = nextItems.reduce((s, it) => s + it.price, 0) / nextItems.length;
    document.getElementById('tuNextTitle').textContent = `${{nextRar}} in your inventory (${{nextItems.length}} item${{nextItems.length !== 1 ? 's' : ''}}, avg $${{avg.toFixed(2)}})`;
    document.getElementById('tuNextGrid').innerHTML = nextItems.map(it => {{
      let h = `<div class="tu-item" style="cursor:default" title="${{it.name}}">`;
      if (it.image_url) h += `<img src="${{it.image_url}}" />`;
      h += `<div class="ti-name">${{it.name.replace(/\s*\([^)]*\)\s*$/, '')}}</div>`;
      if (it.exterior) h += `<div class="ti-ext">${{it.exterior}}</div>`;
      h += `<div class="ti-price">$${{it.price.toFixed(2)}}</div></div>`;
      return h;
    }}).join('');
  }} else {{
    wrap.style.display = 'none';
  }}
}}

// ═══════════════════════════════════════════════════════════════════
//  Portfolio: Concentration Risk
// ═══════════════════════════════════════════════════════════════════
let CONCENTRATION = getAccountData().concentration;

function renderConcentration() {{
  const el = document.getElementById('concentrationSection');
  if (!el || !CONCENTRATION.total) return;
  const riskColor = CONCENTRATION.risk_level === 'High' ? '#ff4444'
                  : CONCENTRATION.risk_level === 'Medium' ? '#ffd700' : '#a4d007';
  const riskClass = 'risk-' + CONCENTRATION.risk_level.toLowerCase();
  let html = `
    <h2 style="color:#fff;margin:0 0 16px">Portfolio Concentration Risk</h2>
    <div class="summary" style="margin-bottom:24px">
      <div class="summary-card">
        <div class="value"><span class="risk-badge ${{riskClass}}">${{CONCENTRATION.risk_level}}</span></div>
        <div class="label">Risk Level</div>
      </div>
      <div class="summary-card">
        <div class="value">${{CONCENTRATION.hhi.toFixed(0)}}</div>
        <div class="label">HHI Score <span style="font-size:0.6em;color:#8f98a0">(0–10000)</span></div>
      </div>
      <div class="summary-card">
        <div class="value" style="color:${{riskColor}}">${{CONCENTRATION.top1_pct}}%</div>
        <div class="label">Top Item Concentration</div>
      </div>
      <div class="summary-card">
        <div class="value">${{CONCENTRATION.top3_pct}}%</div>
        <div class="label">Top 3 Items</div>
      </div>
    </div>`;
  const maxPct = Math.max(...CONCENTRATION.items.map(it => it.pct));
  html += '<div class="conc-section"><h3>Top Items by Portfolio Weight</h3><div class="conc-bars">';
  CONCENTRATION.items.forEach(it => {{
    const c = it.pct >= 20 ? '#ff4444' : it.pct >= 10 ? '#ffd700' : '#66c0f4';
    const w = (it.pct / maxPct * 100).toFixed(1);
    html += `<div class="conc-row">
      ${{it.image_url ? `<img src="${{it.image_url}}" class="conc-img" alt="" />` : '<div class="conc-img"></div>'}}
      <span class="conc-name" title="${{it.name}}">${{it.name}}</span>
      <div class="conc-bar-track"><div class="conc-bar-fill" style="width:${{w}}%;background:${{c}}"></div></div>
      <span class="conc-pct" style="color:${{c}}">${{it.pct}}%</span>
      <span class="conc-val">$${{it.value.toFixed(2)}}</span>
    </div>`;
  }});
  html += '</div></div>';
  html += '<div class="conc-section" style="margin-top:20px"><h3>By Rarity</h3><div class="conc-bars">';
  CONCENTRATION.by_rarity.forEach(r => {{
    html += `<div class="conc-row">
      <span class="conc-name">${{r.rarity}}</span>
      <div class="conc-bar-track"><div class="conc-bar-fill" style="width:${{r.pct}}%;background:${{r.color}}"></div></div>
      <span class="conc-pct">${{r.pct}}%</span>
      <span class="conc-val">$${{r.value.toFixed(2)}}</span>
    </div>`;
  }});
  html += '</div></div>';
  el.innerHTML = html;
}}

// ═══════════════════════════════════════════════════════════════════
//  Price Predictions
// ═══════════════════════════════════════════════════════════════════
let PREDICTIONS = getAccountData().predictions;

function initPredictions() {{
  const el = document.getElementById('predictionsContent');
  if (!PREDICTIONS.length) {{
    el.innerHTML = '<p style="color:#8f98a0;padding:20px 0">No price history — run steam_price_charts.py first.</p>';
    return;
  }}
  const bullish = PREDICTIONS.filter(p => p.change_pct > 0).length;
  const bearish = PREDICTIONS.filter(p => p.change_pct < 0).length;
  const avgChg  = PREDICTIONS.reduce((s, p) => s + p.change_pct, 0) / PREDICTIONS.length;
  const avgClr  = avgChg >= 0 ? '#a4d007' : '#ff4444';
  const avgArr  = avgChg >= 0 ? '▲' : '▼';
  let html = `
    <div class="summary" style="margin-bottom:20px">
      <div class="summary-card"><div class="value" style="color:#a4d007">${{bullish}}</div><div class="label">📈 Bullish (30d)</div></div>
      <div class="summary-card"><div class="value" style="color:#ff4444">${{bearish}}</div><div class="label">📉 Bearish (30d)</div></div>
      <div class="summary-card"><div class="value" style="color:${{avgClr}}">${{avgArr}} ${{Math.abs(avgChg).toFixed(1)}}%</div><div class="label">Avg Predicted Change</div></div>
      <div class="summary-card"><div class="value">${{PREDICTIONS.length}}</div><div class="label">Items Analysed</div></div>
    </div>
    <div class="pred-disclaimer">⚠️ Predictions use linear regression on 90-day history. Higher R² = more reliable. Not financial advice.</div>
    <div class="pred-grid">`;
  PREDICTIONS.forEach(p => {{
    const cls   = p.change_pct >= 0 ? 'pred-up' : 'pred-down';
    const arrow = p.change_pct >= 0 ? '▲' : '▼';
    const r2c   = p.r2 >= 0.7 ? '#a4d007' : p.r2 >= 0.4 ? '#ffd700' : '#8f98a0';
    html += `<div class="pred-card">
      ${{p.image_url ? `<img src="${{p.image_url}}" alt="" />` : ''}}
      <div class="pred-info">
        <div class="pred-name">${{p.name}}</div>
        <div class="pred-prices">Current: <b style="color:#a4d007">$${{p.current_price.toFixed(2)}}</b> → 30d Pred: <b>$${{p.predicted_price.toFixed(2)}}</b></div>
      </div>
      <div class="pred-change ${{cls}}">${{arrow}} ${{Math.abs(p.change_pct).toFixed(1)}}%</div>
      <div class="pred-r2" title="R² (0–1, higher = more reliable)">R² <span style="color:${{r2c}}">${{p.r2.toFixed(2)}}</span></div>
    </div>`;
  }});
  html += '</div>';
  el.innerHTML = html;
}}

// ═══════════════════════════════════════════════════════════════════
//  Case Investment Tracker
// ═══════════════════════════════════════════════════════════════════
const CASE_DATA = {cases_json};
const caseCharts = {{}};
let caseFilter = 'all';

function initCases() {{
  const el = document.getElementById('casesContent');
  if (!CASE_DATA.length) {{
    el.innerHTML = '<p style="color:#8f98a0;padding:20px 0">No case data yet — run <code>python steam_case_tracker.py</code> first.</p>';
    return;
  }}
  const disc  = CASE_DATA.filter(c => c.status === 'discontinued').length;
  const rare  = CASE_DATA.filter(c => c.status === 'rare').length;
  const actv  = CASE_DATA.filter(c => c.status === 'active').length;
  const best  = CASE_DATA[0];
  el.innerHTML = `
    <div class="summary" style="margin-bottom:16px">
      <div class="summary-card"><div class="value">${{CASE_DATA.length}}</div><div class="label">Cases Tracked</div></div>
      <div class="summary-card"><div class="value" style="color:#ff6666">${{disc}}</div><div class="label">Discontinued</div></div>
      <div class="summary-card"><div class="value" style="color:#ffd700">${{rare}}</div><div class="label">Rare Drop</div></div>
      <div class="summary-card"><div class="value" style="color:#a4d007">${{actv}}</div><div class="label">Active Drop</div></div>
    </div>
    <div class="case-filters" id="caseFilters">
      <button class="active" data-f="all">All</button>
      <button data-f="discontinued">🔴 Discontinued</button>
      <button data-f="rare">🟡 Rare</button>
      <button data-f="active">🟢 Active</button>
    </div>
    <div id="caseGrid"></div>
  `;
  document.querySelectorAll('#caseFilters button').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('#caseFilters button').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      caseFilter = btn.dataset.f;
      renderCaseCards();
    }});
  }});
  renderCaseCards();
}}

function renderCaseCards() {{
  Object.values(caseCharts).forEach(c => c.destroy());
  for (const k in caseCharts) delete caseCharts[k];
  const grid = document.getElementById('caseGrid');
  const items = caseFilter === 'all' ? CASE_DATA : CASE_DATA.filter(c => c.status === caseFilter);
  grid.innerHTML = '';
  items.forEach((c, idx) => {{
    const id = 'case-chart-' + idx;
    const statusCls = 'status-' + c.status;
    const atChg = c.all_time_chg >= 0 ? '#a4d007' : '#ff4444';
    const yrChg = c.yr_chg >= 0 ? '#a4d007' : '#ff4444';
    const qChg  = c.q_chg >= 0 ? '#a4d007' : '#ff4444';
    const card = document.createElement('div');
    card.className = 'case-card';
    card.innerHTML = `
      <div class="case-top">
        <span class="case-name">${{c.name}}<span class="status-pill ${{statusCls}}">${{c.status}}</span></span>
        <span class="case-price">$${{c.current.toFixed(2)}}</span>
      </div>
      <div class="case-stats">
        <span>All-time: <b style="color:${{atChg}}">${{c.all_time_chg >= 0 ? '+' : ''}}${{c.all_time_chg}}%</b></span>
        <span>1Y: <b style="color:${{yrChg}}">${{c.yr_chg >= 0 ? '+' : ''}}${{c.yr_chg}}%</b></span>
        <span>90d: <b style="color:${{qChg}}">${{c.q_chg >= 0 ? '+' : ''}}${{c.q_chg}}%</b></span>
        <span>ATH: <b>$${{c.ath.toFixed(2)}}</b></span>
        <span>ATL: <b>$${{c.atl.toFixed(2)}}</b></span>
        <span>Vol (30d avg): <b>${{c.avg_vol}}</b></span>
      </div>
      <div class="case-chart-wrap"><canvas id="${{id}}"></canvas></div>
    `;
    grid.appendChild(card);
    const hist = CASE_HISTORIES[c.name] || [];
    const data = hist.map(d => ({{ x: d[0], y: d[1] }}));
    const color = c.status === 'discontinued' ? '#ff6666' : c.status === 'rare' ? '#ffd700' : '#a4d007';
    caseCharts[c.name] = new Chart(document.getElementById(id), {{
      type: 'line',
      data: {{ datasets: [{{
        label: 'Price', data,
        borderColor: color, backgroundColor: color + '15',
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
      }}] }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }},
          tooltip: {{
            backgroundColor: '#1e2a3a', titleColor: '#fff', bodyColor: '#c6d4df',
            borderColor: '#2a475e', borderWidth: 1,
            callbacks: {{
              title: items => new Date(items[0].parsed.x).toLocaleDateString('en-US', {{ year:'numeric', month:'short', day:'numeric' }}),
              label: ctx => ' $' + ctx.parsed.y.toFixed(2),
            }}
          }}
        }},
        scales: {{
          x: {{ type: 'time', grid: {{ color: 'rgba(42,71,94,0.3)' }}, ticks: {{ color: '#8f98a0', maxTicksLimit: 6 }} }},
          y: {{ grid: {{ color: 'rgba(42,71,94,0.3)' }}, ticks: {{ color: '#8f98a0', callback: v => '$' + v.toFixed(2) }} }}
        }}
      }}
    }});
  }});
}}

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

    portfolio_history = []
    if os.path.exists(PORTFOLIO_HISTORY_FILE):
        with open(PORTFOLIO_HISTORY_FILE) as f:
            portfolio_history = json.load(f)

    case_history = {}
    if os.path.exists(CASE_DATA_FILE):
        with open(CASE_DATA_FILE) as f:
            case_history = json.load(f)

    case_meta = {}
    if os.path.exists(CASE_META_FILE):
        with open(CASE_META_FILE) as f:
            case_meta = json.load(f)

    html = generate_dashboard(data, price_data, input_file, portfolio_history,
                              case_history, case_meta)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    with open('docs/index.html', "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = len(html) / 1024
    print(f"✅ Dashboard written to {output_file} ({size_kb:.0f} KB)")
    print(f"✅ Dashboard written to docs/index.html ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
