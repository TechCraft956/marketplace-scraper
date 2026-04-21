"""
Craigslist scraper — Fetches listings from Craigslist search results.
Uses requests + BeautifulSoup. No JS rendering needed for search pages.
"""
import re
import logging
import time
import random
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# Craigslist category slugs
CL_CATEGORIES = {
    "all": "sss",              # all for sale
    "vehicles": "cta",         # cars+trucks
    "cars-trucks": "cta",      # alias
    "motorcycles": "mca",      # motorcycles
    "electronics": "ela",      # electronics
    "furniture": "fua",        # furniture
    "tools": "tla",            # tools
    "heavy_equipment": "hva",  # heavy equipment
    "trailers": "tra",         # trailers
    "boats": "bpa",            # boats
    "atvs": "sna",             # atvs/utvs/snowmobiles
    "farm": "gra",             # farm+garden
    "free": "zip",             # free stuff
    "general": "sss",          # general for-sale (alias)
    "for-sale": "sss",         # general for-sale (alias)
}

# Major Craigslist city subdomains
# Default location is read from CRAIGSLIST_LOCATION env var (see server.py endpoint)
CL_CITIES = {
    "sfbay": "sfbay",
    "austin": "austin",
    "houston": "houston",
    "dallas": "dallas",
    "san-antonio": "sanantonio",
    "fort-worth": "fortworth",
    "new-york": "newyork",
    "los-angeles": "losangeles",
    "chicago": "chicago",
    "phoenix": "phoenix",
    "seattle": "seattle",
    "denver": "denver",
    "atlanta": "atlanta",
    "miami": "miami",
    "portland": "portland",
    "minneapolis": "minneapolis",
    "detroit": "detroit",
    "tampa": "tampa",
    "sacramento": "sacramento",
    "nashville": "nashville",
    "raleigh": "raleigh",
    # RGV / South Texas
    "mcallen": "mcallen",
    "laredo": "laredo",
    "corpuschristi": "corpuschristi",
    # harlingen.craigslist.org does not currently resolve reliably, use brownsville region as resilient fallback
    "harlingen": "brownsville",
    "brownsville": "brownsville",
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
    city: str = "austin",
    query: str = "",
    category: str = "all",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    search_distance: Optional[int] = None,
) -> str:
    """Build a Craigslist search URL."""
    subdomain = CL_CITIES.get(city.lower(), city.lower())
    cat_slug = CL_CATEGORIES.get(category.lower(), "sss")

    base_url = f"https://{subdomain}.craigslist.org/search/{cat_slug}"

    params = {}
    if query:
        params["query"] = query
    if min_price is not None:
        params["min_price"] = int(min_price)
    if max_price is not None:
        params["max_price"] = int(max_price)
    if search_distance is not None:
        params["search_distance"] = search_distance

    if params:
        return f"{base_url}?{urlencode(params)}"
    return base_url


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.strip().lower()
    if text in ("free", "$0"):
        return 0.0
    match = re.search(r"\$?([\d,]+)", text)
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def parse_search_results(html: str, base_url: str) -> list[dict]:
    """Parse Craigslist search results HTML into listing dicts."""
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # Try JSON embedded data first (newer CL pages)
    script_tag = soup.find("script", id="ld_searchpage_results")
    if script_tag:
        try:
            import json
            data = json.loads(script_tag.string)
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "Product":
                        listing = {
                            "title": item.get("name", ""),
                            "listing_url": item.get("url", ""),
                            "price": None,
                            "location": "",
                            "description": item.get("description", ""),
                            "image_url": item.get("image", ""),
                        }
                        offers = item.get("offers", {})
                        if isinstance(offers, dict):
                            listing["price"] = float(offers.get("price", 0)) if offers.get("price") else None
                        listings.append(listing)
                if listings:
                    return listings
        except Exception as e:
            logger.debug("JSON parse failed, falling back to HTML: %s", e)

    # HTML parsing - try multiple selector strategies
    result_rows = soup.select("li.result-row, li.cl-search-result, .cl-search-result")

    if not result_rows:
        # Try gallery view items
        result_rows = soup.select(".result-node, .cl-static-search-result")

    for row in result_rows:
        try:
            # Title & URL
            title_tag = row.select_one("a.result-title, a.cl-app-anchor, a.titlestring, .title a, a[href*='/']")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            if href and not href.startswith("http"):
                href = urljoin(base_url, href)

            # Price
            price_tag = row.select_one("span.result-price, span.price, .priceinfo")
            price_text = price_tag.get_text(strip=True) if price_tag else ""
            price = parse_price(price_text)

            # Location
            loc_tag = row.select_one("span.result-hood, .surlocation, .meta .location")
            location = loc_tag.get_text(strip=True).strip(" ()") if loc_tag else ""

            # Image
            img_tag = row.select_one("img")
            image_url = ""
            if img_tag:
                image_url = img_tag.get("src") or img_tag.get("data-src") or ""

            # Date
            date_tag = row.select_one("time, .date")
            posted_at = None
            if date_tag:
                dt_str = date_tag.get("datetime") or date_tag.get("title") or date_tag.get_text(strip=True)
                try:
                    posted_at = datetime.fromisoformat(dt_str).isoformat()
                except Exception:
                    posted_at = None

            if title:
                listings.append({
                    "title": title,
                    "listing_url": href,
                    "price": price,
                    "price_raw": price_text,
                    "location": location,
                    "image_url": image_url,
                    "posted_at": posted_at,
                    "description": "",
                    "image_count": 1 if image_url else 0,
                })
        except Exception as exc:
            logger.debug("Failed to parse row: %s", exc)

    return listings


def scrape_listing_detail(session: requests.Session, url: str) -> dict:
    """Scrape individual listing page for full description and images."""
    try:
        time.sleep(random.uniform(1.0, 3.0))
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        description = ""
        desc_tag = soup.select_one("#postingbody, section#postingbody")
        if desc_tag:
            # Remove "QR Code Link to This Post" notice
            for notice in desc_tag.select(".print-information"):
                notice.decompose()
            description = desc_tag.get_text(strip=True)

        images = []
        for img in soup.select("#thumbs a, .gallery img, .swipe img"):
            src = img.get("href") or img.get("src") or ""
            if src and "craigslist" in src:
                images.append(src)

        # Try gallery images from script
        for script in soup.find_all("script"):
            if script.string and "imgList" in (script.string or ""):
                img_matches = re.findall(r'"url":"(https://[^"]+)"', script.string)
                images.extend(img_matches)

        # Location from map
        map_tag = soup.select_one("#map")
        lat = map_tag.get("data-latitude") if map_tag else None
        lng = map_tag.get("data-longitude") if map_tag else None

        # Location from breadcrumb
        loc_parts = []
        for bc in soup.select(".breadcrumb a, .breadbox span"):
            text = bc.get_text(strip=True)
            if text and len(text) > 2:
                loc_parts.append(text)

        return {
            "description": description,
            "images": list(set(images)),
            "image_count": len(set(images)),
            "latitude": float(lat) if lat else None,
            "longitude": float(lng) if lng else None,
        }
    except Exception as exc:
        logger.warning("Detail scrape failed for %s: %s", url, exc)
        return {}


def scrape_craigslist(
    city: str = "austin",
    query: str = "",
    category: str = "all",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    search_distance: Optional[int] = None,
    max_results: int = 50,
    fetch_details: bool = False,
) -> dict:
    """
    Scrape Craigslist search results.
    
    Returns:
        {
            "listings": [...],
            "total_found": int,
            "source_url": str,
            "error": str or None
        }
    """
    url = build_search_url(city, query, category, min_price, max_price, search_distance)
    logger.info("Scraping Craigslist: %s", url)

    session = get_session()
    all_listings = []

    try:
        resp = session.get(url, timeout=20)
        resp.raise_for_status()

        listings = parse_search_results(resp.text, url)
        logger.info("Found %d listings on search page", len(listings))

        # Limit results
        listings = listings[:max_results]

        # Optionally fetch detail pages for descriptions
        if fetch_details:
            for i, listing in enumerate(listings):
                if listing.get("listing_url"):
                    details = scrape_listing_detail(session, listing["listing_url"])
                    if details.get("description"):
                        listing["description"] = details["description"]
                    if details.get("images"):
                        listing["image_url"] = details["images"][0]
                        listing["image_count"] = details["image_count"]
                    # Rate limit
                    if (i + 1) % 5 == 0:
                        time.sleep(random.uniform(3.0, 6.0))

        all_listings = listings

        return {
            "listings": all_listings,
            "total_found": len(all_listings),
            "source_url": url,
            "error": None,
        }

    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP error: {e.response.status_code}"
        logger.error("Craigslist scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
    except requests.exceptions.RequestException as e:
        error_msg = f"Request failed: {str(e)}"
        logger.error("Craigslist scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
    except Exception as e:
        error_msg = f"Scrape error: {str(e)}"
        logger.error("Craigslist scrape failed: %s", error_msg)
        return {"listings": [], "total_found": 0, "source_url": url, "error": error_msg}
