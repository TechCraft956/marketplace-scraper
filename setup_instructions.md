# Setup Instructions — Facebook Marketplace Scraper Module

## Prerequisites
- Python 3.10+
- Node.js 18+ (for the React frontend)
- An active Facebook account (required for Marketplace access)
- The Operator Dashboard (FastAPI + React) already running

---

## Step 1 — Install Python Dependencies

```bash
# Navigate to your dashboard project root
cd /path/to/your/operator-dashboard

# Install module dependencies
pip install -r backend/modules/marketplace_scraper/requirements.txt

# Install Playwright and download the Chromium browser binary
playwright install chromium

# Verify Playwright works
python -c "from playwright.async_api import async_playwright; print('OK')"
```

---

## Step 2 — Create the Data Directory

```bash
mkdir -p data
# This is where cookies.json and marketplace.db will live
```

---

## Step 3 — Facebook Login and Cookie Export

The scraper authenticates using a stored cookie session. You only need to do this once (sessions typically last weeks to months).

### Option A: Export from your browser (recommended)

1. **Install the "Cookie-Editor" browser extension** (Chrome/Firefox) — or any similar cookie export tool
2. Log into Facebook in your browser as normal
3. Navigate to `facebook.com/marketplace`
4. Open Cookie-Editor → Export → Export as JSON
5. Save the file to `data/fb_cookies.json`

### Option B: Use Playwright's codegen to capture a session

```bash
# Launch a headed Playwright session (this opens a real browser window)
python -c "
import asyncio
from playwright.async_api import async_playwright

async def capture():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto('https://www.facebook.com/login')
        print('Log in to Facebook in the browser window that just opened.')
        print('Navigate to Marketplace, then press ENTER here when done.')
        input()
        cookies = await context.cookies()
        import json
        with open('data/fb_cookies.json', 'w') as f:
            json.dump(cookies, f, indent=2)
        print(f'Saved {len(cookies)} cookies to data/fb_cookies.json')
        await browser.close()

asyncio.run(capture())
"
```

### Verifying the cookie session works

```bash
python -c "
import asyncio, json
from backend.modules.marketplace_scraper.scraper import PlaywrightScraper

async def test():
    async with PlaywrightScraper(cookies_path='data/fb_cookies.json', headless=False) as s:
        results = await s.search('laptop', location='austin', max_pages=1)
        print(f'Found {len(results)} listings')
        if results:
            print('First result:', results[0]['title'], results[0]['price'])

asyncio.run(test())
"
```

If the browser opens and shows Marketplace results (not a login page), you're authenticated.

---

## Step 4 — Register the Module with the Dashboard

### 4a. Add to your FastAPI app

In your dashboard's `main.py` or `app.py`:

```python
from backend.modules.marketplace_scraper import MarketplaceScraperModule

# Module config
marketplace_config = {
    "search_queries": ["macbook pro", "ps5", "iphone", "mechanical keyboard"],
    "location_city": "austin",
    "location_zip": "78701",
    "radius_miles": 40,
    "price_min": 20,
    "price_max": 1500,
    "min_resale_score": 55,
    "refresh_interval_minutes": 30,
    "max_pages_per_query": 3,
    "cookies_path": "data/fb_cookies.json",
    "db_path": "data/marketplace.db",
    "excluded_keywords": ["broken", "parts only", "for parts", "cracked screen", "damaged"],
    "headless": True,
}

marketplace_module = MarketplaceScraperModule(config=marketplace_config)

@app.on_event("startup")
async def startup():
    await marketplace_module.initialize()
```

### 4b. Add API routes

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
import json

router = APIRouter(prefix="/api/modules/marketplace_scraper")

@router.get("/fetch")
async def fetch():
    payload = await marketplace_module.fetch()
    return payload.to_dict()

@router.post("/trigger")
async def trigger():
    # Run scrape in background (non-blocking)
    asyncio.create_task(marketplace_module.run_scrape_cycle())
    return {"status": "triggered"}

@router.get("/stream")
async def stream():
    """Server-Sent Events endpoint for real-time updates."""
    async def event_generator():
        async for payload in marketplace_module.stream():
            data = json.dumps(payload.to_dict())
            yield f"data: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )

@router.post("/action/mark_sold")
async def mark_sold(body: dict):
    return await marketplace_module.mark_sold(body["listing_url"])

@router.post("/action/mark_contacted")
async def mark_contacted(body: dict):
    return await marketplace_module.mark_contacted(body["listing_url"])

@router.get("/history")
async def history():
    return await marketplace_module.get_run_history()

app.include_router(router)
```

### 4c. Register the React panel

In your dashboard's frontend module registry (e.g. `src/modules/registry.js`):

```javascript
import MarketplacePanel from "./MarketplacePanel";

export const MODULE_REGISTRY = {
  // ... your existing modules
  marketplace_scraper: {
    component: MarketplacePanel,
    title: "Marketplace Scanner",
    icon: "🛒",
    description: "Facebook Marketplace deal finder",
  },
};
```

---

## Step 5 — Run the First Scrape

### Option A: Via dashboard UI
1. Start your dashboard (`uvicorn main:app --reload`)
2. Open the dashboard in your browser
3. Navigate to the Marketplace Scanner panel
4. Click "Scan Now"
5. Wait 2-5 minutes depending on number of queries and `max_pages_per_query`

### Option B: Direct Python call

```bash
python -c "
import asyncio
from backend.modules.marketplace_scraper import MarketplaceScraperModule

config = {
    'search_queries': ['macbook pro', 'ps5'],
    'location_city': 'austin',
    'price_max': 800,
    'min_resale_score': 50,
    'cookies_path': 'data/fb_cookies.json',
    'db_path': 'data/marketplace.db',
}

async def run():
    module = MarketplaceScraperModule(config=config)
    await module.initialize()
    result = await module.run_scrape_cycle()
    print('Run result:', result)

    # View top opportunities
    payload = await module.fetch()
    print(f'Top deals ({len(payload.data)} found):')
    for deal in payload.data[:5]:
        print(f'  [{deal[\"score\"]:.0f}] {deal[\"title\"]} — \${deal[\"price\"]}')

asyncio.run(run())
"
```

---

## Troubleshooting

### "Session not authenticated" error
- Your `cookies.json` file is missing, expired, or corrupt
- Re-export cookies from your browser (Step 3)
- Try setting `headless: False` temporarily to watch what happens

### Playwright timeout errors
- Facebook loaded slowly — increase timeout in `scraper.py` (`timeout=30000` → `timeout=60000`)
- Your IP may be rate-limited — wait 15-30 minutes before retrying
- Add a proxy configuration if persistent

### No listings found (0 results)
- Facebook may have changed their DOM structure — CSS selectors need updating
- Check `headless=False` and watch the browser manually navigate
- Try a different `location_city` slug (e.g. `"dallas"` instead of `"dallas-tx"`)

### Module config validation error
- Run `python -c "from backend.modules.marketplace_scraper.config_schema import MarketplaceScraperConfig; c = MarketplaceScraperConfig(**your_config); print(c)"` to see validation errors

### Database errors
- Ensure the `data/` directory exists: `mkdir -p data`
- Delete `data/marketplace.db` to start fresh

---

## Cookie Refresh Schedule

Facebook session cookies typically last 30-90 days. When the scraper detects a login redirect, it will log a warning:

```
WARNING - Session not authenticated. Please log into Facebook manually
and export cookies to data/fb_cookies.json
```

Set up a reminder to refresh cookies monthly. The scraper will continue using stored data while you refresh.

---

## Security Notes

- Store `cookies.json` in a secure location — it grants full access to your Facebook account
- Never commit `cookies.json` to version control — add it to `.gitignore`
- The SQLite database contains listing data only, no personal credentials
- If deploying on a server, ensure the `data/` directory has appropriate permissions (`chmod 700 data/`)
