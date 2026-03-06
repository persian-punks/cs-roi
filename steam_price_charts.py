#!/usr/bin/env python3
"""Fetch lifetime Steam Market price history for inventory items and generate
an interactive HTML dashboard with time-series charts (Chart.js)."""

import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
import requests
import rsa
import base64

import requests

# ── Config ──────────────────────────────────────────────────────────────────
INPUT_FILE = "data/steam_inventory.json"
PRICE_DATA_FILE = "data/steam_price_history.json"
OUTPUT_FILE = "reports/steam_price_charts.html"
APP_ID = 730  # CS2

PRICE_HISTORY_URL = "https://steamcommunity.com/market/pricehistory/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Rate-limit: Steam is strict on the pricehistory endpoint
REQUEST_DELAY = 5  # seconds between requests

session = requests.Session()

def load_price_data():
    """Load existing price history data from the JSON output file."""
    if os.path.exists(PRICE_DATA_FILE):
        with open(PRICE_DATA_FILE) as f:
            return json.load(f)
    return {}


def save_price_data(data):
    """Write price history data to the JSON output file."""
    with open(PRICE_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_price_history(item_name):
    """Fetch lifetime price history for a single item.

    Returns a list of [timestamp_ms, price, volume] entries, or None on failure.
    """
    params = {"appid": APP_ID, "market_hash_name": item_name, "currency": 1}

    try:
        resp = session.get(
            PRICE_HISTORY_URL,
            params=params,
            timeout=15,
        )

        if resp.status_code == 429:
            print("    ⏳ Rate limited — waiting 60s...")
            time.sleep(60)
            resp = session.get(
                PRICE_HISTORY_URL,
                params=params,
                timeout=15,
            )

        if resp.status_code == 401 or resp.status_code == 403:
            print("    ❌ Auth failed — check your steamLoginSecure cookie.")
            return None

        if not resp.ok:
            print(f"    ❌ HTTP {resp.status_code}")
            print(f"    Response: {resp.text[:200]}")
            return None

        data = resp.json()
        if not data.get("success"):
            print("    ❌ API returned success=false")
            return None

        # Steam returns: ["Mon DD YYYY HH: +0", price, "volume"]
        # Convert to [timestamp_ms, price, volume_int]
        entries = []
        for entry in data.get("prices", []):
            date_str, price, volume_str = entry
            # Parse the Steam date format: "Feb 13 2015 01: +0"
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


def get_unique_marketable_items(data):
    """Extract unique marketable item names across all profiles, with metadata."""
    items = {}  # name -> item metadata (first occurrence)
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


def fetch_all_histories(items, price_data):
    """Fetch price history for all items, skipping those already in the output file."""
    total = len(items)
    fetched = 0

    for i, (name, meta) in enumerate(items.items(), 1):
        if name in price_data:
            print(f"  [{i}/{total}] ✅ {name} (already fetched)")
            continue

        print(f"  [{i}/{total}] 📈 {name}...", end=" ", flush=True)
        history = fetch_price_history(name)

        if history is not None:
            price_data[name] = history
            print(f"OK ({len(history)} data points)")
            fetched += 1
            save_price_data(price_data)  # write after each successful fetch
        else:
            print("skipped")

        # Always sleep between requests to avoid rate limiting
        if i < total:
            time.sleep(REQUEST_DELAY)

    print(f"\n  Fetched {fetched} new item(s), {len(price_data)} total in output file.")


def generate_html(items, price_data):
    """Generate a self-contained HTML dashboard with Chart.js time-series charts."""

    # Build chart data for items that have price history
    chart_items = []
    for name, meta in items.items():
        if name not in price_data or not price_data[name]:
            continue
        history = price_data[name]
        chart_items.append({
            "name": name,
            "type": meta.get("type", ""),
            "rarity": meta.get("rarity", ""),
            "rarity_color": meta.get("rarity_color", "ffffff"),
            "image_url": meta.get("image_url", ""),
            "exterior": meta.get("exterior", ""),
            "current_price": (meta.get("market_price") or {}).get("lowest_price"),
            "history": history,
        })

    # Sort by current price descending
    chart_items.sort(key=lambda x: x.get("current_price") or 0, reverse=True)

    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    # Serialize chart data to JSON for embedding
    charts_json = json.dumps(chart_items)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Steam CS2 Inventory — Lifetime Price Charts</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>
<style>
  :root {{
    --bg: #1b2838;
    --card-bg: #1e2a3a;
    --text: #c6d4df;
    --accent: #66c0f4;
    --border: #2a475e;
    --header-bg: #171a21;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 0;
  }}
  header {{
    background: var(--header-bg);
    padding: 24px 32px;
    border-bottom: 2px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
  }}
  header h1 {{
    font-size: 1.8em;
    color: #fff;
    margin-bottom: 4px;
  }}
  header p {{
    color: #8f98a0;
    font-size: 0.9em;
  }}
  .controls {{
    display: flex;
    gap: 12px;
    margin-top: 12px;
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
  .container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 24px;
  }}
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
    padding: 16px;
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
  .chart-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(600px, 1fr));
    gap: 20px;
  }}
  .chart-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    transition: transform 0.2s;
  }}
  .chart-card:hover {{
    transform: translateY(-2px);
    border-color: var(--accent);
  }}
  .chart-header {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }}
  .chart-header img {{
    width: 80px;
    height: 60px;
    object-fit: contain;
    background: rgba(0,0,0,0.2);
    border-radius: 4px;
  }}
  .chart-header .info {{
    flex: 1;
  }}
  .chart-header .info h3 {{
    color: #fff;
    font-size: 1em;
    margin-bottom: 2px;
  }}
  .chart-header .info .meta {{
    font-size: 0.8em;
    color: #8f98a0;
  }}
  .chart-header .price {{
    font-size: 1.4em;
    font-weight: bold;
    color: #a4d007;
  }}
  .chart-header .rarity-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 0.75em;
    font-weight: 600;
    color: #fff;
    margin-top: 4px;
  }}
  .chart-body {{
    padding: 12px 16px 16px;
    position: relative;
  }}
  .chart-body canvas {{
    width: 100% !important;
    height: 250px !important;
  }}
  .chart-stats {{
    display: flex;
    gap: 16px;
    padding: 0 16px 12px;
    font-size: 0.8em;
    color: #8f98a0;
    flex-wrap: wrap;
  }}
  .chart-stats span {{
    white-space: nowrap;
  }}
  .chart-stats .up {{ color: #a4d007; }}
  .chart-stats .down {{ color: #ff4444; }}
  .time-buttons {{
    display: flex;
    gap: 4px;
    padding: 8px 16px;
  }}
  .time-buttons button {{
    background: transparent;
    color: #8f98a0;
    border: 1px solid var(--border);
    padding: 3px 10px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 0.75em;
    transition: all 0.2s;
  }}
  .time-buttons button:hover,
  .time-buttons button.active {{
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }}
  @media (max-width: 700px) {{
    .chart-grid {{ grid-template-columns: 1fr; }}
    header h1 {{ font-size: 1.3em; }}
  }}
</style>
</head>
<body>

<header>
  <h1>📈 Steam CS2 Inventory — Price Charts</h1>
  <p>Generated on {now}</p>
  <div class="controls">
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
</header>

<div class="container">
  <div class="summary" id="summarySection"></div>
  <div class="chart-grid" id="chartGrid"></div>
</div>

<script>
const ITEMS = {charts_json};

// ── Helpers ──
function filterByTime(history, range) {{
  if (range === 'all') return history;
  const now = Date.now();
  const ms = {{
    '1w': 7*86400000, '1m': 30*86400000, '3m': 90*86400000,
    '6m': 180*86400000, '1y': 365*86400000
  }}[range] || 0;
  const cutoff = now - ms;
  return history.filter(d => d[0] >= cutoff);
}}

function computeStats(history) {{
  if (!history.length) return {{}};
  const prices = history.map(d => d[1]);
  const allTimeHigh = Math.max(...prices);
  const allTimeLow = Math.min(...prices);
  const first = prices[0];
  const last = prices[prices.length - 1];
  const changePct = first > 0 ? ((last - first) / first * 100) : 0;
  const avgVol = history.reduce((s, d) => s + d[2], 0) / history.length;
  return {{ allTimeHigh, allTimeLow, first, last, changePct, avgVol }};
}}

// ── Chart creation ──
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
          label: 'Price (USD)',
          data: data,
          borderColor: color,
          backgroundColor: color + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 0,
          pointHitRadius: 8,
          borderWidth: 2,
          yAxisID: 'y',
        }},
        {{
          label: 'Volume',
          data: volData,
          type: 'bar',
          backgroundColor: 'rgba(102,192,244,0.15)',
          borderColor: 'transparent',
          yAxisID: 'y1',
          barPercentage: 1,
          categoryPercentage: 1,
        }}
      ]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{
        mode: 'index',
        intersect: false,
      }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#1e2a3a',
          titleColor: '#fff',
          bodyColor: '#c6d4df',
          borderColor: '#2a475e',
          borderWidth: 1,
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
            displayFormats: {{
              day: 'MMM d',
              week: 'MMM d',
              month: 'MMM yyyy',
              year: 'yyyy',
            }}
          }},
          grid: {{ color: 'rgba(42,71,94,0.4)' }},
          ticks: {{ color: '#8f98a0', maxTicksLimit: 8 }},
        }},
        y: {{
          position: 'left',
          grid: {{ color: 'rgba(42,71,94,0.4)' }},
          ticks: {{
            color: '#8f98a0',
            callback: v => '$' + v.toFixed(2),
          }},
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
  const priceData = filtered.map(d => ({{ x: d[0], y: d[1] }}));
  const volData = filtered.map(d => ({{ x: d[0], y: d[2] }}));
  charts[name].data.datasets[0].data = priceData;
  charts[name].data.datasets[1].data = volData;
  charts[name].update('none');

  // Update stats
  const stats = computeStats(filtered);
  const statsEl = document.getElementById('stats-' + CSS.escape(name));
  if (statsEl) renderStats(statsEl, stats);
}}

function renderStats(el, stats) {{
  if (!stats.allTimeHigh) {{ el.innerHTML = ''; return; }}
  const cls = stats.changePct >= 0 ? 'up' : 'down';
  const arrow = stats.changePct >= 0 ? '▲' : '▼';
  el.innerHTML = `
    <span>High: <b>$$${{stats.allTimeHigh.toFixed(2)}}</b></span>
    <span>Low: <b>$$${{stats.allTimeLow.toFixed(2)}}</b></span>
    <span class="${{cls}}">${{arrow}} ${{stats.changePct.toFixed(1)}}%</span>
    <span>Avg Vol: <b>${{Math.round(stats.avgVol)}}</b></span>
  `;
}}

// ── Render all cards ──
let currentRange = 'all';

function renderDashboard(sortKey, searchTerm) {{
  // Destroy existing charts
  Object.values(charts).forEach(c => c.destroy());
  for (const k in charts) delete charts[k];

  let items = [...ITEMS];

  // Filter
  if (searchTerm) {{
    const q = searchTerm.toLowerCase();
    items = items.filter(it => it.name.toLowerCase().includes(q) || it.type.toLowerCase().includes(q));
  }}

  // Compute stats for sorting
  items.forEach(it => {{
    const filtered = filterByTime(it.history, currentRange);
    it._stats = computeStats(filtered);
  }});

  // Sort
  switch (sortKey) {{
    case 'price-desc':
      items.sort((a, b) => (b.current_price || 0) - (a.current_price || 0));
      break;
    case 'price-asc':
      items.sort((a, b) => (a.current_price || 0) - (b.current_price || 0));
      break;
    case 'name-asc':
      items.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case 'change-desc':
      items.sort((a, b) => (b._stats.changePct || 0) - (a._stats.changePct || 0));
      break;
    case 'change-asc':
      items.sort((a, b) => (a._stats.changePct || 0) - (b._stats.changePct || 0));
      break;
  }}

  // Summary
  const summaryEl = document.getElementById('summarySection');
  const totalItems = items.length;
  const totalValue = items.reduce((s, it) => s + (it.current_price || 0), 0);
  const avgChange = items.length
    ? items.reduce((s, it) => s + (it._stats.changePct || 0), 0) / items.length
    : 0;
  const avgCls = avgChange >= 0 ? 'up' : 'down';

  summaryEl.innerHTML = `
    <div class="summary-card">
      <div class="value">${{totalItems}}</div>
      <div class="label">Items Tracked</div>
    </div>
    <div class="summary-card">
      <div class="value">$$${{totalValue.toFixed(2)}}</div>
      <div class="label">Total Current Value</div>
    </div>
    <div class="summary-card">
      <div class="value" style="color: ${{avgChange >= 0 ? '#a4d007' : '#ff4444'}}">${{avgChange >= 0 ? '+' : ''}}${{avgChange.toFixed(1)}}%</div>
      <div class="label">Avg Price Change (${{currentRange === 'all' ? 'All Time' : currentRange}})</div>
    </div>
  `;

  // Chart cards
  const grid = document.getElementById('chartGrid');
  grid.innerHTML = '';

  items.forEach((item, idx) => {{
    const canvasId = 'chart-' + idx;
    const filtered = filterByTime(item.history, currentRange);
    const stats = item._stats;
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

    // Create chart
    charts[item.name] = createChart(canvasId, filtered, item.rarity_color);

    // Render stats
    const statsEl = card.querySelector('.chart-stats');
    renderStats(statsEl, stats);

    // Per-card time buttons
    card.querySelectorAll('.time-buttons button').forEach(btn => {{
      btn.addEventListener('click', () => {{
        card.querySelectorAll('.time-buttons button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateChart(item.name, btn.dataset.range);
      }});
    }});
  }});
}}

// ── Event listeners ──
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

// Initial render
renderDashboard('price-desc', '');
</script>
</body>
</html>"""

    return html


def main():
    input_file = sys.argv[1] if len(sys.argv) > 1 else INPUT_FILE
    output_file = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    # Load inventory
    print(f"📦 Loading inventory from {input_file}...")
    with open(input_file) as f:
        data = json.load(f)

    items = get_unique_marketable_items(data)
    print(f"   Found {len(items)} unique marketable items across {len(data)} profile(s).\n")

    if not items:
        print("No marketable items found.")
        sys.exit(1)

    # Load existing price data
    price_data = load_price_data()
    existing_count = sum(1 for name in items if name in price_data)
    print(f"📁 Price data: {existing_count}/{len(items)} items already fetched.\n")

    # Get Steam cookie for authentication
    cookie = os.environ.get("STEAM_LOGIN_SECURE", "").strip()
    if not cookie:
        print("🔑 No STEAM_LOGIN_SECURE env var found.")
        print("   Paste your steamLoginSecure cookie (from browser DevTools → Cookies):")
        cookie = input("   > ").strip()

    if not cookie:
        if existing_count > 0:
            print("\n⚠️  No cookie provided. Generating charts from existing data only.\n")
        else:
            print("\n❌ No cookie and no price data. Cannot proceed.")
            sys.exit(1)
    else:
        session.cookies.set("steamLoginSecure", cookie, domain="steamcommunity.com")
        session.headers.update(HEADERS)
        print(f"\n📈 Fetching price histories...")
        fetch_all_histories(items, price_data)

    # Generate HTML
    print(f"\n🎨 Generating dashboard...")
    html = generate_html(items, price_data)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = len(html) / 1024
    chart_count = sum(1 for name in items if name in price_data and price_data[name])
    print(f"✅ Dashboard written to {output_file} ({size_kb:.0f} KB)")
    print(f"   {chart_count} item chart(s) with lifetime price data.")
    print()
    print("=" * 60)
    print("  💰 ETH Donation: 0x89705f4d632E93F8a466683Dc520577Ec08D37e0")
    print("  🐙 GitHub:       https://github.com/persian-punks")
    print("=" * 60)


if __name__ == "__main__":
    main()
