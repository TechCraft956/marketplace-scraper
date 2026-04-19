# Facebook Marketplace Deal Scraper Module

A production-grade deal-hunting system for Facebook Marketplace, designed to integrate with the **Operator Dashboard** (FastAPI + React modular plugin architecture). Scrapes listings, scores them by resale potential (0-100), filters by user criteria, and surfaces opportunities in a dark-themed dashboard panel.

**Status:** Complete, production-ready. 4,075 lines of real code.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 2. Export Facebook cookies (see setup_instructions.md)
# Place in: data/fb_cookies.json

# 3. Register with dashboard (see setup_instructions.md for FastAPI wiring)

# 4. Run first scrape
python -c "
import asyncio
from backend.modules.marketplace_scraper import MarketplaceScraperModule

config = {
    'search_queries': ['macbook pro', 'ps5'],
    'location_city': 'austin',
    'price_max': 800,
    'min_resale_score': 55,
    'cookies_path': 'data/fb_cookies.json',
    'db_path': 'data/marketplace.db',
}

async def main():
    m = MarketplaceScraperModule(config)
    await m.initialize()
    result = await m.run_scrape_cycle()
    print('Result:', result)

asyncio.run(main())
"
```

---

## Features

✓ **Playwright-based scraping** — Browser automation with stealth config, human-like delays, retry logic
✓ **Deal scoring** — 0-100 point system across 5 factors (price vs median, urgency keywords, recency, images, distance)
✓ **Vehicle financing deal evaluation** — Manual 1..10 deal comparison with payment math, total cost, rank, and pursue / negotiate / pass guidance
✓ **Smart filtering** — Chainable pipeline: price range, keywords, distance, category, score threshold
✓ **Persistent storage** — SQLite database with dedup, scoring breakdown, run history
✓ **Dashboard integration** — FastAPI module with SSE streaming, React panel with live updates
✓ **Dark UI** — Operator aesthetic: score glow badges, urgency chips, expandable details
✓ **Type-safe** — Full type hints, Pydantic config validation, async/await throughout

## Vehicle Financing Deal Optimizer MVP

A minimal evaluator API is available for manually comparing up to 10 vehicle purchase scenarios without changing the scraping or ingestion flows.

### Endpoint

`POST /api/vehicle-deals/evaluate`

### Request body

```json
{
  "deals": [
    {
      "listing_title": "2020 Honda Civic EX",
      "asking_price": 18900,
      "year": 2020,
      "make": "Honda",
      "model": "Civic",
      "mileage": 48000,
      "apr": 8.9,
      "loan_term_months": 72,
      "down_payment": 2000,
      "estimated_taxes_and_fees": 1500,
      "distance_miles": 22,
      "estimated_fair_market_value": 21500,
      "trim": "EX",
      "condition_score": 8,
      "seller_type": "dealer",
      "inventory_age_days": 37,
      "title_status": "clean"
    }
  ]
}
```

### Response shape

Each evaluated deal returns:
- `rank`
- `financed_amount`
- `estimated_monthly_payment`
- `total_interest_paid`
- `total_acquisition_cost`
- `market_spread`
- `distance_penalty`
- `deal_score`
- `recommendation`
- `reason_to_act`
- `top_risks`

The response also includes `best_deal` for the top-ranked option.

---

## Architecture

```
Facebook Marketplace (target) ← Playwright (browser automation)
                                    ↓
                          PlaywrightScraper (scraper.py)
                                    ↓
                          ResaleScorer (scorer.py)
                                    ↓
                          FilterEngine (filters.py)
                                    ↓
                          MarketplaceStorage (storage.py)
                                    ↓
                          MarketplaceScraperModule (module.py)
                                    ↓
                          FastAPI routes / React panel
                                    ↓
                          Dashboard display
```

See `architecture.md` for full system design, ASCII diagrams, and data flow.

---

## File Structure

```
marketplace_scraper/
├── architecture.md                           # System design & docs
├── setup_instructions.md                     # Installation guide
├── requirements.txt                          # Python dependencies
├── .gitignore                               # Git ignore rules
└── backend/modules/marketplace_scraper/
    ├── __init__.py                          # Public API
    ├── scraper.py                           # Playwright automation (952 lines)
    ├── scorer.py                            # Deal scoring (558 lines)
    ├── filters.py                           # Filter pipeline (503 lines)
    ├── config_schema.py                     # Pydantic config (317 lines)
    ├── storage.py                           # SQLite storage (597 lines)
    └── module.py                            # Dashboard integration (355 lines)
└── frontend/modules/
    └── MarketplacePanel.jsx                 # React panel (739 lines)
```

---

## Scoring Breakdown

Each listing gets a 0-100 score based on:

| Factor | Points | Logic |
|--------|--------|-------|
| **Price** | 0-40 | % below category median (60% below = 40 pts) |
| **Urgency** | 0-20 | Keywords ("moving", "must sell", "obo", etc.) |
| **Recency** | 0-15 | Days listed (newer = higher) |
| **Images** | 0-10 | Count (more legitimacy signal) |
| **Distance** | 0-15 | Miles from user (closer = higher) |

---

## Monetization Paths

1. **Personal flipping** — Buy low, resell high. The obvious use case.
2. **Alerts-as-a-service** — $9–19/month per user for deal notifications
3. **Market price data API** — Sell historical pricing/trend data
4. **White-label SaaS** — Reseller tool at $49–149/month
5. **Cross-platform automation** — Auto-list to eBay, Craigslist after purchase
6. **Inventory management** — Integrate with Vendoo, SellerBoard, Shopify

See `architecture.md` Section 6 for details.

---

## Legal & ToS

⚠️ **Important:** Automated scraping of Facebook Marketplace likely violates Facebook's Terms of Service (Section 3.2). This tool is provided for **educational and research purposes only**. Users assume all legal and ethical responsibility.

**Recommended safe approach:**
- Personal use only (not commercial)
- Single account, low frequency
- Rate limiting and human-like delays (built-in)
- No PII harvesting
- Don't resell raw data
- Honor rate-limit headers

See `architecture.md` Section 5 for full ToS/legal discussion.

---

## Configuration

All settings in `MarketplaceScraperConfig` (Pydantic):

```python
{
    "search_queries": ["macbook pro", "ps5"],
    "location_city": "austin",
    "location_zip": "78701",
    "radius_miles": 40,
    "price_min": 20,
    "price_max": 800,
    "min_resale_score": 55,
    "refresh_interval_minutes": 30,
    "max_pages_per_query": 3,
    "cookies_path": "data/fb_cookies.json",
    "db_path": "data/marketplace.db",
    "excluded_keywords": ["broken", "parts only", "for parts"],
    "headless": True,
}
```

See `config_schema.py` for all 21 options with validators and descriptions.

---

## Dashboard Integration

### FastAPI Routes

```python
POST  /api/modules/marketplace_scraper/trigger     # Run scrape now
GET   /api/modules/marketplace_scraper/fetch       # Current opportunities
GET   /api/modules/marketplace_scraper/stream      # SSE for live updates
POST  /api/modules/marketplace_scraper/action/mark_sold
POST  /api/modules/marketplace_scraper/action/mark_contacted
GET   /api/modules/marketplace_scraper/history     # Run history
```

### React Panel Features

- Sorted opportunity cards with score glow badges
- Color-coded urgency (🔥 Hot / ✓ Good / ~ Fair)
- Expandable score breakdowns with detailed explanations
- Urgency keyword chips ("moving", "must sell", etc.)
- "% below median" price indicator
- Filter bar: keyword search, category, max price, min score slider
- SSE auto-refresh with polling fallback
- "Mark Sold" / "Mark Contacted" actions
- Live scan indicator with stats (active listings, hot deals count)

---

## Troubleshooting

See `setup_instructions.md` for:
- Cookie export & refresh
- Playwright timeout issues
- Zero results debugging
- Config validation errors
- Database errors

---

## Performance Notes

**Typical run times:**
- Per-query scrape: 2–5 minutes (including detail page visits)
- Multi-query scrape (3 queries): 8–15 minutes
- Filtering/scoring: <1 second
- Storage: <1 second

**Resource usage:**
- RAM: ~100–300 MB (headless Chromium + Python)
- Disk: ~5–50 MB (SQLite, up to 1000 listings)
- Network: Bandwidth-light; respects Facebook's rate limits

---

## Dependencies

- `playwright>=1.43.0` — Browser automation
- `aiosqlite>=0.20.0` — Async SQLite
- `pydantic>=2.0.0` — Config validation
- Standard library: `asyncio`, `logging`, `json`, `re`, `pathlib`, `datetime`, `random`

---

## Author

Built for the Operator Dashboard. Version 1.0.0.

---

## License

Educational/research use only. See ToS notes in `architecture.md`.
