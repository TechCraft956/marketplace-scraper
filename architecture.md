# Facebook Marketplace Deal Scraper — System Architecture

## Overview

A production-grade deal-hunting module designed to integrate with an existing Operator Dashboard (FastAPI + React). It scrapes Facebook Marketplace using browser automation, normalizes and scores listings, persists opportunities in a local SQLite database, and surfaces them through a modular dashboard panel.

---

## Section 1 — Full System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          OPERATOR DASHBOARD SYSTEM                              │
│                                                                                 │
│  ┌───────────────────────────┐        ┌──────────────────────────────────────┐  │
│  │     FastAPI Backend       │        │        React Frontend                │  │
│  │                           │        │                                      │  │
│  │  ┌─────────────────────┐  │        │  ┌────────────────────────────────┐  │  │
│  │  │   Module Registry   │  │        │  │     Dashboard Plugin Host      │  │  │
│  │  │  (plugin loader)    │  │        │  │   (dynamic panel mounting)     │  │  │
│  │  └────────┬────────────┘  │        │  └──────────────┬─────────────────┘  │  │
│  │           │               │        │                 │                    │  │
│  │  ┌────────▼────────────┐  │        │  ┌──────────────▼─────────────────┐  │  │
│  │  │  BaseModule (ABC)   │  │        │  │      MarketplacePanel.jsx      │  │  │
│  │  │  - fetch()          │  │        │  │  - Opportunity cards           │  │  │
│  │  │  - stream()         │  │        │  │  - Filter bar                  │  │  │
│  │  │  - config_schema    │  │        │  │  - Score badges                │  │  │
│  │  └────────┬────────────┘  │        │  │  - Auto-refresh via SSE        │  │  │
│  │           │               │        │  └──────────────▲─────────────────┘  │  │
│  │  ┌────────▼────────────────────────────────────────┐ │                    │  │
│  │  │           MarketplaceScraperModule              │ │                    │  │
│  │  │                                                 │ │                    │  │
│  │  │  ┌─────────────┐  ┌──────────┐  ┌───────────┐  │ │                    │  │
│  │  │  │  Scraper    │  │  Scorer  │  │  Filter   │  │ │                    │  │
│  │  │  │  Engine     │→ │  Engine  │→ │  Engine   │  │ │                    │  │
│  │  │  │ (Playwright)│  │(0-100 pt)│  │(chainable)│  │ │                    │  │
│  │  │  └──────┬──────┘  └──────────┘  └─────┬─────┘  │ │                    │  │
│  │  │         │                              │        │ │                    │  │
│  │  │  ┌──────▼──────────────────────────────▼──────┐ │ │                    │  │
│  │  │  │           Storage Layer (SQLite)            │─┼─┘                    │  │
│  │  │  │  listings | scores | scrape_runs            │ │                    │  │
│  │  │  └─────────────────────────────────────────────┘ │                    │  │
│  │  └─────────────────────────────────────────────────-┘                    │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘

EXTERNAL DEPENDENCIES
┌──────────────────────┐    ┌─────────────────────────┐    ┌──────────────────┐
│  Facebook Marketplace│    │  Playwright/Chromium     │    │  cookies.json    │
│  (target site)       │◄───│  (headless browser)     │◄───│  (FB auth session│
└──────────────────────┘    └─────────────────────────┘    └──────────────────┘
                                        │
                               ┌────────▼────────┐
                               │  Proxy Rotator  │
                               │  (optional)     │
                               └─────────────────┘
```

---

## Section 2 — Component Breakdown

### 2.1 Scraper Engine (`scraper.py`)
The core browser-automation layer. Manages Playwright sessions, handles Facebook authentication via stored cookies, scrolls through infinite-feed listings, extracts raw HTML data, and returns normalized listing dicts. Implements human-like timing, retry logic, and stealth fingerprinting.

**Responsibilities:**
- Launch/manage headless Chromium context with stealth settings
- Load and persist Facebook session cookies
- Navigate to Marketplace search URLs with parameterized queries
- Extract listing cards from the DOM using multi-strategy CSS selectors
- Handle infinite scroll / pagination
- Emit structured raw listing dicts

### 2.2 Filter Layer (`filters.py`)
A chainable filter pipeline applied after scraping. Eliminates listings that don't meet the operator's criteria before scoring overhead is incurred.

**Responsibilities:**
- Price range filtering (min/max)
- Geographic radius check
- Category allow-list
- Keyword include/exclude (title + description)
- Minimum score gate (applied post-scoring)

### 2.3 Scoring Engine (`scorer.py`)
Assigns a 0–100 "deal potential" score to each normalized listing. Higher scores indicate stronger resale/flip opportunities.

**Scoring Factors:**
- Price vs. category market median (up to 40 pts)
- Urgency keywords in title (up to 20 pts)
- Recency of listing (up to 15 pts)
- Image count / legitimacy signal (up to 10 pts)
- Distance from user location (up to 15 pts)

### 2.4 Storage Layer (`storage.py`)
A lightweight SQLite database (via `aiosqlite`) for persisting listings, scores, and run history. Designed to be local-first with zero external dependencies.

**Tables:**
- `listings` — deduplicated by `listing_url`
- `scores` — score + factor breakdown per listing
- `scrape_runs` — timestamp, query params, counts per run

### 2.5 Dashboard Module (`module.py`)
The integration glue. Implements the `BaseModule` interface expected by the Operator Dashboard's plugin loader. Orchestrates the scrape → filter → score → store → surface pipeline.

**Responsibilities:**
- Expose `fetch()` returning a `DashboardPayload` snapshot
- Expose `stream()` as an async generator for SSE push updates
- Validate config via Pydantic schema
- Schedule background refresh at `refresh_interval_minutes`

---

## Section 3 — Data Flow

```
[Config: queries, location, filters, schedule]
              │
              ▼
  ┌───────────────────────┐
  │  1. SCRAPE            │
  │  PlaywrightScraper    │
  │  → raw listing dicts  │
  └───────────┬───────────┘
              │  {title, price, location, distance,
              │   image_url, listing_url, posted_at, description}
              ▼
  ┌───────────────────────┐
  │  2. NORMALIZE         │
  │  Price parsing,       │
  │  date normalization,  │
  │  distance extraction  │
  └───────────┬───────────┘
              │
              ▼
  ┌───────────────────────┐
  │  3. PRE-FILTER        │
  │  FilterEngine         │
  │  (price, category,    │
  │   keywords, radius)   │
  └───────────┬───────────┘
              │  (reduced set)
              ▼
  ┌───────────────────────┐
  │  4. SCORE             │
  │  ResaleScorer         │
  │  → score 0-100        │
  │  + factor breakdown   │
  └───────────┬───────────┘
              │
              ▼
  ┌───────────────────────┐
  │  5. POST-FILTER       │
  │  min_score gate       │
  └───────────┬───────────┘
              │
              ▼
  ┌───────────────────────┐
  │  6. STORE             │
  │  SQLite upsert        │
  │  dedup by URL         │
  └───────────┬───────────┘
              │
              ▼
  ┌───────────────────────┐
  │  7. SURFACE           │
  │  DashboardPayload     │
  │  → fetch() snapshot   │
  │  → stream() SSE push  │
  └───────────────────────┘
              │
              ▼
  ┌───────────────────────┐
  │  8. DISPLAY           │
  │  MarketplacePanel.jsx │
  │  Cards + Filter UI    │
  └───────────────────────┘
```

---

## Section 4 — Scraping Methodology

### Why Playwright over Raw HTTP

Facebook Marketplace is a fully client-rendered Single Page Application (SPA). Raw HTTP requests return a skeleton HTML shell with no listing data — the actual content is injected by React.js after multiple authenticated API calls using session tokens embedded in cookies. Additionally:

- **Authentication required**: Marketplace requires a logged-in Facebook session. Maintaining this via raw requests requires replicating complex cookie and header sequences.
- **Dynamic content**: Listing feeds are loaded lazily via internal GraphQL APIs with obfuscated endpoint tokens that rotate frequently.
- **Bot detection**: Facebook employs sophisticated fingerprinting (canvas, WebGL, navigator properties, mouse movement patterns) that raw HTTP clients trivially fail.
- **Infinite scroll**: Pagination is handled client-side via Intersection Observer — there are no stable "next page" URLs to hit sequentially.

**Playwright with a real Chromium engine** sidesteps all of these issues: the browser handles authentication, JS execution, cookie management, and scroll events exactly as a real user would, making detection significantly harder.

### Session / Cookie Management Strategy

```python
# Strategy: export cookies from a manually logged-in browser session
# Store in cookies.json, load on scraper startup

# Login flow (one-time manual step):
# 1. Launch headed Playwright: playwright codegen facebook.com
# 2. Log in normally (including 2FA if required)
# 3. Export: context.cookies() → save to cookies.json
# 4. Scraper loads cookies.json at startup → skips login entirely

# Session refresh:
# - Monitor for login redirect (url contains /login)
# - If detected, emit warning and pause (require manual cookie refresh)
# - Never attempt automated login (violates ToS, triggers security alerts)
```

Cookies should be stored encrypted at rest if the system handles multiple users. For personal use, a plaintext `cookies.json` in a secure directory is acceptable.

### Rate Limiting and Human-like Delays

```
Between page navigations:   random 3.0 – 6.0 seconds
Between scroll events:      random 0.8 – 2.2 seconds
Between search queries:     random 8.0 – 15.0 seconds
Between scrape sessions:    configured refresh_interval_minutes (default: 30)
Max pages per query:        configurable (default: 3)
Max concurrent contexts:    1 (never parallel-scrape the same account)
```

Delays use `random.uniform()` seeded differently each run. Mouse movement simulation and scroll jitter are added via Playwright's `mouse.move()` with random intermediate waypoints.

### Stealth Configuration

The scraper applies the following anti-detection measures:

- **User agent spoofing**: Use a recent, real Chrome user agent string
- **WebDriver flag removal**: `Object.defineProperty(navigator, 'webdriver', {get: () => undefined})`
- **Navigator properties**: Spoof `navigator.plugins`, `navigator.languages`, `navigator.hardwareConcurrency`
- **Viewport randomization**: Random viewport within common screen size ranges
- **Timezone matching**: Set browser timezone to match the target location
- **`playwright-stealth` package**: Applies ~20 additional patches (canvas fingerprint noise, WebGL vendor spoofing, etc.)

### Proxy Rotation (Optional)

For high-frequency use or multiple accounts, rotating residential proxies reduce the risk of IP-level bans:

```python
# Supported proxy config in Playwright:
browser = await playwright.chromium.launch(
    proxy={
        "server": "http://proxy-provider.com:8080",
        "username": "user",
        "password": "pass"
    }
)
# Rotate proxy per scrape session, not per request
# Residential proxies (Bright Data, Oxylabs, Smartproxy) >> datacenter proxies
# Match proxy geo to search location for authenticity
```

### Pagination and Infinite Scroll Handling

Facebook Marketplace uses infinite scroll. The scraper handles this by:

1. Navigating to the search URL
2. Extracting all visible listing cards
3. Scrolling to the bottom of the page using `page.evaluate("window.scrollTo(0, document.body.scrollHeight)")`
4. Waiting for network idle (new cards to load)
5. Comparing card count before/after scroll — if unchanged, pagination is exhausted
6. Repeating up to `max_pages` scroll cycles (each "page" ≈ one scroll batch of ~20 listings)

---

## Section 5 — ToS / Legal Considerations

### Important Disclaimer

> **This software is provided for educational and research purposes. Automated scraping of Facebook Marketplace likely violates Facebook's Terms of Service (Section 3.2: "You will not collect users' content or information using automated means"). The authors make no warranties regarding its use. Users assume all legal and ethical responsibility.**

### Recommended Safe Approach

| Practice | Reason |
|----------|---------|
| **Personal use only** | Commercial scraping is higher legal risk |
| **Single account, low frequency** | Avoid ToS triggers; rate limits protect your account |
| **Respect `robots.txt`** | `facebook.com/robots.txt` disallows `/marketplace/` for bots — acknowledge this |
| **No PII harvesting** | Never store seller personal information (phone, email) |
| **No resale of raw data** | Scraped FB data cannot be commercially redistributed |
| **Honor `Retry-After` headers** | If rate-limited, back off for the specified duration |
| **Delete stale data** | Don't retain listing data longer than necessary |
| **Consider the Craigslist/LinkedIn precedent** | hiQ v. LinkedIn (9th Cir.) found public data scraping may be protected, but FB is authenticated — different legal footing |

The safest posture: treat this as a personal productivity tool that reads your own Marketplace feed on your behalf, similar to how a browser extension would.

---

## Section 6 — Monetization Options

### 6.1 Personal Flipping (Primary Use Case)
Buy underpriced items identified by high resale scores and resell at market value on eBay, Craigslist, or other Marketplace listings. The scoring engine is specifically designed to surface items priced 30–60% below category median — the classic arbitrage signal.

**Target categories:** Electronics, tools, furniture, musical instruments, sporting goods, vintage items.

**Margin model:** 
- Acquire at 40–60% of market value
- Resell at 85–100% of market value
- Net margin: 25–45% minus time, transport, and listing fees

### 6.2 Alerts-as-a-Service
Build a thin SaaS layer on top of this module:
- Users configure their search criteria and minimum score via a web UI
- The system runs scrapes on their behalf (on your server)
- Notify via SMS (Twilio), email (SendGrid), or push notification when a deal above their threshold is found
- **Monetization:** $9–19/month subscription per user; tiered by number of active search queries and alert frequency

**Stack additions needed:** User auth, Stripe billing, notification service, multi-tenant storage.

### 6.3 Market Price Data / Analytics API
The scrape_runs table accumulates historical pricing data over time. This becomes a price intelligence dataset:
- Category-level price trend API: "What's the median price for a PS5 in Austin, TX this month?"
- Price depreciation curves by item category
- Demand signals (how quickly items sell)
- **Monetization:** API access at $0.01/query or $99–299/month for bulk access

### 6.4 White-Label SaaS for Resellers
Package the entire system as a branded product for professional resellers:
- Custom domain, branded UI
- Multi-city / multi-query monitoring
- Inventory tracking (mark items as purchased, calculate flip margins)
- CRM-lite for tracking seller negotiations
- **Monetization:** $49–149/month; B2B target is flea market vendors, eBay PowerSellers, estate sale flippers

### 6.5 Cross-Platform Listing Automation
Extend the module with outbound posting capabilities:
- Detect a high-score deal on Facebook Marketplace
- After purchase, auto-draft a listing for eBay, Craigslist, and OfferUp using the original images and a rewritten description
- Track sell-through rate and net profit per flip
- **Monetization:** SaaS feature add-on ($20–30/month) or integrated into the reseller platform above

### 6.6 Integration with Inventory Management Tools
API hooks into existing reseller tools:
- **Vendoo / List Perfectly**: Auto-draft cross-platform listings
- **InventoryLab / Sellerboard**: Cost basis tracking for Amazon FBA
- **Shopify**: Auto-import as draft products for retail arbitrage stores
- **Monetization:** Integration marketplace revenue share, or bundled in the white-label SaaS tier
