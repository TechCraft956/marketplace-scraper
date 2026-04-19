"""
PublicSurplus scraper — government & municipal surplus auctions.

PublicSurplus (publicsurplus.com) lists equipment, vehicles, and tools
from government agencies. Listings are publicly viewable without login.

source="publicsurplus"
"""
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.publicsurplus.com"

PS_CATEGORIES = {
    "all": "0",
    "vehicles": "1",
    "heavy-equipment": "3",
    "trucks": "1",
    "trailers": "1",
    "electronics": "10",
    "tools": "9",
    "office": "8",
    "construction": "3",
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
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def scrape_publicsurplus(
    query: str = "",
    category: str = "all",
    max_price: Optional[float] = None,
    max_results: int = 50,
    state: str = "",
) -> dict:
    """
    Scrape PublicSurplus auction listings.

    Returns:
        {"listings": [...], "total_found": int, "source_url": str, "error": str|None}
    """
    cat_id = PS_CATEGORIES.get(category.lower(), "0")
    params: dict = {
        "aID": "",
        "catId": cat_id,
        "selCat": "",
        "selState": state or "0",
        "searchTxt": query or "",
    }
    source_url = f"{BASE_URL}/sms/browse/home?{urlencode(params)}"
    logger.info("PublicSurplus scrape: %s", source_url)

    session = _session()
    listings = []

    try:
        resp = session.get(source_url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        error_msg = f"Request failed: {e}"
        logger.error("PublicSurplus scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": source_url, "error": error_msg}

    soup = BeautifulSoup(resp.text, "html.parser")

    # Each .auction-item contains .auction-item-body with the title link,
    # price, and end time packed into text like "Price:$255.00Ends:1 day 21 hours"
    items = soup.select(".auction-item")

    for item in items:
        try:
            body = item.select_one(".auction-item-body")
            if not body:
                continue

            title_tag = body.select_one("a[href*='/sms/auction']")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            # Strip leading lot number "#1234567 - "
            title = re.sub(r"^#\d+\s*-\s*", "", title).strip()
            if not title:
                continue

            href = title_tag.get("href", "")
            if href and not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            # Parse "Price:$255.00Ends:1 day 21 hours" from body text
            body_text = body.get_text(" ", strip=True)
            price_match = re.search(r"Price:\s*\$?([\d,]+(?:\.\d+)?)", body_text)
            price_text = price_match.group(0) if price_match else ""
            price = float(price_match.group(1).replace(",", "")) if price_match else None

            if max_price and price and price > max_price:
                continue

            end_match = re.search(r"Ends?:\s*(.+?)(?:Price|$)", body_text)
            end_text = end_match.group(1).strip() if end_match else ""

            # State/location from the image div
            img_div = item.select_one(".auction-item-img")
            location = img_div.get_text(strip=True) if img_div else ""

            # Image
            img_tag = item.select_one("img")
            image_url = ""
            if img_tag:
                src = img_tag.get("src") or img_tag.get("data-src", "")
                if src and not src.startswith("http"):
                    src = urljoin(BASE_URL, src)
                image_url = src

            listings.append({
                "title": title,
                "price": price,
                "price_raw": price_text,
                "listing_url": href,
                "location": location,
                "description": f"Government surplus auction. {f'Ends: {end_text}' if end_text else ''}".strip(),
                "image_url": image_url,
                "image_count": 1 if image_url else 0,
                "posted_at": None,
                "source": "publicsurplus",
            })

            if len(listings) >= max_results:
                break

        except Exception as exc:
            logger.debug("PublicSurplus item parse error: %s", exc)

    time.sleep(random.uniform(1.0, 2.0))

    if not listings:
        logger.warning(
            "PublicSurplus returned 0 listings — site structure may have changed "
            "or requires login for this category."
        )

    return {
        "listings": listings,
        "total_found": len(listings),
        "source_url": source_url,
        "error": None,
    }
