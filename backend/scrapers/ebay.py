"""
eBay scraper — Buy It Now underpriced deals + ending-soon auctions.

Auth: set EBAY_APP_ID env var to use the Finding API (higher rate limits,
structured data). Falls back to HTML scraping if no key is set.

Categories covered: tools, motorcycles, heavy-equipment, electronics.
"""
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, quote_plus

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EBAY_APP_ID: str = os.environ.get("EBAY_APP_ID", "")

# eBay category IDs for Finding API / URL filtering
EBAY_CATEGORIES = {
    "all": "",
    "tools": "631",            # Hand Tools & Workshop Equipment
    "power-tools": "92074",
    "motorcycles": "6024",
    "heavy-equipment": "12139",
    "electronics": "293",
    "trucks": "6001",          # Cars & Trucks
    "trailers": "66471",       # Trailers
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

KEYWORD_FALLBACKS = {
    "tools": "tools",
    "power-tools": "power tools",
    "motorcycles": "motorcycles",
    "heavy-equipment": "heavy equipment",
    "electronics": "electronics",
    "trucks": "trucks",
    "trailers": "trailers",
}


def _resolve_search_keyword(query: str, category: str) -> str:
    keyword = (query or "").strip()
    if keyword:
        return keyword
    category_key = (category or "").strip().lower()
    return KEYWORD_FALLBACKS.get(category_key, category_key.replace("-", " ").strip())


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })
    # Warm up session with homepage to get cookies (avoids 503)
    try:
        s.get("https://www.ebay.com", timeout=10)
        time.sleep(random.uniform(1.0, 2.0))
    except Exception:
        pass
    return s


def _parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.replace(",", "").strip()
    # Handle ranges like "$10.00 to $50.00" — take lower bound
    match = re.search(r"\$?([\d]+(?:\.\d+)?)", text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def _scrape_html(
    query: str,
    category: str,
    listing_type: str,
    min_price: Optional[float],
    max_price: Optional[float],
    max_results: int,
) -> list[dict]:
    """HTML scrape of eBay search results page."""
    if not query:
        raise ValueError("eBay keyword is required")
    cat_id = EBAY_CATEGORIES.get(category.lower(), "")
    params: dict = {"_nkw": query, "_ipg": min(max_results, 60)}
    if cat_id:
        params["_sacat"] = cat_id
    if listing_type == "buy-it-now":
        params["LH_BIN"] = "1"
        params["_sop"] = "15"   # sort price low→high
    elif listing_type == "auction":
        params["LH_Auction"] = "1"
        params["_sop"] = "1"    # ending soonest
    if min_price is not None:
        params["_udlo"] = int(min_price)
    if max_price is not None:
        params["_udhi"] = int(max_price)

    url = f"https://www.ebay.com/sch/i.html?{urlencode(params)}"
    logger.info("eBay HTML scrape: %s", url)

    session = _session()
    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        logger.error("eBay HTTP error: %s", e)
        return []

    interruption_markers = ["Pardon Our Interruption", "splashui", "robot check", "verify yourself"]
    if any(marker.lower() in resp.text.lower() for marker in interruption_markers):
        logger.warning("eBay anti-bot interruption detected for %s", url)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    listings = []

    for item in soup.select(".s-item"):
        try:
            title_tag = item.select_one(".s-item__title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            # Skip eBay's "Shop on eBay" placeholder
            if "Shop on eBay" in title or not title:
                continue

            link_tag = item.select_one("a.s-item__link")
            link = (link_tag.get("href") or "").split("?")[0] if link_tag else ""

            price_tag = item.select_one(".s-item__price")
            price_text = price_tag.get_text(strip=True) if price_tag else ""
            price = _parse_price(price_text)

            img_tag = item.select_one(".s-item__image-img")
            image_url = img_tag.get("src") or img_tag.get("data-src", "") if img_tag else ""

            # Auction end time
            end_tag = item.select_one(".s-item__time-end, .s-item__time-left")
            end_time = end_tag.get_text(strip=True) if end_tag else None

            # Location
            loc_tag = item.select_one(".s-item__location, .s-item__itemLocation")
            location = loc_tag.get_text(strip=True).replace("From ", "") if loc_tag else ""

            listings.append({
                "title": title,
                "price": price,
                "price_raw": price_text,
                "listing_url": link,
                "image_url": image_url,
                "location": location,
                "description": f"eBay listing. {f'Ends: {end_time}' if end_time else ''}".strip(),
                "image_count": 1 if image_url else 0,
                "posted_at": None,
                "source": "ebay",
            })

            if len(listings) >= max_results:
                break

        except Exception as exc:
            logger.debug("eBay row parse error: %s", exc)

    return listings


def _scrape_finding_api(
    query: str,
    category: str,
    listing_type: str,
    min_price: Optional[float],
    max_price: Optional[float],
    max_results: int,
) -> list[dict]:
    """eBay Finding API (requires EBAY_APP_ID)."""
    if not query:
        raise ValueError("eBay keyword is required")
    cat_id = EBAY_CATEGORIES.get(category.lower(), "")
    op = "findItemsByKeywords" if not cat_id else "findItemsAdvanced"

    params: dict = {
        "OPERATION-NAME": op,
        "SERVICE-VERSION": "1.0.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "keywords": query or category,
        "paginationInput.entriesPerPage": min(max_results, 100),
        "sortOrder": "PricePlusShippingLowest",
    }
    if cat_id:
        params["categoryId"] = cat_id
    if listing_type == "buy-it-now":
        params["itemFilter(0).name"] = "ListingType"
        params["itemFilter(0).value"] = "FixedPrice"
    if min_price is not None:
        params["itemFilter(1).name"] = "MinPrice"
        params["itemFilter(1).value"] = str(min_price)
    if max_price is not None:
        params["itemFilter(2).name"] = "MaxPrice"
        params["itemFilter(2).value"] = str(max_price)

    url = f"https://svcs.ebay.com/services/search/FindingService/v1?{urlencode(params)}"
    logger.info("eBay Finding API: %s", url)

    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("eBay Finding API error: %s", e)
        return []

    listings = []
    try:
        items = (
            data
            .get(f"{op}Response", [{}])[0]
            .get("searchResult", [{}])[0]
            .get("item", [])
        )
        for item in items:
            title = item.get("title", [""])[0]
            price = float(item.get("sellingStatus", [{}])[0]
                          .get("currentPrice", [{}])[0]
                          .get("__value__", 0) or 0)
            link = item.get("viewItemURL", [""])[0]
            image = item.get("galleryURL", [""])[0]
            location = item.get("location", [""])[0]
            end_time = item.get("listingInfo", [{}])[0].get("endTime", [""])[0]

            listings.append({
                "title": title,
                "price": price,
                "price_raw": f"${price:.2f}",
                "listing_url": link,
                "image_url": image,
                "location": location,
                "description": f"eBay listing. Ends: {end_time}" if end_time else "eBay listing.",
                "image_count": 1 if image else 0,
                "posted_at": None,
                "source": "ebay",
            })
    except Exception as exc:
        logger.error("eBay API response parse error: %s", exc)

    return listings


def scrape_ebay(
    query: str = "",
    category: str = "all",
    listing_type: str = "buy-it-now",  # "buy-it-now", "auction", "all"
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    max_results: int = 50,
) -> dict:
    """
    Scrape eBay for underpriced deals.

    Uses Finding API if EBAY_APP_ID is set, otherwise HTML scraping.

    Returns:
        {"listings": [...], "total_found": int, "source_url": str, "error": str|None}
    """
    search_keyword = _resolve_search_keyword(query, category)
    if not search_keyword:
        return {
            "listings": [],
            "total_found": 0,
            "source_url": "https://www.ebay.com",
            "error": "eBay keyword is required",
        }

    source_url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(search_keyword)}"

    try:
        if EBAY_APP_ID:
            logger.info("eBay: using Finding API (app_id configured)")
            listings = _scrape_finding_api(search_keyword, category, listing_type, min_price, max_price, max_results)
        else:
            logger.info("eBay: using HTML scraping (no EBAY_APP_ID set)")
            listings = _scrape_html(search_keyword, category, listing_type, min_price, max_price, max_results)
            time.sleep(random.uniform(1.0, 2.0))

        return {
            "listings": listings,
            "total_found": len(listings),
            "source_url": source_url,
            "error": None,
        }

    except Exception as e:
        logger.error("eBay scrape failed: %s", e)
        return {"listings": [], "total_found": 0, "source_url": source_url, "error": str(e)}
