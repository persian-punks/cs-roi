# cs-roi

A toolkit for tracking the value and price history of your CS2 (Counter-Strike 2) Steam inventory. Scrapes inventory data, fetches market prices and lifetime price histories, and generates visual reports as Markdown, HTML, or an interactive dashboard.

URL: `https://persian-punks.github.io/cs-roi/`

## Overview

```
steam_inventory_scraper.py   → Scrape inventory + current market prices → data/steam_inventory.json
steam_price_charts.py        → Fetch lifetime price histories           → data/steam_price_history.json
                                                                        → reports/steam_price_charts.html
steam_inventory_report.py    → Generate Markdown inventory report       → reports/steam_inventory_report.md
steam_inventory_report_html.py → Generate HTML inventory report         → reports/steam_inventory_report.html
steam_dashboard.py           → Combined tabbed HTML dashboard           → reports/steam_dashboard.html
```

## Setup

**Requirements:** Python 3.8+

Install dependencies:

```sh
pip install requests python-dotenv rsa
```

Create a `.env` file in the project root with your Steam ID:

```
STEAM_ID=<insert numeric Steam ID>
```

## Usage

### 1. Scrape your inventory

```sh
python3 steam_inventory_scraper.py
```

Fetches all CS2 items from the configured Steam profile(s), looks up current market prices, and saves everything to `data/steam_inventory.json`. You can also pass Steam IDs as arguments to scan multiple profiles:

```sh
python3 steam_inventory_scraper.py 76561198012345678 76561198087654321
```

### 2. Fetch price history and generate charts

```sh
python3 steam_price_charts.py
```

Fetches lifetime Steam Market price history for each marketable item in your inventory. Requires a `steamLoginSecure` cookie for authentication (prompted on run, or set via `STEAM_LOGIN_SECURE` env var). Price data is cached in `data/steam_price_history.json` so subsequent runs only fetch new items.

Outputs an interactive HTML dashboard (`reports/steam_price_charts.html`) with per-item time-series charts, sorting, filtering, and time range controls.

### 3. Generate reports

**Markdown report:**

```sh
python3 steam_inventory_report.py
```

Writes `reports/steam_inventory_report.md` — a detailed breakdown with item cards, rarity stats, and price info.

**HTML report:**

```sh
python3 steam_inventory_report_html.py
```

Writes `reports/steam_inventory_report.html` — same content styled with the Steam dark theme.

**Combined dashboard:**

```sh
python3 steam_dashboard.py
```

Writes `reports/steam_dashboard.html` — a single-page dashboard with tab navigation between the inventory report and the interactive price charts.

## Project Structure

```
cs-roi/
├── data/
│   ├── steam_inventory.json       # Scraped inventory + prices
│   └── steam_price_history.json   # Cached lifetime price histories
├── reports/
│   ├── steam_inventory_report.md  # Markdown report
│   ├── steam_inventory_report.html# HTML report
│   ├── steam_price_charts.html    # Interactive price charts
│   └── steam_dashboard.html       # Combined tabbed dashboard
├── steam_inventory_scraper.py     # Inventory scraper + price lookup
├── steam_price_charts.py          # Price history fetcher + chart generator
├── steam_inventory_report.py      # Markdown report generator
├── steam_inventory_report_html.py # HTML report generator
├── steam_dashboard.py             # Combined dashboard generator
├── .env                           # Steam ID config (not committed)
├── .gitignore
└── LICENSE                        # MIT
```

## License

MIT
