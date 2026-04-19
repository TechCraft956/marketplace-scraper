"""
Craigslist RSS scraper — faster and cleaner than HTML scraping.

Craigslist publishes RSS 2.0 feeds per city/category:
  https://{city}.craigslist.org/search/{cat}?format=rss

Uses stdlib only (urllib + xml.etree.ElementTree). No extra deps.
source="craigslist_rss"
"""
import logging
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

CRAIGSLIST_LOCATION: str = os.environ.get("CRAIGSLIST_LOCATION", "sfbay")

# Same slugs as craigslist.py
CL_CATEGORIES_RSS = {
    "all": "sss",
    "vehicles": "cta",
    "cars-trucks": "cta",
    "motorcycles": "mca",
    "electronics": "ela",
    "furniture": "fua",
    "tools": "tla",
    "heavy_equipment": "hva",
    "trailers": "tra",
    "boats": "bpa",
    "atvs": "sna",
    "farm": "gra",
    "free": "zip",
    "general": "sss",
    "for-sale": "sss",
}

# Namespace used in CL RSS
_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "enc": "http://purl.oclc.org/net/rss_2.0/enc#",
}


def _parse_price(title: str) -> Optional[float]:
    """Extract leading price from CL RSS title e.g. '$1,200 Honda CB500'."""
    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", title.replace(",", ""))
    if match:
        try:
            return float(match.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _fetch_rss(url: str) -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            # Handle gzip
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        logger.error("CL RSS HTTP %s: %s — site may be blocking scraper IPs", e.code, url)
    except Exception as e:
        logger.error("CL RSS fetch error: %s", e)
    return None


def _parse_feed(xml_text: str, source_url: str) -> list[dict]:
    listings = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("CL RSS XML parse error: %s", e)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    for item in channel.findall("item"):
        try:
            title_raw = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = (item.findtext("description") or "").strip()
            pub_date = item.findtext("pubDate") or item.findtext("dc:date", namespaces=_NS)

            if not title_raw or not link:
                continue

            # Price from title prefix
            price = _parse_price(title_raw)
            # Remove price prefix from display title
            title = re.sub(r"^\$[\d,]+\s*", "", title_raw).strip()

            # Image from enclosure or description
            image_url = ""
            enc = item.find("enc:enclosure", _NS)
            if enc is not None:
                image_url = enc.get("resource", "")
            if not image_url:
                img_match = re.search(r'<img[^>]+src="([^"]+)"', description)
                if img_match:
                    image_url = img_match.group(1)

            # Location from description
            location = ""
            loc_match = re.search(r'\(([^)]+)\)\s*$', title_raw)
            if loc_match:
                location = loc_match.group(1)

            # Parse date
            posted_at = None
            if pub_date:
                for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
                    try:
                        posted_at = datetime.strptime(pub_date.strip(), fmt).isoformat()
                        break
                    except ValueError:
                        continue

            # Strip HTML from description
            clean_desc = re.sub(r"<[^>]+>", " ", description).strip()

            listings.append({
                "title": title or title_raw,
                "price": price,
                "price_raw": f"${price:,.0f}" if price else "",
                "listing_url": link,
                "location": location,
                "description": clean_desc[:500],
                "image_url": image_url,
                "image_count": 1 if image_url else 0,
                "posted_at": posted_at,
                "source": "craigslist_rss",
            })
        except Exception as exc:
            logger.debug("CL RSS item parse error: %s", exc)

    return listings


def scrape_craigslist_rss(
    city: Optional[str] = None,
    category: str = "all",
    query: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    max_results: int = 120,
) -> dict:
    """
    Fetch Craigslist RSS feed for a city/category.

    Faster than HTML scraping, returns up to ~120 items per feed page.
    Does NOT support pagination (CL RSS is one page only).

    Returns:
        {"listings": [...], "total_found": int, "source_url": str, "error": str|None}
    """
    resolved_city = city or CRAIGSLIST_LOCATION
    # CL subdomains for known cities (same mapping as craigslist.py)
    city_map = {
        "sfbay": "sfbay", "austin": "austin", "houston": "houston",
        "dallas": "dallas", "los-angeles": "losangeles", "new-york": "newyork",
        "chicago": "chicago", "seattle": "seattle", "denver": "denver",
        "atlanta": "atlanta", "miami": "miami", "portland": "portland",
        "phoenix": "phoenix", "sacramento": "sacramento", "nashville": "nashville",
    }
    subdomain = city_map.get(resolved_city.lower(), resolved_city.lower())
    cat_slug = CL_CATEGORIES_RSS.get(category.lower(), "sss")

    params: dict = {"format": "rss"}
    if query:
        params["query"] = query
    if min_price is not None:
        params["min_price"] = int(min_price)
    if max_price is not None:
        params["max_price"] = int(max_price)

    qs = urlencode(params)
    source_url = f"https://{subdomain}.craigslist.org/search/{cat_slug}?{qs}"

    xml_text = _fetch_rss(source_url)
    if not xml_text:
        return {"listings": [], "total_found": 0, "source_url": source_url, "error": "Feed fetch failed"}

    listings = _parse_feed(xml_text, source_url)[:max_results]
    time.sleep(1.0)  # polite rate limit

    return {
        "listings": listings,
        "total_found": len(listings),
        "source_url": source_url,
        "error": None,
    }
