"""
GovDeals scraper — fetches public government auction listings with pickup-aware location capture.
Uses requests + BeautifulSoup and normalizes pickup location into `location`.
"""
import logging
import random
import re
import time
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.govdeals.com"

GD_CATEGORIES = {
    "all": "",
    "vehicles": "cars-trucks-and-vans",
    "trailers": "trailers",
    "electronics": "computers-and-electronics",
    "tools": "tools",
    "office": "office-equipment-and-supplies",
    "heavy_equipment": "heavy-equipment",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"\$?([\d,]+(?:\.\d+)?)", text.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _build_url(query: str = "", category: str = "all", max_price: Optional[float] = None) -> str:
    category_slug = GD_CATEGORIES.get(category.lower(), "")
    params = {}
    if query:
        params["kWord"] = query
    if max_price is not None:
        params["priceTo"] = int(max_price)
    base = f"{BASE_URL}/search/{category_slug}".rstrip("/")
    if params:
        return f"{base}?{urlencode(params)}"
    return base


def scrape_govdeals(
    query: str = "",
    category: str = "all",
    max_price: Optional[float] = None,
    max_results: int = 50,
) -> dict:
    url = _build_url(query=query, category=category, max_price=max_price)
    logger.info("GovDeals scrape: %s", url)
    session = _session()
    listings = []

    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        error_msg = f"Request failed: {e}"
        logger.error("GovDeals scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select(
        ".search-result, .auction-item, .card, article, [class*='searchResult'], [class*='auction']"
    )
    if not cards:
        cards = soup.select("a[href*='asset/']")

    for card in cards:
        try:
            title_tag = card.select_one("h2, h3, .title, [class*='title'], a[href]")
            if not title_tag:
                continue
            title = title_tag.get_text(" ", strip=True)
            if not title or len(title) < 4:
                continue

            link = title_tag if title_tag.name == "a" else card.select_one("a[href]")
            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            body_text = card.get_text(" ", strip=True)
            price = None
            price_raw = ""
            for sel in (".price", ".current-bid", "[class*='price']", "[class*='bid']"):
                price_tag = card.select_one(sel)
                if price_tag:
                    price_raw = price_tag.get_text(" ", strip=True)
                    price = _parse_price(price_raw)
                    if price is not None:
                        break
            if price is None:
                m = re.search(r"(?:current bid|price)\s*[:\-]?\s*(\$?[\d,]+(?:\.\d+)?)", body_text, re.I)
                if m:
                    price_raw = m.group(1)
                    price = _parse_price(price_raw)

            location = ""
            for sel in (".location", "[class*='location']", "[class*='seller']", "[class*='agency']"):
                loc_tag = card.select_one(sel)
                if loc_tag:
                    location = loc_tag.get_text(" ", strip=True)
                    if location:
                        break
            if not location:
                m = re.search(r"(?:location|pickup location|seller)\s*[:\-]\s*([^|]+)", body_text, re.I)
                if m:
                    location = m.group(1).strip()

            end_text = ""
            m = re.search(r"(?:ends?|closing)\s*[:\-]?\s*([^|]+)", body_text, re.I)
            if m:
                end_text = m.group(1).strip()

            img_tag = card.select_one("img")
            image_url = ""
            if img_tag:
                src = img_tag.get("src") or img_tag.get("data-src") or ""
                if src and not src.startswith("http"):
                    src = urljoin(BASE_URL, src)
                image_url = src

            listings.append({
                "title": title,
                "price": price,
                "price_raw": price_raw,
                "listing_url": href,
                "location": location,
                "description": f"GovDeals auction. {f'Ends: {end_text}' if end_text else ''}".strip(),
                "image_url": image_url,
                "image_count": 1 if image_url else 0,
                "posted_at": None,
                "seller_name": "GovDeals",
                "source": "govdeals",
            })
            if len(listings) >= max_results:
                break
        except Exception as exc:
            logger.debug("GovDeals item parse error: %s", exc)

    time.sleep(random.uniform(0.8, 1.8))

    return {
        "listings": listings,
        "total_found": len(listings),
        "source_url": url,
        "error": None,
    }
