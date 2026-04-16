"""
GovPlanet scraper — Fetches equipment/vehicle auction listings.
Uses requests + BeautifulSoup to parse search result pages.
"""
import re
import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# GovPlanet category URLs
GP_CATEGORIES = {
    "all": "all-surplus",
    "construction": "Construction-Equipment",
    "trucks": "Trucks-Trailers",
    "vehicles": "Vehicles",
    "trailers": "Trailers",
    "agriculture": "Agriculture-Equipment",
    "material_handling": "Material-Handling",
    "heavy_equipment": "Heavy-Equipment",
}


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


def build_search_url(
    query: str = "",
    category: str = "all",
    max_price: Optional[float] = None,
) -> str:
    """Build a GovPlanet search URL."""
    base = "https://www.govplanet.com/for-sale"

    cat_path = GP_CATEGORIES.get(category.lower(), "all-surplus")

    params = {}
    if query:
        params["keyword"] = query
    if max_price:
        params["prHi"] = int(max_price)

    url = f"{base}/{cat_path}"
    if params:
        url += "?" + urlencode(params)
    return url


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"[\$]?([\d,]+(?:\.\d+)?)", text.replace(",", ""))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_govplanet_results(html: str, base_url: str) -> list[dict]:
    """Parse GovPlanet search results into listing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # GovPlanet uses various item card structures
    item_cards = soup.select(
        ".item-card, .search-result-item, .lot-item, "
        "[class*='ItemCard'], [class*='item-tile'], "
        ".results-list .item, article.listing"
    )

    if not item_cards:
        # Try broader selectors
        item_cards = soup.select("a[href*='/for-sale/'], a[href*='/item/']")

    for card in item_cards:
        try:
            # Title
            title_tag = card.select_one(
                "h2, h3, .item-title, .title, "
                "[class*='title'], [class*='Title'], .item-name"
            )
            if not title_tag:
                # If the card itself is an anchor, use its text
                if card.name == "a":
                    title = card.get_text(strip=True)[:100]
                else:
                    continue
            else:
                title = title_tag.get_text(strip=True)

            if not title or len(title) < 3:
                continue

            # URL
            link = card if card.name == "a" else card.select_one("a[href]")
            href = link.get("href", "") if link else ""
            if href and not href.startswith("http"):
                href = urljoin("https://www.govplanet.com", href)

            # Price / Current Bid
            price = None
            price_raw = ""
            for price_sel in [".price", ".current-bid", "[class*='price']", "[class*='Price']", "[class*='bid']"]:
                price_tag = card.select_one(price_sel)
                if price_tag:
                    price_raw = price_tag.get_text(strip=True)
                    price = parse_price(price_raw)
                    if price:
                        break

            # Image
            img_tag = card.select_one("img")
            image_url = ""
            if img_tag:
                image_url = img_tag.get("src") or img_tag.get("data-src") or img_tag.get("data-lazy-src") or ""

            # Location
            location = ""
            loc_tag = card.select_one(".location, [class*='location'], [class*='Location']")
            if loc_tag:
                location = loc_tag.get_text(strip=True)

            # Auction date
            date_tag = card.select_one(".auction-date, [class*='date'], [class*='Date'], time")
            posted_at = None
            if date_tag:
                date_text = date_tag.get("datetime") or date_tag.get_text(strip=True)
                try:
                    posted_at = datetime.fromisoformat(date_text).isoformat()
                except Exception:
                    posted_at = None

            listings.append({
                "title": title,
                "listing_url": href,
                "price": price,
                "price_raw": price_raw,
                "location": location,
                "image_url": image_url,
                "image_count": 1 if image_url else 0,
                "posted_at": posted_at,
                "description": "",
                "seller_name": "GovPlanet",
            })

        except Exception as exc:
            logger.debug("Failed to parse GovPlanet card: %s", exc)

    return listings


def scrape_govplanet(
    query: str = "",
    category: str = "all",
    max_price: Optional[float] = None,
    max_results: int = 50,
) -> dict:
    """
    Scrape GovPlanet auction listings.
    
    Returns:
        {
            "listings": [...],
            "total_found": int,
            "source_url": str,
            "error": str or None
        }
    """
    url = build_search_url(query, category, max_price)
    logger.info("Scraping GovPlanet: %s", url)

    session = get_session()

    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()

        listings = parse_govplanet_results(resp.text, url)
        logger.info("Found %d listings from GovPlanet", len(listings))

        listings = listings[:max_results]

        return {
            "listings": listings,
            "total_found": len(listings),
            "source_url": url,
            "error": None,
        }

    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error: {e.response.status_code}"
        if e.response.status_code == 403:
            error_msg = "Access blocked by GovPlanet (403 Forbidden). The site may require browser-level access. Try importing listings manually via CSV/JSON instead."
        logger.error("GovPlanet scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"Request failed: {str(e)}"
        logger.error("GovPlanet scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
    except Exception as e:
        error_msg = f"Scrape error: {str(e)}"
        logger.error("GovPlanet scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
