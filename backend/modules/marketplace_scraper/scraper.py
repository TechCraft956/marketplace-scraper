"""
Facebook Marketplace Playwright-based async scraper.

Uses headless Chromium with stealth settings, cookie-based auth,
human-like delays, and multi-strategy CSS selectors.

DISCLAIMER: Automated scraping may violate Facebook's ToS.
Use responsibly, for personal/research purposes only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlencode

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
        TimeoutError as PlaywrightTimeout,
    )
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = BrowserContext = Page = Playwright = None
    PlaywrightTimeout = TimeoutError
    async_playwright = None
    logger.warning("playwright not installed — scraper module disabled")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RawListing:
    title: str
    price: Optional[float]
    price_raw: str
    location: str
    distance: Optional[float]        # miles
    image_url: Optional[str]
    image_count: int
    listing_url: str
    posted_at: Optional[datetime]
    posted_raw: str
    description: str
    category: Optional[str]
    seller_name: Optional[str]
    scraped_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "price": self.price,
            "price_raw": self.price_raw,
            "location": self.location,
            "distance": self.distance,
            "image_url": self.image_url,
            "image_count": self.image_count,
            "listing_url": self.listing_url,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "posted_raw": self.posted_raw,
            "description": self.description,
            "category": self.category,
            "seller_name": self.seller_name,
            "scraped_at": self.scraped_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Stealth JS patches applied to every new page
# ---------------------------------------------------------------------------

STEALTH_SCRIPTS = [
    # Remove webdriver flag
    """
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });
    """,
    # Spoof plugins (non-empty array)
    """
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
        configurable: true
    });
    """,
    # Spoof languages
    """
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });
    """,
    # Spoof hardware concurrency
    """
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });
    """,
    # Chrome runtime (Playwright sets this, but reinforce)
    """
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };
    """,
    # Permissions API spoof
    """
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters)
    );
    """,
]


# ---------------------------------------------------------------------------
# Selector strategies for Facebook Marketplace
# ---------------------------------------------------------------------------
# FB uses obfuscated class names that change with deploys.
# We use data-testid, aria-label, href patterns, and structural selectors
# as fallback chains. Order = most specific → least specific.

LISTING_CARD_SELECTORS = [
    # Primary: anchor tags pointing to /marketplace/item/
    "a[href*='/marketplace/item/']",
    # Fallback: data-testid patterns
    "[data-testid='marketplace_feed_item'] a",
    # Fallback: role-based
    "div[role='article'] a[href*='marketplace']",
]

LISTING_TITLE_SELECTORS = [
    # Span immediately following the price span (common structure)
    "span[dir='auto']:not(:empty)",
    # Any visible text span within the card
    "div[aria-label] span",
    # Generic fallback
    "span",
]

LISTING_PRICE_SELECTORS = [
    # Price spans typically contain $ sign
    "span:has-text('$')",
    # Data-testid
    "[data-testid='marketplace_listing_price']",
    # Aria-label containing price
    "[aria-label*='$']",
]

IMAGE_SELECTORS = [
    "img[src*='fbcdn']",
    "img[data-visualcompletion='media-vc-image']",
    "img:not([alt=''])",
    "img",
]


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

MARKETPLACE_BASE = "https://www.facebook.com/marketplace"

CATEGORY_MAP = {
    "electronics": "electronics",
    "furniture": "furniture",
    "clothing": "apparel",
    "tools": "tools",
    "sporting_goods": "sports_outdoors",
    "musical_instruments": "musical_instruments",
    "vehicles": "vehicles",
    "all": "",
}


def build_search_url(
    query: str,
    location: str,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    category: Optional[str] = None,
    radius_miles: int = 40,
) -> str:
    """
    Construct a Facebook Marketplace search URL.
    location can be a city name or zip code.
    """
    params: dict[str, Any] = {
        "query": query,
        "exact": "false",
        "radius": radius_miles,
    }
    if min_price is not None:
        params["minPrice"] = int(min_price)
    if max_price is not None:
        params["maxPrice"] = int(max_price)

    cat_slug = CATEGORY_MAP.get((category or "").lower(), "")
    if cat_slug:
        base = f"{MARKETPLACE_BASE}/{location}/{cat_slug}/search"
    else:
        base = f"{MARKETPLACE_BASE}/{location}/search"

    return f"{base}/?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Price / date parsers
# ---------------------------------------------------------------------------

def parse_price(raw: str) -> Optional[float]:
    """Extract numeric price from strings like '$1,200', 'Free', '$50 OBO'."""
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if cleaned in ("free", "$0", ""):
        return 0.0
    match = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)", cleaned)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_distance(raw: str) -> Optional[float]:
    """Parse '5 miles away' → 5.0, '0.3 miles away' → 0.3."""
    match = re.search(r"([\d.]+)\s*mile", raw, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_posted_at(raw: str) -> Optional[datetime]:
    """
    Parse relative dates like 'Listed 3 hours ago', 'Listed 2 days ago',
    'Listed a week ago', etc. Returns a UTC datetime approximation.
    """
    now = datetime.utcnow()
    raw_lower = raw.lower()

    patterns = [
        (r"(\d+)\s*minute", lambda m: now.replace(
            minute=max(0, now.minute - int(m.group(1))))),
        (r"(\d+)\s*hour", lambda m: now.replace(
            hour=max(0, now.hour - int(m.group(1))))),
        (r"(\d+)\s*day", lambda m: datetime.fromtimestamp(
            now.timestamp() - int(m.group(1)) * 86400)),
        (r"a week|1 week", lambda m: datetime.fromtimestamp(
            now.timestamp() - 7 * 86400)),
        (r"(\d+)\s*week", lambda m: datetime.fromtimestamp(
            now.timestamp() - int(m.group(1)) * 7 * 86400)),
        (r"a month|1 month", lambda m: datetime.fromtimestamp(
            now.timestamp() - 30 * 86400)),
    ]
    for pattern, fn in patterns:
        m = re.search(pattern, raw_lower)
        if m:
            try:
                return fn(m)
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
# Core scraper class
# ---------------------------------------------------------------------------

class PlaywrightScraper:
    """
    Async Facebook Marketplace scraper powered by Playwright.

    Usage:
        async with PlaywrightScraper(cookies_path="cookies.json") as scraper:
            listings = await scraper.search(
                query="macbook pro",
                location="austin",
                max_price=800,
                max_pages=3,
            )
    """

    def __init__(
        self,
        cookies_path: str = "cookies.json",
        headless: bool = True,
        proxy: Optional[dict[str, str]] = None,
        viewport_width: int = 1280,
        viewport_height: int = 900,
    ) -> None:
        self.cookies_path = Path(cookies_path)
        self.headless = headless
        self.proxy = proxy
        self.viewport_width = viewport_width + random.randint(-100, 100)
        self.viewport_height = viewport_height + random.randint(-50, 50)

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "PlaywrightScraper":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        """Launch browser and configure stealth context."""
        logger.info("Starting Playwright browser (headless=%s)", self.headless)
        self._playwright = await async_playwright().start()

        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if self.proxy:
            launch_kwargs["proxy"] = self.proxy

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict[str, Any] = {
            "viewport": {
                "width": self.viewport_width,
                "height": self.viewport_height,
            },
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
            "timezone_id": "America/Chicago",
            "permissions": ["geolocation"],
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
        }

        self._context = await self._browser.new_context(**context_kwargs)

        # Apply stealth patches to every new page
        await self._context.add_init_script(
            "\n".join(STEALTH_SCRIPTS)
        )

        # Load cookies if available
        await self._load_cookies()

        logger.info("Browser context ready")

    async def stop(self) -> None:
        """Save cookies and teardown browser."""
        if self._context:
            await self._save_cookies()
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser stopped")

    # ------------------------------------------------------------------
    # Cookie management
    # ------------------------------------------------------------------

    async def _load_cookies(self) -> None:
        if self._context and self.cookies_path.exists():
            try:
                cookies = json.loads(self.cookies_path.read_text())
                await self._context.add_cookies(cookies)
                logger.info("Loaded %d cookies from %s", len(cookies), self.cookies_path)
            except Exception as exc:
                logger.warning("Failed to load cookies: %s", exc)

    async def _save_cookies(self) -> None:
        if self._context:
            try:
                cookies = await self._context.cookies()
                self.cookies_path.write_text(json.dumps(cookies, indent=2))
                logger.debug("Saved %d cookies to %s", len(cookies), self.cookies_path)
            except Exception as exc:
                logger.warning("Failed to save cookies: %s", exc)

    # ------------------------------------------------------------------
    # Human-like interaction helpers
    # ------------------------------------------------------------------

    async def _human_delay(self, min_s: float = 1.5, max_s: float = 4.0) -> None:
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def _slow_scroll(self, page: Page, steps: int = 5) -> None:
        """Scroll down incrementally with jitter to simulate human behavior."""
        for _ in range(steps):
            scroll_amount = random.randint(300, 700)
            await page.mouse.wheel(0, scroll_amount)
            await asyncio.sleep(random.uniform(0.4, 1.1))

    async def _move_mouse_randomly(self, page: Page) -> None:
        """Move mouse to a random position to simulate human presence."""
        x = random.randint(100, self.viewport_width - 100)
        y = random.randint(100, self.viewport_height - 100)
        await page.mouse.move(x, y)

    # ------------------------------------------------------------------
    # Auth check
    # ------------------------------------------------------------------

    async def _check_logged_in(self, page: Page) -> bool:
        """Return True if the current page indicates an active FB session."""
        try:
            url = page.url
            if "/login" in url or "login_attempt" in url:
                logger.warning("Not logged in — cookie session expired or missing")
                return False
            # Look for nav items that only appear when logged in
            nav = await page.query_selector("[aria-label='Facebook'][role='banner']")
            return nav is not None
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Listing extraction
    # ------------------------------------------------------------------

    async def _extract_listing_urls(self, page: Page) -> list[str]:
        """Extract all unique /marketplace/item/ URLs visible on the page."""
        urls: set[str] = set()
        for selector in LISTING_CARD_SELECTORS:
            try:
                anchors = await page.query_selector_all(selector)
                for anchor in anchors:
                    href = await anchor.get_attribute("href")
                    if href and "/marketplace/item/" in href:
                        # Normalize to absolute URL
                        if href.startswith("/"):
                            href = f"https://www.facebook.com{href}"
                        # Strip query params for dedup (keep base listing URL)
                        base = href.split("?")[0].rstrip("/")
                        urls.add(base)
                if urls:
                    break  # Found listings, no need to try other selectors
            except Exception as exc:
                logger.debug("Selector %r failed: %s", selector, exc)

        logger.debug("Found %d unique listing URLs on page", len(urls))
        return list(urls)

    async def _extract_card_data(
        self, page: Page, listing_url: str
    ) -> Optional[RawListing]:
        """
        Visit a listing URL and extract structured data.
        Falls back gracefully on missing fields.
        """
        retries = 3
        for attempt in range(retries):
            try:
                await page.goto(listing_url, wait_until="domcontentloaded", timeout=20000)
                await self._human_delay(1.0, 2.5)
                await self._move_mouse_randomly(page)

                # ---- Title ----
                title = ""
                title_selectors = [
                    "h1",
                    "span[dir='auto'][class*='x1lliihq']",
                    "div[data-testid='marketplace_pdp_title']",
                    "meta[property='og:title']",
                ]
                for sel in title_selectors:
                    try:
                        if sel.startswith("meta"):
                            el = await page.query_selector(sel)
                            if el:
                                title = await el.get_attribute("content") or ""
                        else:
                            el = await page.query_selector(sel)
                            if el:
                                title = (await el.text_content() or "").strip()
                        if title:
                            break
                    except Exception:
                        continue

                # ---- Price ----
                price_raw = ""
                price_selectors = [
                    "div[data-testid='marketplace_pdp_price'] span",
                    "span:has-text('$')",
                    "[aria-label*='$']",
                    "div[class*='x1anpbxc'] span:first-child",
                ]
                for sel in price_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            text = (await el.text_content() or "").strip()
                            if "$" in text or text.lower() == "free":
                                price_raw = text
                                break
                    except Exception:
                        continue

                # ---- Location & Distance ----
                location = ""
                distance_raw = ""
                loc_selectors = [
                    "div[data-testid='marketplace_pdp_seller_info'] span",
                    "span:has-text('mile')",
                    "span:has-text('km')",
                    "div[class*='x1e56ztr'] span",
                ]
                for sel in loc_selectors:
                    try:
                        elements = await page.query_selector_all(sel)
                        for el in elements:
                            text = (await el.text_content() or "").strip()
                            if "mile" in text.lower() or "km" in text.lower():
                                distance_raw = text
                            elif len(text) > 3 and not text.startswith("$"):
                                location = location or text
                    except Exception:
                        continue

                # ---- Posted date ----
                posted_raw = ""
                date_selectors = [
                    "span:has-text('Listed')",
                    "abbr[data-utime]",
                    "span:has-text('ago')",
                ]
                for sel in date_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            if sel == "abbr[data-utime]":
                                ts = await el.get_attribute("data-utime")
                                if ts:
                                    posted_raw = f"timestamp:{ts}"
                            else:
                                posted_raw = (await el.text_content() or "").strip()
                            if posted_raw:
                                break
                    except Exception:
                        continue

                # ---- Description ----
                description = ""
                desc_selectors = [
                    "div[data-testid='marketplace_pdp_description']",
                    "div[class*='xz9dl7a'] span[dir='auto']",
                    "div[aria-label='Description']",
                ]
                for sel in desc_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            description = (await el.text_content() or "").strip()
                            if description:
                                break
                    except Exception:
                        continue

                # ---- Images ----
                images: list[str] = []
                for img_sel in IMAGE_SELECTORS:
                    try:
                        img_els = await page.query_selector_all(img_sel)
                        for img_el in img_els:
                            src = await img_el.get_attribute("src")
                            if src and "fbcdn" in src and src not in images:
                                images.append(src)
                        if images:
                            break
                    except Exception:
                        continue

                # ---- Category ----
                category = None
                cat_selectors = [
                    "a[href*='/marketplace/category/']",
                    "span:has-text('Category')",
                ]
                for sel in cat_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            category = (await el.text_content() or "").strip()
                            if category:
                                break
                    except Exception:
                        continue

                # ---- Seller name ----
                seller_name = None
                seller_selectors = [
                    "a[href*='/marketplace/profile/']",
                    "div[data-testid='marketplace_pdp_seller_name']",
                ]
                for sel in seller_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            seller_name = (await el.text_content() or "").strip()
                            if seller_name:
                                break
                    except Exception:
                        continue

                # ---- Parse timestamps ----
                posted_at: Optional[datetime] = None
                if posted_raw.startswith("timestamp:"):
                    try:
                        ts_int = int(posted_raw.split(":")[1])
                        posted_at = datetime.utcfromtimestamp(ts_int)
                    except Exception:
                        pass
                else:
                    posted_at = parse_posted_at(posted_raw)

                return RawListing(
                    title=title or "Unknown",
                    price=parse_price(price_raw),
                    price_raw=price_raw,
                    location=location,
                    distance=parse_distance(distance_raw),
                    image_url=images[0] if images else None,
                    image_count=len(images),
                    listing_url=listing_url,
                    posted_at=posted_at,
                    posted_raw=posted_raw,
                    description=description,
                    category=category,
                    seller_name=seller_name,
                )

            except PlaywrightTimeout:
                logger.warning(
                    "Timeout on %s (attempt %d/%d)", listing_url, attempt + 1, retries
                )
                if attempt < retries - 1:
                    await self._human_delay(3.0, 7.0)
            except Exception as exc:
                logger.error(
                    "Error extracting %s: %s (attempt %d/%d)",
                    listing_url, exc, attempt + 1, retries,
                )
                if attempt < retries - 1:
                    await self._human_delay(2.0, 5.0)

        return None

    # ------------------------------------------------------------------
    # Main search method
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        location: str = "austin",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        category: Optional[str] = None,
        radius_miles: int = 40,
        max_pages: int = 3,
        detail_scrape: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Search Facebook Marketplace and return a list of listing dicts.

        Args:
            query: Search term (e.g. "macbook pro", "ps5", "couch")
            location: City name or zip code (e.g. "austin", "78701")
            min_price: Minimum price filter
            max_price: Maximum price filter
            category: Category slug (see CATEGORY_MAP keys)
            radius_miles: Search radius
            max_pages: Number of scroll pages to load (each ~20 items)
            detail_scrape: If True, visit each listing page for full data.
                           If False, extract summary data from cards only.

        Returns:
            List of normalized listing dicts ready for the filter/scorer pipeline.
        """
        assert self._context is not None, "Call start() or use async context manager"

        url = build_search_url(
            query=query,
            location=location,
            min_price=min_price,
            max_price=max_price,
            category=category,
            radius_miles=radius_miles,
        )
        logger.info("Searching: %s", url)

        page = await self._context.new_page()
        results: list[dict[str, Any]] = []

        try:
            # Navigate to search page
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await self._human_delay(2.0, 4.0)

            # Check auth
            if not await self._check_logged_in(page):
                logger.error(
                    "Session not authenticated. Please log into Facebook manually "
                    "and export cookies to %s",
                    self.cookies_path,
                )
                return []

            # Dismiss any popups/dialogs
            await self._dismiss_popups(page)

            # Collect listing URLs across scroll pages
            all_listing_urls: list[str] = []
            seen_urls: set[str] = set()

            for page_num in range(max_pages):
                logger.info("Scrolling page batch %d/%d", page_num + 1, max_pages)

                # Extract current visible listings
                current_urls = await self._extract_listing_urls(page)
                new_urls = [u for u in current_urls if u not in seen_urls]
                seen_urls.update(new_urls)
                all_listing_urls.extend(new_urls)

                logger.info(
                    "Page %d: found %d new listings (%d total)",
                    page_num + 1, len(new_urls), len(all_listing_urls),
                )

                if page_num < max_pages - 1:
                    # Scroll down for more results
                    prev_count = len(seen_urls)
                    await self._slow_scroll(page, steps=random.randint(4, 8))
                    await self._human_delay(2.0, 4.5)

                    # Check if new items appeared
                    post_urls = await self._extract_listing_urls(page)
                    new_after_scroll = [u for u in post_urls if u not in seen_urls]
                    if not new_after_scroll:
                        logger.info("No new listings after scroll, pagination complete")
                        break

            logger.info(
                "Collected %d unique listing URLs for query %r",
                len(all_listing_urls), query,
            )

            # Scrape detail pages (or fall back to card-level extraction)
            if detail_scrape:
                for i, listing_url in enumerate(all_listing_urls):
                    logger.debug(
                        "Scraping listing %d/%d: %s",
                        i + 1, len(all_listing_urls), listing_url,
                    )
                    listing = await self._extract_card_data(page, listing_url)
                    if listing:
                        results.append(listing.to_dict())

                    # Human-like delay between listing visits
                    await self._human_delay(1.5, 3.5)

                    # Occasionally take a longer break
                    if (i + 1) % 10 == 0:
                        logger.debug("Taking extended break after 10 listings")
                        await self._human_delay(5.0, 12.0)
            else:
                # Card-level extraction only (faster, less data)
                results = await self._extract_cards_from_feed(page, all_listing_urls)

        except Exception as exc:
            logger.error("Search failed for query %r: %s", query, exc, exc_info=True)
        finally:
            await page.close()

        logger.info(
            "Search complete for %r: %d listings returned", query, len(results)
        )
        return results

    async def _dismiss_popups(self, page: Page) -> None:
        """Try to close common Facebook popups/overlays."""
        popup_selectors = [
            "[aria-label='Close']",
            "div[role='dialog'] [aria-label='Close']",
            "button:has-text('Not Now')",
            "button:has-text('Close')",
        ]
        for sel in popup_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _extract_cards_from_feed(
        self, page: Page, urls: list[str]
    ) -> list[dict[str, Any]]:
        """
        Lightweight extraction from the feed page without visiting each listing.
        Returns less data but is faster and less detectable.
        """
        results = []
        try:
            cards = await page.query_selector_all("a[href*='/marketplace/item/']")
            for card in cards:
                try:
                    href = await card.get_attribute("href") or ""
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"https://www.facebook.com{href}"
                    base_url = href.split("?")[0].rstrip("/")
                    if base_url not in urls:
                        continue

                    # Extract text from within the card anchor
                    all_text = (await card.text_content() or "").strip()
                    lines = [l.strip() for l in all_text.split("\n") if l.strip()]

                    # Heuristic: first line with $ is price, rest is title/location
                    price_raw = next(
                        (l for l in lines if "$" in l or l.lower() == "free"), ""
                    )
                    title = next(
                        (l for l in lines if l != price_raw and len(l) > 5), ""
                    )
                    location = next(
                        (
                            l for l in lines
                            if l not in (price_raw, title) and len(l) > 2
                        ),
                        "",
                    )

                    # Image
                    img_el = await card.query_selector("img")
                    image_url = await img_el.get_attribute("src") if img_el else None

                    results.append({
                        "title": title,
                        "price": parse_price(price_raw),
                        "price_raw": price_raw,
                        "location": location,
                        "distance": parse_distance(location),
                        "image_url": image_url,
                        "image_count": 1 if image_url else 0,
                        "listing_url": base_url,
                        "posted_at": None,
                        "posted_raw": "",
                        "description": "",
                        "category": None,
                        "seller_name": None,
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
                except Exception as exc:
                    logger.debug("Card extraction error: %s", exc)
        except Exception as exc:
            logger.error("Feed extraction failed: %s", exc)

        return results

    # ------------------------------------------------------------------
    # Multi-query convenience method
    # ------------------------------------------------------------------

    async def search_multiple(
        self,
        queries: list[str],
        location: str = "austin",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        category: Optional[str] = None,
        radius_miles: int = 40,
        max_pages: int = 3,
        inter_query_delay: tuple[float, float] = (8.0, 15.0),
    ) -> list[dict[str, Any]]:
        """Run multiple search queries and aggregate results with deduplication."""
        all_results: dict[str, dict[str, Any]] = {}

        for i, query in enumerate(queries):
            logger.info(
                "Query %d/%d: %r", i + 1, len(queries), query
            )
            results = await self.search(
                query=query,
                location=location,
                min_price=min_price,
                max_price=max_price,
                category=category,
                radius_miles=radius_miles,
                max_pages=max_pages,
            )
            for r in results:
                url = r.get("listing_url", "")
                if url and url not in all_results:
                    all_results[url] = r

            if i < len(queries) - 1:
                delay = random.uniform(*inter_query_delay)
                logger.info("Waiting %.1fs before next query", delay)
                await asyncio.sleep(delay)

        logger.info(
            "Multi-query complete: %d unique listings across %d queries",
            len(all_results), len(queries),
        )
        return list(all_results.values())
