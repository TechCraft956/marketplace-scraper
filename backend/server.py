"""
DealScope — Marketplace Deal Intelligence API
FastAPI backend with MongoDB storage, scoring engine, and multi-source ingestion.
"""
import asyncio
import os
import csv
import json
import io
import re
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import notifier
import action_engine

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, DESCENDING
from bson import ObjectId

# Import scoring engine directly (avoid triggering full module init)
from modules.marketplace_scraper.scorer import ResaleScorer, CATEGORY_PRICE_REFERENCE, URGENCY_KEYWORDS
from vehicle_deals import VehicleDealEvaluationRequest, evaluate_vehicle_deals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

app = FastAPI(title="DealScope API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = MongoClient(MONGO_URL)
db = client[DB_NAME]
listings_col = db["listings"]
import_runs_col = db["import_runs"]
scored_opportunities_col = db["scored_opportunities"]

# Ensure indexes
listings_col.create_index([("score", DESCENDING)])
listings_col.create_index("listing_hash", unique=True, sparse=True)
listings_col.create_index("category")
listings_col.create_index("is_sold")
scored_opportunities_col.create_index([("score", DESCENDING)])
scored_opportunities_col.create_index("listing_id", unique=True, sparse=True)

SCORED_JSON_PATH = Path(os.environ.get("STORAGE_PATH", "/app/storage")) / "scored.json"

TOP_ACTIONS_CACHE: dict = {"top_actions": [], "suppressed_count": 0, "cached_at": None}
SCRAPE_LOG_PATH  = Path(os.environ.get("STORAGE_PATH", "/app/storage")) / "scrape_log.json"
FB_COOKIES_PATH  = Path(os.environ.get("FB_COOKIES_PATH", "/app/cookies/fb_cookies.json"))

# Per-source scheduler state (mutated by background tasks at runtime)
SCHEDULER_STATUS: dict = {
    "craigslist":    {"interval_minutes": 30, "last_run": None, "next_run": None, "last_imported": 0, "last_error": None, "running": False},
    "govplanet":     {"interval_minutes": 60, "last_run": None, "next_run": None, "last_imported": 0, "last_error": None, "running": False},
    "publicsurplus": {"interval_minutes": 60, "last_run": None, "next_run": None, "last_imported": 0, "last_error": None, "running": False},
    "facebook":      {"interval_minutes": 45, "last_run": None, "next_run": None, "last_imported": 0, "last_error": None, "running": False},
    "ebay":          {"interval_minutes": 60, "last_run": None, "next_run": None, "last_imported": 0, "last_error": None, "running": False},
}


def _inspect_facebook_cookies() -> dict:
    state = {
        "path": str(FB_COOKIES_PATH),
        "present": FB_COOKIES_PATH.exists(),
        "valid": False,
        "expired": False,
        "cookie_count": 0,
        "missing_required": [],
        "message": None,
    }
    if not state["present"]:
        state["message"] = "missing_cookies"
        return state

    try:
        cookie_data = json.loads(FB_COOKIES_PATH.read_text())
        if not isinstance(cookie_data, list) or not cookie_data:
            raise ValueError("empty list")
    except Exception as exc:
        state["message"] = f"invalid_cookies: {exc}"
        return state

    state["cookie_count"] = len(cookie_data)
    now_ts = datetime.now(timezone.utc).timestamp()
    names = {str(item.get("name", "")) for item in cookie_data if isinstance(item, dict)}
    required = [name for name in ("c_user", "xs") if name not in names]
    expiries = [
        float(item["expires"])
        for item in cookie_data
        if isinstance(item, dict) and item.get("expires") not in (None, -1, "")
    ]

    state["missing_required"] = required
    if required:
        state["message"] = f"missing_required_cookies: {', '.join(required)}"
        return state

    if expiries and max(expiries) < now_ts:
        state["expired"] = True
        state["message"] = "expired_cookies"
        return state

    state["valid"] = True
    state["message"] = "ready"
    return state


# ---------------------------------------------------------------------------
# Extended price references for priority categories
# ---------------------------------------------------------------------------
VEHICLE_PRICES = {
    "motorcycle": (1500, 5000, 15000),
    "honda motorcycle": (1500, 4000, 10000),
    "harley": (3000, 8000, 25000),
    "harley davidson": (3000, 8000, 25000),
    "kawasaki": (1500, 4500, 12000),
    "yamaha motorcycle": (1500, 4000, 10000),
    "car": (2000, 8000, 25000),
    "truck": (3000, 12000, 35000),
    "ford f150": (5000, 18000, 40000),
    "ford f-150": (5000, 18000, 40000),
    "chevy silverado": (5000, 18000, 40000),
    "toyota tacoma": (8000, 22000, 38000),
    "toyota camry": (3000, 10000, 22000),
    "honda civic": (3000, 9000, 22000),
    "honda accord": (3000, 10000, 24000),
    "jeep wrangler": (8000, 20000, 40000),
    "suv": (3000, 12000, 30000),
    "sedan": (2000, 8000, 20000),
    "minivan": (2000, 6000, 18000),
    "trailer": (1000, 4000, 15000),
    "utility trailer": (500, 2500, 8000),
    "boat": (2000, 8000, 30000),
    "atv": (1000, 4000, 12000),
    "side by side": (3000, 8000, 20000),
    "rzr": (5000, 12000, 25000),
}

EQUIPMENT_PRICES = {
    "excavator": (5000, 25000, 80000),
    "backhoe": (5000, 20000, 60000),
    "skid steer": (5000, 18000, 50000),
    "bobcat": (5000, 18000, 50000),
    "forklift": (3000, 10000, 30000),
    "tractor": (3000, 15000, 50000),
    "john deere": (3000, 15000, 50000),
    "kubota": (3000, 12000, 40000),
    "welder": (200, 800, 3000),
    "compressor": (200, 800, 3000),
    "construction equipment": (5000, 20000, 60000),
    "dump trailer": (2000, 6000, 20000),
    "cargo trailer": (1000, 4000, 12000),
    "enclosed trailer": (1500, 5000, 15000),
    "flatbed trailer": (1000, 3500, 12000),
    "chainsaw": (50, 250, 800),
    "power tools": (50, 200, 600),
    "tool chest": (100, 400, 1500),
    "snap on": (200, 800, 5000),
}

# Merge into scorer's reference
CATEGORY_PRICE_REFERENCE.update(VEHICLE_PRICES)
CATEGORY_PRICE_REFERENCE.update(EQUIPMENT_PRICES)


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "vehicles": [
        "car", "truck", "motorcycle", "suv", "sedan", "van", "minivan",
        "jeep", "ford", "chevy", "toyota", "honda", "harley", "kawasaki",
        "yamaha", "trailer", "boat", "atv", "rzr", "side by side",
        "f-150", "f150", "silverado", "tacoma", "camry", "civic", "accord",
        "wrangler", "ram", "dodge", "nissan", "subaru", "bmw", "mercedes",
        "audi", "lexus", "tesla", "mustang", "corvette", "challenger",
    ],
    "equipment": [
        "excavator", "backhoe", "skid steer", "bobcat", "forklift",
        "tractor", "john deere", "kubota", "welder", "compressor",
        "construction", "dump trailer", "cargo trailer", "chainsaw",
        "power tools", "tool chest", "snap on", "generator", "pressure washer",
        "riding mower", "air compressor", "table saw", "circular saw",
        "dewalt", "milwaukee", "makita", "scaffolding", "crane",
    ],
    "electronics": [
        "iphone", "ipad", "macbook", "laptop", "ps5", "ps4", "xbox",
        "nintendo", "switch", "gaming", "gpu", "graphics card", "monitor",
        "tv", "camera", "drone", "airpods", "headphones", "keyboard",
        "samsung", "phone", "tablet", "apple watch", "computer", "pc",
    ],
    "furniture": [
        "couch", "sofa", "bed", "mattress", "desk", "dresser", "bookshelf",
        "dining", "table", "chair", "recliner", "sectional", "cabinet",
        "nightstand", "wardrobe", "shelving", "ottoman", "futon",
    ],
}


def detect_category(title: str, description: str = "") -> str:
    title_text = f" {title} ".lower()
    desc_text = f" {description} ".lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            pattern = r'(?:^|\s|/)' + re.escape(kw) + r'(?:\s|$|[.,;!?/\-])'
            if re.search(pattern, title_text):
                score += 3  # Title matches weigh 3x more
            elif re.search(pattern, desc_text):
                score += 1
        if score > 0:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "other"


def extract_keywords(title: str, description: str = "") -> list:
    text = f"{title} {description}".lower()
    found = []
    for kw in URGENCY_KEYWORDS:
        if kw in text:
            found.append(kw)
    return found


def generate_hash(title: str, price, source: str) -> str:
    raw = f"{title.lower().strip()}|{price}|{source}"
    return hashlib.md5(raw.encode()).hexdigest()


def score_listing(listing: dict) -> dict:
    scorer = ResaleScorer(max_acceptable_distance=100.0)
    result = scorer.score(listing)
    return result.to_dict()


def serialize_listing(doc: dict) -> dict:
    doc["id"] = str(doc.pop("_id"))
    return doc


def _upsert_opportunity(listing: dict, listing_id: str) -> None:
    doc = {k: v for k, v in listing.items() if k != "_id"}
    doc["listing_id"] = listing_id
    scored_opportunities_col.replace_one({"listing_id": listing_id}, doc, upsert=True)


def _write_scored_json(results: list) -> None:
    try:
        SCORED_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SCORED_JSON_PATH, "w") as f:
            json.dump(results, f, indent=2, default=str)
    except Exception as e:
        logger.warning("Could not write scored.json: %s", e)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "dealscope", "version": "1.0.0"}


@app.post("/api/vehicle-deals/evaluate")
def evaluate_vehicle_deals_endpoint(payload: VehicleDealEvaluationRequest):
    return evaluate_vehicle_deals(payload)


@app.get("/api/listings")
def get_listings(
    min_score: float = Query(0, ge=0, le=100),
    max_price: Optional[float] = Query(None, ge=0),
    min_price: Optional[float] = Query(None, ge=0),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort_by: str = Query("score", regex="^(score|price|posted_at|created_at)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    exclude_sold: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    query = {}
    if exclude_sold:
        query["is_sold"] = {"$ne": True}
    if min_score > 0:
        query["score"] = {"$gte": min_score}
    if category and category != "all":
        query["category"] = category
    if max_price is not None:
        query.setdefault("price", {})["$lte"] = max_price
    if min_price is not None:
        query.setdefault("price", {})["$gte"] = min_price
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
        ]

    sort_dir = DESCENDING if sort_order == "desc" else 1
    cursor = listings_col.find(query).sort(sort_by, sort_dir).skip(offset).limit(limit)
    results = [serialize_listing(doc) for doc in cursor]
    total = listings_col.count_documents(query)

    return {"listings": results, "total": total, "offset": offset, "limit": limit}


@app.get("/api/listings/{listing_id}")
def get_listing(listing_id: str):
    try:
        doc = listings_col.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    return serialize_listing(doc)


@app.post("/api/listings/{listing_id}/mark-sold")
def mark_sold(listing_id: str):
    try:
        result = listings_col.update_one(
            {"_id": ObjectId(listing_id)},
            {"$set": {"is_sold": True, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "action": "marked_sold"}


@app.post("/api/listings/{listing_id}/mark-contacted")
def mark_contacted(listing_id: str):
    try:
        result = listings_col.update_one(
            {"_id": ObjectId(listing_id)},
            {"$set": {"is_contacted": True, "updated_at": datetime.now(timezone.utc).isoformat()}}
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "action": "marked_contacted"}


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: str):
    try:
        result = listings_col.delete_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"success": True, "action": "deleted"}


@app.get("/api/stats")
def get_stats():
    total = listings_col.count_documents({})
    active = listings_col.count_documents({"is_sold": {"$ne": True}})
    hot_deals = listings_col.count_documents({"score": {"$gte": 70}, "is_sold": {"$ne": True}})

    pipeline = [
        {"$match": {"is_sold": {"$ne": True}}},
        {"$group": {"_id": None, "avg_score": {"$avg": "$score"}, "avg_price": {"$avg": "$price"}}},
    ]
    agg = list(listings_col.aggregate(pipeline))
    avg_score = round(agg[0]["avg_score"], 1) if agg and agg[0].get("avg_score") else None
    avg_price = round(agg[0]["avg_price"], 0) if agg and agg[0].get("avg_price") else None

    cat_pipeline = [
        {"$match": {"is_sold": {"$ne": True}}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
    ]
    cat_counts = {doc["_id"]: doc["count"] for doc in listings_col.aggregate(cat_pipeline)}

    score_dist = {
        "hot": listings_col.count_documents({"score": {"$gte": 70}, "is_sold": {"$ne": True}}),
        "good": listings_col.count_documents({"score": {"$gte": 50, "$lt": 70}, "is_sold": {"$ne": True}}),
        "fair": listings_col.count_documents({"score": {"$gte": 30, "$lt": 50}, "is_sold": {"$ne": True}}),
        "low": listings_col.count_documents({"score": {"$lt": 30}, "is_sold": {"$ne": True}}),
    }

    last_import = import_runs_col.find_one(sort=[("created_at", DESCENDING)])

    return {
        "total_listings": total,
        "active_listings": active,
        "hot_deals": hot_deals,
        "avg_score": avg_score,
        "avg_price": avg_price,
        "category_counts": cat_counts,
        "score_distribution": score_dist,
        "last_import": {
            "source": last_import.get("source"),
            "count": last_import.get("count"),
            "created_at": last_import.get("created_at"),
        } if last_import else None,
    }


@app.get("/api/categories")
def get_categories():
    pipeline = [
        {"$match": {"is_sold": {"$ne": True}}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}, "avg_score": {"$avg": "$score"}}},
        {"$sort": {"count": -1}},
    ]
    results = list(listings_col.aggregate(pipeline))
    return [
        {"name": r["_id"] or "other", "count": r["count"], "avg_score": round(r["avg_score"] or 0, 1)}
        for r in results
    ]


# ---------------------------------------------------------------------------
# Ingestion endpoints
# ---------------------------------------------------------------------------

def process_and_store_listing(raw: dict, source: str) -> Optional[str]:
    title = (raw.get("title") or "").strip()
    if not title:
        return None

    price = raw.get("price")
    if isinstance(price, str):
        price = parse_price_str(price)

    category = raw.get("category") or detect_category(title, raw.get("description", ""))
    listing_hash = generate_hash(title, price, source)

    existing = listings_col.find_one({"listing_hash": listing_hash})
    if existing:
        return None

    listing_data = {
        "title": title,
        "price": price,
        "price_raw": raw.get("price_raw") or str(raw.get("price", "")),
        "location": raw.get("location", ""),
        "distance": raw.get("distance"),
        "category": category,
        "description": raw.get("description", ""),
        "listing_url": raw.get("listing_url") or raw.get("url") or raw.get("link") or "",
        "image_url": raw.get("image_url") or raw.get("image") or "",
        "image_count": raw.get("image_count", 1 if raw.get("image_url") else 0),
        "posted_at": raw.get("posted_at") or raw.get("date"),
        "seller_name": raw.get("seller_name") or raw.get("seller"),
        "source": source,
        "listing_hash": listing_hash,
        "is_sold": False,
        "is_contacted": False,
        "keywords": extract_keywords(title, raw.get("description", "")),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    breakdown = score_listing(listing_data)
    listing_data["score"] = breakdown["score"]
    listing_data["confidence"] = breakdown.get("confidence")
    listing_data["estimated_profit_low"] = breakdown.get("estimated_profit_low")
    listing_data["travel_tier"] = breakdown.get("travel_tier", "unknown")
    listing_data["distance_miles"] = breakdown.get("distance_miles")
    listing_data["effective_profit_after_travel"] = breakdown.get("effective_profit_after_travel")
    listing_data["score_breakdown"] = breakdown

    result = listings_col.insert_one(listing_data)
    new_id = str(result.inserted_id)

    if notifier.is_opportunity(listing_data):
        _upsert_opportunity(listing_data, new_id)
        notifier.maybe_alert(listing_data, new_id)

    return new_id


def parse_price_str(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.strip().lower()
    if s in ("free", "$0", "0"):
        return 0.0
    match = re.search(r"[\d,]+(?:\.\d+)?", s.replace(",", ""))
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None


@app.post("/api/import/json")
async def import_json(file: UploadFile = File(...)):
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="File must be .json")

    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="JSON must be an array of listings")

    imported = 0
    skipped = 0
    for item in data:
        result = process_and_store_listing(item, "json_import")
        if result:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "json_import",
        "filename": file.filename,
        "count": imported,
        "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {"imported": imported, "skipped": skipped, "total_in_file": len(data)}


@app.post("/api/import/csv")
async def import_csv(file: UploadFile = File(...)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be .csv")

    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    imported = 0
    skipped = 0
    total = 0

    for row in reader:
        total += 1
        result = process_and_store_listing(dict(row), "csv_import")
        if result:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "csv_import",
        "filename": file.filename,
        "count": imported,
        "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {"imported": imported, "skipped": skipped, "total_in_file": total}


@app.post("/api/import/manual")
async def import_manual(listing: dict):
    result = process_and_store_listing(listing, "manual_entry")
    if not result:
        raise HTTPException(status_code=400, detail="Could not import listing (missing title or duplicate)")
    return {"success": True, "listing_id": result}


# ---------------------------------------------------------------------------
# Screenshot OCR endpoint
# ---------------------------------------------------------------------------

@app.post("/api/import/screenshot")
async def import_screenshot(file: UploadFile = File(...)):
    allowed_types = ["image/jpeg", "image/png", "image/webp", "image/jpg"]
    if file.content_type and file.content_type not in allowed_types:
        ext = file.filename.split(".")[-1].lower() if file.filename else ""
        if ext not in ("jpg", "jpeg", "png", "webp"):
            raise HTTPException(status_code=400, detail="File must be JPEG, PNG, or WebP image")

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large (max 20MB)")

    mime = file.content_type or "image/jpeg"

    from scrapers.ocr import extract_from_screenshot
    extracted = await extract_from_screenshot(content, mime)

    if extracted.get("error"):
        raise HTTPException(status_code=422, detail=extracted["error"])

    if not extracted.get("title"):
        raise HTTPException(status_code=422, detail="Could not extract listing data from screenshot. Try a clearer image.")

    source = extracted.pop("source", "screenshot")
    result = process_and_store_listing(extracted, source)

    if not result:
        raise HTTPException(status_code=400, detail="Could not store listing (missing title or duplicate)")

    import_runs_col.insert_one({
        "source": source,
        "filename": file.filename,
        "count": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    stored = listings_col.find_one({"_id": ObjectId(result)}, {"_id": 0})
    return {
        "success": True,
        "listing_id": result,
        "extracted": extracted,
        "score": stored.get("score") if stored else None,
    }


# ---------------------------------------------------------------------------
# Craigslist scraper endpoint
# ---------------------------------------------------------------------------

from pydantic import BaseModel

CRAIGSLIST_LOCATION = os.environ.get("CRAIGSLIST_LOCATION", "sfbay")


class CraigslistScrapeRequest(BaseModel):
    city: Optional[str] = None   # falls back to CRAIGSLIST_LOCATION env var
    query: str = ""
    category: str = "all"
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    search_distance: Optional[int] = None
    max_results: int = 50
    fetch_details: bool = False


@app.post("/api/scrape/craigslist")
def scrape_craigslist_endpoint(req: CraigslistScrapeRequest):
    from scrapers.craigslist import scrape_craigslist as do_scrape

    city = req.city or CRAIGSLIST_LOCATION

    result = do_scrape(
        city=city,
        query=req.query,
        category=req.category,
        min_price=req.min_price,
        max_price=req.max_price,
        search_distance=req.search_distance,
        max_results=req.max_results,
        fetch_details=req.fetch_details,
    )

    if result["error"]:
        return {
            "success": False,
            "error": result["error"],
            "source_url": result["source_url"],
            "imported": 0,
            "total_found": 0,
        }

    imported = 0
    skipped = 0
    for listing in result["listings"]:
        res = process_and_store_listing(listing, "craigslist")
        if res:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "craigslist",
        "city": req.city,
        "query": req.query,
        "count": imported,
        "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "total_found": result["total_found"],
        "source_url": result["source_url"],
    }


# ---------------------------------------------------------------------------
# GovPlanet scraper endpoint
# ---------------------------------------------------------------------------

class GovPlanetScrapeRequest(BaseModel):
    query: str = ""
    category: str = "all"
    max_price: Optional[float] = None
    max_results: int = 50


@app.post("/api/scrape/govplanet")
def scrape_govplanet_endpoint(req: GovPlanetScrapeRequest):
    from scrapers.govplanet import scrape_govplanet as do_scrape

    result = do_scrape(
        query=req.query,
        category=req.category,
        max_price=req.max_price,
        max_results=req.max_results,
    )

    if result["error"]:
        return {
            "success": False,
            "error": result["error"],
            "source_url": result["source_url"],
            "imported": 0,
            "total_found": 0,
        }

    imported = 0
    skipped = 0
    for listing in result["listings"]:
        res = process_and_store_listing(listing, "govplanet")
        if res:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "govplanet",
        "query": req.query,
        "count": imported,
        "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "total_found": result["total_found"],
        "source_url": result["source_url"],
    }


# ---------------------------------------------------------------------------
# Available scraper sources info
# ---------------------------------------------------------------------------

@app.get("/api/scrapers")
def get_scrapers():
    from scrapers.craigslist import CL_CITIES, CL_CATEGORIES
    from scrapers.govplanet import GP_CATEGORIES
    from scrapers.ebay import EBAY_CATEGORIES, EBAY_APP_ID
    from scrapers.craigslist_rss import CL_CATEGORIES_RSS
    from scrapers.publicsurplus import PS_CATEGORIES
    return {
        "craigslist": {
            "name": "Craigslist HTML",
            "status": "available",
            "endpoint": "POST /api/scrape/craigslist",
            "cities": list(CL_CITIES.keys()),
            "categories": list(CL_CATEGORIES.keys()),
        },
        "craigslist_rss": {
            "name": "Craigslist RSS",
            "status": "available",
            "endpoint": "POST /api/scrape/craigslist-rss",
            "note": "Faster than HTML, stdlib only, no extra deps",
            "categories": list(CL_CATEGORIES_RSS.keys()),
        },
        "govplanet": {
            "name": "GovPlanet",
            "status": "available",
            "endpoint": "POST /api/scrape/govplanet",
            "categories": list(GP_CATEGORIES.keys()),
        },
        "ebay": {
            "name": "eBay",
            "status": "available",
            "endpoint": "POST /api/scrape/ebay",
            "mode": "Finding API" if EBAY_APP_ID else "HTML scraping (set EBAY_APP_ID for API mode)",
            "categories": list(EBAY_CATEGORIES.keys()),
        },
        "publicsurplus": {
            "name": "PublicSurplus",
            "status": "available",
            "endpoint": "POST /api/scrape/publicsurplus",
            "note": "Government/municipal surplus auctions",
            "categories": list(PS_CATEGORIES.keys()),
        },
        "screenshot_ocr": {
            "name": "Screenshot OCR",
            "status": "available",
            "endpoint": "POST /api/import/screenshot",
            "supported_formats": ["JPEG", "PNG", "WebP"],
            "vision_model": "GPT-4o (primary) + Tesseract (fallback)",
        },
    }


# ---------------------------------------------------------------------------
# eBay scraper endpoint
# ---------------------------------------------------------------------------

class EbayScrapeRequest(BaseModel):
    query: str = ""
    category: str = "all"
    listing_type: str = "buy-it-now"
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    max_results: int = 50


@app.post("/api/scrape/ebay")
def scrape_ebay_endpoint(req: EbayScrapeRequest):
    from scrapers.ebay import scrape_ebay as do_scrape

    result = do_scrape(
        query=req.query,
        category=req.category,
        listing_type=req.listing_type,
        min_price=req.min_price,
        max_price=req.max_price,
        max_results=req.max_results,
    )

    if result["error"]:
        return {"success": False, "error": result["error"], "source_url": result["source_url"], "imported": 0, "total_found": 0}

    imported = skipped = 0
    for listing in result["listings"]:
        res = process_and_store_listing(listing, "ebay")
        if res:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "ebay", "query": req.query, "category": req.category,
        "count": imported, "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "imported": imported, "skipped": skipped,
            "total_found": result["total_found"], "source_url": result["source_url"]}


# ---------------------------------------------------------------------------
# Craigslist RSS endpoint
# ---------------------------------------------------------------------------

class CraigslistRssScrapeRequest(BaseModel):
    city: Optional[str] = None
    category: str = "all"
    query: str = ""
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    max_results: int = 120


@app.post("/api/scrape/craigslist-rss")
def scrape_craigslist_rss_endpoint(req: CraigslistRssScrapeRequest):
    from scrapers.craigslist_rss import scrape_craigslist_rss as do_scrape

    result = do_scrape(
        city=req.city,
        category=req.category,
        query=req.query,
        min_price=req.min_price,
        max_price=req.max_price,
        max_results=req.max_results,
    )

    if result["error"]:
        return {"success": False, "error": result["error"], "source_url": result["source_url"], "imported": 0, "total_found": 0}

    imported = skipped = 0
    for listing in result["listings"]:
        res = process_and_store_listing(listing, "craigslist_rss")
        if res:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "craigslist_rss", "city": req.city or CRAIGSLIST_LOCATION,
        "category": req.category, "count": imported, "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "imported": imported, "skipped": skipped,
            "total_found": result["total_found"], "source_url": result["source_url"]}


# ---------------------------------------------------------------------------
# PublicSurplus endpoint
# ---------------------------------------------------------------------------

class PublicSurplusScrapeRequest(BaseModel):
    query: str = ""
    category: str = "all"
    max_price: Optional[float] = None
    max_results: int = 50
    state: str = ""


@app.post("/api/scrape/publicsurplus")
def scrape_publicsurplus_endpoint(req: PublicSurplusScrapeRequest):
    from scrapers.publicsurplus import scrape_publicsurplus as do_scrape

    result = do_scrape(
        query=req.query,
        category=req.category,
        max_price=req.max_price,
        max_results=req.max_results,
        state=req.state,
    )

    if result["error"]:
        return {"success": False, "error": result["error"], "source_url": result["source_url"], "imported": 0, "total_found": 0}

    imported = skipped = 0
    for listing in result["listings"]:
        res = process_and_store_listing(listing, "publicsurplus")
        if res:
            imported += 1
        else:
            skipped += 1

    import_runs_col.insert_one({
        "source": "publicsurplus", "query": req.query, "category": req.category,
        "count": imported, "skipped": skipped,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "imported": imported, "skipped": skipped,
            "total_found": result["total_found"], "source_url": result["source_url"]}


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------

@app.get("/opportunities")
def get_opportunities(
    min_score: float = Query(70, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    category: Optional[str] = Query(None),
):
    """Return top-scored deals from scored_opportunities, ranked by score desc."""
    query: dict = {"score": {"$gte": min_score}}
    if category and category != "all":
        query["category"] = category

    cursor = scored_opportunities_col.find(query).sort("score", DESCENDING).limit(limit)
    results = []
    for doc in cursor:
        doc.pop("_id", None)
        results.append(doc)

    _write_scored_json(results)

    return {"opportunities": results, "count": len(results), "min_score": min_score}


def _rescore_sync() -> dict:
    """Rescore all active listings, refresh opportunities, and emit only Top 3 action briefings by default."""
    cursor = listings_col.find({"is_sold": {"$ne": True}})
    upserted = alerted = rescored = 0
    for doc in cursor:
        listing_id = str(doc["_id"])
        clean = {k: v for k, v in doc.items() if k != "_id"}

        try:
            breakdown = score_listing(clean)
            update_fields = {
                "score": breakdown["score"],
                "confidence": breakdown.get("confidence"),
                "estimated_profit_low": breakdown.get("estimated_profit_low"),
                "travel_tier": breakdown.get("travel_tier", "unknown"),
                "distance_miles": breakdown.get("distance_miles"),
                "effective_profit_after_travel": breakdown.get("effective_profit_after_travel"),
                "score_breakdown": breakdown,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            action_fields = action_engine.compute_action_score({**clean, **update_fields})
            update_fields.update(action_fields)
            listings_col.update_one({"_id": doc["_id"]}, {"$set": update_fields})
            clean.update(update_fields)
            rescored += 1
        except Exception as exc:
            logger.warning("Rescore failed for %s: %s", listing_id, exc)

        if notifier.is_opportunity(clean):
            _upsert_opportunity(clean, listing_id)
            upserted += 1
            if notifier.maybe_alert(clean, listing_id, clean.get("action_score") or 0.0):
                alerted += 1

    top_docs = list(
        scored_opportunities_col.find({"score": {"$gte": 70}})
        .sort("score", DESCENDING).limit(200)
    )
    top_actions, suppressed_count = action_engine.rank_top_actions([
        _serialize_opportunity(doc) for doc in top_docs
    ], top_n=3)
    if notifier.maybe_alert_top3(top_actions, suppressed_count):
        alerted += 1

    TOP_ACTIONS_CACHE.update({
        "top_actions": top_actions,
        "suppressed_count": suppressed_count,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    })

    _write_scored_json([{k: v for k, v in d.items() if k != "_id"} for d in top_docs])
    return {
        "rescored": rescored,
        "upserted": upserted,
        "alerted": alerted,
        "top_actions_count": len(top_actions),
        "suppressed_count": suppressed_count,
    }


@app.post("/api/opportunities/rescore")
def rescore_opportunities():
    """Scan all listings, upsert opportunities, send Telegram alerts for new high-score items."""
    return _rescore_sync()


@app.post("/api/opportunities/alert-test")
def alert_test():
    """Simulate one Telegram alert using the current top-scoring listing."""
    doc = scored_opportunities_col.find_one(sort=[("score", DESCENDING)])
    if not doc:
        doc = listings_col.find_one(sort=[("score", DESCENDING)])
    if not doc:
        return {"sent": False, "reason": "No listings found"}

    doc.pop("_id", None)
    sent = notifier.send_test_alert(doc)
    return {
        "sent": sent,
        "listing": doc.get("title"),
        "score": doc.get("score"),
        "reason": "OK" if sent else "Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID",
    }


def _get_field(doc: dict, field: str, default=None):
    """Read a field from top level or score_breakdown fallback."""
    v = doc.get(field)
    if v is None:
        v = (doc.get("score_breakdown") or {}).get(field, default)
    return v if v is not None else default


def _serialize_opportunity(doc: dict) -> dict:
    """Serialize a listing doc for opportunity endpoints."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id", ""))
    payload = {
        "id": doc.get("id"),
        "listing_id": doc.get("listing_id") or doc.get("id"),
        "source": doc.get("source"),
        "is_example": doc.get("source") == "seed_data",
        "title": doc.get("title"),
        "price": doc.get("price"),
        "score": doc.get("score"),
        "confidence": _get_field(doc, "confidence"),
        "estimated_profit_low": _get_field(doc, "estimated_profit_low"),
        "effective_profit_after_travel": _get_field(doc, "effective_profit_after_travel"),
        "distance_miles": _get_field(doc, "distance_miles"),
        "travel_tier": _get_field(doc, "travel_tier", "unknown"),
        "listing_url": doc.get("listing_url", ""),
        "category": doc.get("category"),
        "location": doc.get("location"),
        "alert_reason": notifier.alert_reason(doc),
    }

    for key in (
        "action_score",
        "time_to_cash_days",
        "profit_per_day",
        "friction_score",
        "friction_reasons",
        "reason_to_act",
        "risk_flag",
        "rank",
        "why_ranked_here",
    ):
        if key in doc:
            payload[key] = doc.get(key)

    return payload


@app.get("/opportunities/top-actions")
def get_top_actions_brief(
    limit: int = Query(5, ge=1, le=25),
    min_score: float = Query(70, ge=0, le=100),
    include_examples: bool = Query(False),
):
    """Return top actions from cache (populated by rescore) or fall back to DB query."""
    if TOP_ACTIONS_CACHE.get("cached_at") and TOP_ACTIONS_CACHE.get("top_actions") is not None:
        cached = TOP_ACTIONS_CACHE["top_actions"]
        if not include_examples:
            cached = [a for a in cached if a.get("source") != "seed_data"]
        return {
            "top_actions": cached[:limit],
            "count": len(cached[:limit]),
            "suppressed_count": TOP_ACTIONS_CACHE.get("suppressed_count", 0),
            "cached_at": TOP_ACTIONS_CACHE.get("cached_at"),
            "min_score": min_score,
        }

    query: dict = {"score": {"$gte": min_score}}
    if not include_examples:
        query["source"] = {"$ne": "seed_data"}
    docs = list(scored_opportunities_col.find(query).sort("score", DESCENDING).limit(300))
    ranked, suppressed_count = action_engine.rank_top_actions(
        [_serialize_opportunity(doc) for doc in docs], top_n=limit
    )
    return {
        "top_actions": ranked,
        "count": len(ranked),
        "suppressed_count": suppressed_count,
        "cached_at": None,
        "min_score": min_score,
    }


@app.get("/opportunities/local-best")
def get_local_best():
    """Top local/stretch opportunities sorted by score — the flip-ready shortlist."""
    results = []
    cursor = listings_col.find({"is_sold": {"$ne": True}}).sort("score", DESCENDING).limit(500)
    for doc in cursor:
        tier = _get_field(doc, "travel_tier", "unknown")
        profit_low = _get_field(doc, "estimated_profit_low") or 0
        qualifies = (
            tier in ("local", "unknown")
            or (tier == "stretch" and profit_low >= 500)
        )
        if qualifies:
            results.append(_serialize_opportunity(doc))
        if len(results) >= 20:
            break
    return {"opportunities": results, "count": len(results)}


@app.get("/opportunities/high-value")
def get_high_value():
    """Top listings by estimated profit where score >= 85 and confidence >= 0.7."""
    candidates = []
    cursor = listings_col.find({"is_sold": {"$ne": True}}).sort("score", DESCENDING).limit(500)
    for doc in cursor:
        score = doc.get("score") or 0
        confidence = _get_field(doc, "confidence") or 0
        if score >= 85 and confidence >= 0.7:
            candidates.append(_serialize_opportunity(doc))
    candidates.sort(key=lambda x: x.get("estimated_profit_low") or 0, reverse=True)
    return {"opportunities": candidates[:20], "count": min(len(candidates), 20)}


@app.get("/api/opportunities/top-actions")
def get_top_actions(
    limit: int = Query(3, ge=1, le=25),
    min_score: float = Query(70, ge=0, le=100),
    include_examples: bool = Query(False),
):
    """Return the best 1..N moves worth acting on today, ranked by action_score."""
    query: dict = {"score": {"$gte": min_score}}
    if not include_examples:
        query["source"] = {"$ne": "seed_data"}

    docs = list(
        scored_opportunities_col.find(query)
        .sort("score", DESCENDING).limit(300)
    )
    ranked, suppressed_count = action_engine.rank_top_actions(
        [_serialize_opportunity(doc) for doc in docs],
        top_n=limit,
    )
    return {
        "top_actions": ranked,
        "count": len(ranked),
        "suppressed_count": suppressed_count,
        "min_score": min_score,
        "include_examples": include_examples,
    }


@app.get("/api/opportunities/more")
def get_more_opportunities(
    limit: int = Query(20, ge=1, le=100),
    min_score: float = Query(70, ge=0, le=100),
    include_examples: bool = Query(False),
    sort_by: str = Query("action_score", pattern="^(action_score|score|estimated_profit_low)$"),
):
    """Return a broader ranked set beyond the default brief for dashboard/chat expansion."""
    query: dict = {"score": {"$gte": min_score}}
    if not include_examples:
        query["source"] = {"$ne": "seed_data"}

    docs = [_serialize_opportunity(doc) for doc in scored_opportunities_col.find(query).sort("score", DESCENDING).limit(400)]
    ranked, _ = action_engine.rank_top_actions(docs, top_n=len(docs))

    if sort_by == "score":
        ranked.sort(key=lambda x: x.get("score") or 0, reverse=True)
    elif sort_by == "estimated_profit_low":
        ranked.sort(key=lambda x: x.get("estimated_profit_low") or 0, reverse=True)

    return {
        "opportunities": ranked[:limit],
        "count": min(len(ranked), limit),
        "total_ranked": len(ranked),
        "min_score": min_score,
        "include_examples": include_examples,
        "sort_by": sort_by,
    }


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

@app.post("/api/seed")
def seed_data():
    if listings_col.count_documents({}) > 0:
        return {"message": "Data already exists", "count": listings_col.count_documents({})}

    sample_listings = [
        # VEHICLES - highest priority
        {"title": "2018 Honda Civic EX - Must Sell Moving Out of State", "price": 12500, "location": "Austin, TX", "distance": 8, "description": "Moving to another state, need gone ASAP. Clean title, 65k miles, regular maintenance. AC works great. Must sell by end of month.", "listing_url": "https://example.com/listing/honda-civic-2018", "image_url": "https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?w=400", "image_count": 6, "posted_at": "2026-01-13T10:00:00"},
        {"title": "2015 Ford F-150 XLT 4x4 Crew Cab - Price Drop", "price": 18900, "location": "Round Rock, TX", "distance": 15, "description": "Price drop from $22k. 4x4, tow package, bed liner. 89k miles. Need cash for new business. OBO", "listing_url": "https://example.com/listing/f150-2015", "image_url": "https://images.unsplash.com/photo-1544636331-e26879cd4d9b?w=400", "image_count": 8, "posted_at": "2026-01-14T14:30:00"},
        {"title": "Harley Davidson Sportster 883 - Estate Sale", "price": 4200, "location": "Georgetown, TX", "distance": 28, "description": "Estate sale, priced to sell. 2014 model, 12k miles. Runs great. Clear title. Price is firm.", "listing_url": "https://example.com/listing/harley-883", "image_url": "https://images.unsplash.com/photo-1558981806-ec527fa84c39?w=400", "image_count": 5, "posted_at": "2026-01-12T08:00:00"},
        {"title": "2020 Toyota Tacoma TRD Off-Road", "price": 28500, "location": "Cedar Park, TX", "distance": 12, "description": "One owner, no accidents. 42k miles. Comes with tonneau cover and running boards.", "listing_url": "https://example.com/listing/tacoma-2020", "image_url": "https://images.unsplash.com/photo-1559416523-140ddc3d238c?w=400", "image_count": 7, "posted_at": "2026-01-15T09:00:00"},
        {"title": "2016 Kawasaki Ninja 650 - Need Gone This Week", "price": 3800, "location": "Pflugerville, TX", "distance": 10, "description": "Selling because I bought a bigger bike. 18k miles, new tires, recent oil change. Need gone ASAP, make an offer.", "listing_url": "https://example.com/listing/ninja-650", "image_url": "https://images.unsplash.com/photo-1568772585407-9361f9bf3a87?w=400", "image_count": 4, "posted_at": "2026-01-15T16:00:00"},
        {"title": "Utility Trailer 6x12 - Moving Must Sell", "price": 1800, "location": "Kyle, TX", "distance": 22, "description": "Moving to apartment, can't keep it. 6x12 single axle, good tires, ramp gate. Title in hand.", "listing_url": "https://example.com/listing/utility-trailer", "image_url": "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=400", "image_count": 3, "posted_at": "2026-01-14T11:00:00"},
        {"title": "2012 Jeep Wrangler Unlimited Sahara 4WD", "price": 16500, "location": "San Marcos, TX", "distance": 32, "description": "Hard top, automatic, 4WD. 110k miles but well maintained. New brakes last month. Negotiable.", "listing_url": "https://example.com/listing/jeep-wrangler", "image_url": "https://images.unsplash.com/photo-1519741497674-611481863552?w=400", "image_count": 5, "posted_at": "2026-01-13T15:00:00"},

        # EQUIPMENT & HEAVY MACHINERY
        {"title": "Bobcat S185 Skid Steer - Quick Sale", "price": 14500, "location": "Buda, TX", "distance": 18, "description": "Quick sale - need cash for another project. 3200 hours, new bucket teeth, runs strong. Can deliver locally.", "listing_url": "https://example.com/listing/bobcat-s185", "image_url": "https://images.unsplash.com/photo-1581094288338-2314dddb7ece?w=400", "image_count": 6, "posted_at": "2026-01-14T07:00:00"},
        {"title": "John Deere 3032E Compact Tractor w/ Loader", "price": 18000, "location": "Dripping Springs, TX", "distance": 25, "description": "2019 model, 450 hours. Comes with front loader and 60\" mower deck. Selling ranch, everything must go.", "listing_url": "https://example.com/listing/john-deere-3032e", "image_url": "https://images.unsplash.com/photo-1530267981375-f0de937f5f13?w=400", "image_count": 5, "posted_at": "2026-01-12T12:00:00"},
        {"title": "Miller Bobcat 250 Welder/Generator - OBO", "price": 2800, "location": "New Braunfels, TX", "distance": 45, "description": "Runs great, low hours. Great for mobile welding or backup power. Or best offer, flexible on price.", "listing_url": "https://example.com/listing/miller-welder", "image_url": "https://images.unsplash.com/photo-1504328345606-18bbc8c9d7d1?w=400", "image_count": 3, "posted_at": "2026-01-15T10:00:00"},
        {"title": "Dump Trailer 7x14 - Relocating", "price": 5500, "location": "Hutto, TX", "distance": 20, "description": "Relocating out of state. 7x14 dump trailer, hydraulic lift, new tires. Clean title. Motivated seller.", "listing_url": "https://example.com/listing/dump-trailer", "image_url": "https://images.unsplash.com/photo-1619642751034-765dfdf7c58e?w=400", "image_count": 4, "posted_at": "2026-01-13T09:00:00"},
        {"title": "Snap-On Tool Box Full of Tools - Divorce Sale", "price": 3200, "location": "Leander, TX", "distance": 14, "description": "Divorce sale. Snap-On KRL series box plus all tools inside. Retail over $12k. Price is what it is.", "listing_url": "https://example.com/listing/snapon-tools", "image_url": "https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=400", "image_count": 7, "posted_at": "2026-01-15T08:00:00"},
        {"title": "DeWalt 20V MAX 10-Tool Combo Kit - New in Box", "price": 380, "location": "Austin, TX", "distance": 5, "description": "Brand new in box, never opened. Retails $599. Bought for a project that fell through.", "listing_url": "https://example.com/listing/dewalt-combo", "image_url": "https://images.unsplash.com/photo-1572981779307-38b8cabb2407?w=400", "image_count": 3, "posted_at": "2026-01-15T14:00:00"},

        # ELECTRONICS
        {"title": "MacBook Pro 14\" M3 Pro - Moving Sale", "price": 1200, "location": "Austin, TX", "distance": 3, "description": "Moving sale. 2023 MacBook Pro 14\" M3 Pro, 18GB RAM, 512GB. Apple Care until 2026. Must sell this week.", "listing_url": "https://example.com/listing/macbook-m3", "image_url": "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=400", "image_count": 4, "posted_at": "2026-01-15T11:00:00"},
        {"title": "PS5 Digital + 8 Games - Need Cash", "price": 280, "location": "Pflugerville, TX", "distance": 9, "description": "Need cash for car repair. PS5 digital edition with 8 games. Works perfectly, barely used.", "listing_url": "https://example.com/listing/ps5-digital", "image_url": "https://images.unsplash.com/photo-1606813907291-d86efa9b94db?w=400", "image_count": 3, "posted_at": "2026-01-15T13:00:00"},
        {"title": "iPhone 15 Pro Max 256GB Unlocked", "price": 750, "location": "Round Rock, TX", "distance": 14, "description": "Upgraded to 16. Unlocked, works on all carriers. No cracks, minor scuff on back. Battery health 94%.", "listing_url": "https://example.com/listing/iphone15pro", "image_url": "https://images.unsplash.com/photo-1592750475338-74b7b21085ab?w=400", "image_count": 5, "posted_at": "2026-01-14T17:00:00"},
        {"title": "Samsung 65\" OLED 4K Smart TV", "price": 650, "location": "Cedar Park, TX", "distance": 11, "description": "Downsizing, selling 65\" Samsung OLED. Beautiful picture, purchased 2024. No issues.", "listing_url": "https://example.com/listing/samsung-oled", "image_url": "https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=400", "image_count": 2, "posted_at": "2026-01-13T19:00:00"},
        {"title": "Gaming PC RTX 4070 - Emergency Sell", "price": 850, "location": "Austin, TX", "distance": 6, "description": "Emergency sell - need rent money. i7 13700K, RTX 4070, 32GB RAM, 1TB NVMe. Paid $1600 building it.", "listing_url": "https://example.com/listing/gaming-pc", "image_url": "https://images.unsplash.com/photo-1587202372775-e229f172b9d7?w=400", "image_count": 4, "posted_at": "2026-01-15T15:00:00"},
        {"title": "DJI Mini 4 Pro Drone Fly More Combo", "price": 550, "location": "Georgetown, TX", "distance": 28, "description": "Barely used, flew maybe 5 times. Comes with extra batteries, case, everything. Open to offers.", "listing_url": "https://example.com/listing/dji-mini4", "image_url": "https://images.unsplash.com/photo-1473968512647-3e447244af8f?w=400", "image_count": 3, "posted_at": "2026-01-14T10:00:00"},
        {"title": "Nintendo Switch OLED + 12 Games Bundle", "price": 220, "location": "Kyle, TX", "distance": 22, "description": "Kids got new console. OLED Switch with 12 physical games, pro controller, and carry case. Great bundle deal.", "listing_url": "https://example.com/listing/switch-oled", "image_url": "https://images.unsplash.com/photo-1578303512597-81e6cc155b3e?w=400", "image_count": 4, "posted_at": "2026-01-14T12:00:00"},

        # FURNITURE
        {"title": "Herman Miller Aeron Office Chair - Quick Sale", "price": 450, "location": "Austin, TX", "distance": 4, "description": "Office closed down, selling furniture. Herman Miller Aeron, size B, fully loaded. Quick sale needed.", "listing_url": "https://example.com/listing/aeron-chair", "image_url": "https://images.unsplash.com/photo-1580480055273-228ff5388ef8?w=400", "image_count": 3, "posted_at": "2026-01-15T09:30:00"},
        {"title": "Solid Wood Dining Table + 6 Chairs - House Sold", "price": 350, "location": "Lakeway, TX", "distance": 16, "description": "House sold, moving to smaller place. Solid oak dining set, seats 6. Minor scratches but structurally perfect.", "listing_url": "https://example.com/listing/dining-set", "image_url": "https://images.unsplash.com/photo-1617806118233-18e1de247200?w=400", "image_count": 4, "posted_at": "2026-01-14T08:00:00"},
        {"title": "Pottery Barn Leather Sectional - Fire Sale", "price": 800, "location": "Westlake Hills, TX", "distance": 7, "description": "Fire sale pricing. Pottery Barn Turner leather sectional. Retail $4500. Moving overseas, can't take it. Must go this weekend.", "listing_url": "https://example.com/listing/pb-sectional", "image_url": "https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=400", "image_count": 5, "posted_at": "2026-01-15T07:00:00"},
        {"title": "Standing Desk + Dual Monitor Arms", "price": 180, "location": "Austin, TX", "distance": 5, "description": "Electric standing desk 60x30 + dual monitor arms. Upgrading setup, this has to go.", "listing_url": "https://example.com/listing/standing-desk", "image_url": "https://images.unsplash.com/photo-1518455027359-f3f8164ba6bd?w=400", "image_count": 2, "posted_at": "2026-01-15T12:00:00"},
        {"title": "Mid-Century Modern Dresser - Estate Sale", "price": 275, "location": "Bee Cave, TX", "distance": 13, "description": "Estate sale. Beautiful mid-century modern walnut dresser. Some patina but solid construction. Dovetail joints.", "listing_url": "https://example.com/listing/mcm-dresser", "image_url": "https://images.unsplash.com/photo-1558997519-83ea9252edf8?w=400", "image_count": 3, "posted_at": "2026-01-13T14:00:00"},

        # MORE VEHICLES (high priority)
        {"title": "2019 Ram 1500 Big Horn - Reduced Price", "price": 24000, "location": "Manor, TX", "distance": 16, "description": "Reduced from $28k. 5.7 Hemi, 4WD, 58k miles. Clean CarFax. Selling because we got a new truck.", "listing_url": "https://example.com/listing/ram-1500", "image_url": "https://images.unsplash.com/photo-1544636331-e26879cd4d9b?w=400", "image_count": 7, "posted_at": "2026-01-14T16:00:00"},
        {"title": "Honda CRF250L Dual Sport - Low Miles", "price": 3500, "location": "Bastrop, TX", "distance": 30, "description": "2021 CRF250L, only 2800 miles. Street legal dual sport. Perfect for commuting or trail riding.", "listing_url": "https://example.com/listing/crf250l", "image_url": "https://images.unsplash.com/photo-1449426468159-d96dbf08f19f?w=400", "image_count": 4, "posted_at": "2026-01-14T13:00:00"},

        # MORE EQUIPMENT
        {"title": "Kubota BX2380 w/ Backhoe - Selling Ranch", "price": 22000, "location": "Wimberley, TX", "distance": 38, "description": "Selling ranch property. Kubota BX2380 with BT603 backhoe attachment. 280 hours. Like new condition.", "listing_url": "https://example.com/listing/kubota-bx2380", "image_url": "https://images.unsplash.com/photo-1530267981375-f0de937f5f13?w=400", "image_count": 6, "posted_at": "2026-01-13T11:00:00"},
        {"title": "Lincoln Electric MIG Welder + Cart - OBO", "price": 450, "location": "Taylor, TX", "distance": 35, "description": "Lincoln MIG welder with cart, mask, and supplies. Upgraded to a bigger unit. Or best offer.", "listing_url": "https://example.com/listing/lincoln-welder", "image_url": "https://images.unsplash.com/photo-1504328345606-18bbc8c9d7d1?w=400", "image_count": 2, "posted_at": "2026-01-15T06:00:00"},
    ]

    count = 0
    for item in sample_listings:
        result = process_and_store_listing(item, "seed_data")
        if result:
            count += 1

    import_runs_col.insert_one({
        "source": "seed_data",
        "count": count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return {"message": f"Seeded {count} listings", "count": count}


# ---------------------------------------------------------------------------
# Scheduler helpers
# ---------------------------------------------------------------------------

def _log_scrape_run(source: str, total_found: int, imported: int, error: Optional[str] = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "total_found": total_found,
        "imported": imported,
        "error": error,
    }
    try:
        SCRAPE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log: list = []
        if SCRAPE_LOG_PATH.exists():
            log = json.loads(SCRAPE_LOG_PATH.read_text())
        log.append(entry)
        SCRAPE_LOG_PATH.write_text(json.dumps(log[-500:], indent=2))
    except Exception as exc:
        logger.warning("Could not write scrape_log.json: %s", exc)


def _process_batch(listings: list, source: str) -> tuple:
    imported = skipped = 0
    for listing in listings:
        if process_and_store_listing(listing, source):
            imported += 1
        else:
            skipped += 1
    return imported, skipped


def _get_recent_log(n: int = 10) -> list:
    if not SCRAPE_LOG_PATH.exists():
        return []
    try:
        return json.loads(SCRAPE_LOG_PATH.read_text())[-n:]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Scheduled async run functions
# ---------------------------------------------------------------------------

async def _run_craigslist_sched() -> int:
    from scrapers.craigslist import scrape_craigslist as do_scrape
    result = await asyncio.to_thread(do_scrape, city=CRAIGSLIST_LOCATION, category="all", max_results=80)
    total = result.get("total_found", 0)
    imported, _ = await asyncio.to_thread(_process_batch, result.get("listings", []), "craigslist")
    _log_scrape_run("craigslist", total, imported, result.get("error"))
    if imported:
        import_runs_col.insert_one({
            "source": "craigslist", "city": CRAIGSLIST_LOCATION,
            "count": imported, "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return imported


async def _run_govplanet_sched() -> int:
    from scrapers.govplanet import scrape_govplanet as do_scrape
    result = await asyncio.to_thread(do_scrape, category="all", max_results=50)
    total = result.get("total_found", 0)
    imported, _ = await asyncio.to_thread(_process_batch, result.get("listings", []), "govplanet")
    _log_scrape_run("govplanet", total, imported, result.get("error"))
    if imported:
        import_runs_col.insert_one({
            "source": "govplanet", "count": imported,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return imported


async def _run_publicsurplus_sched() -> int:
    from scrapers.publicsurplus import scrape_publicsurplus as do_scrape
    imported = 0
    for cat in ("vehicles", "heavy-equipment", "tools"):
        result = await asyncio.to_thread(do_scrape, category=cat, max_results=30)
        batch, _ = await asyncio.to_thread(_process_batch, result.get("listings", []), "publicsurplus")
        imported += batch
        _log_scrape_run(f"publicsurplus/{cat}", result.get("total_found", 0), batch, result.get("error"))
    if imported:
        import_runs_col.insert_one({
            "source": "publicsurplus", "count": imported,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    return imported


async def _run_facebook_sched() -> int:
    cookie_state = _inspect_facebook_cookies()
    SCHEDULER_STATUS["facebook"]["auth"] = cookie_state
    if not cookie_state["valid"]:
        reason = cookie_state["message"] or "invalid_cookies"
        logger.warning("FB scheduler blocked: %s (path=%s)", reason, FB_COOKIES_PATH)
        SCHEDULER_STATUS["facebook"]["last_error"] = reason
        _log_scrape_run("facebook", 0, 0, reason)
        return 0
    try:
        from modules.marketplace_scraper.scraper import PlaywrightScraper
        async with PlaywrightScraper(cookies_path=str(FB_COOKIES_PATH), headless=True) as scraper:
            raw = await scraper.search_multiple(
                queries=["electronics", "furniture", "tools", "motorcycles"],
                location="", max_pages=2,
            )
        imported, _ = await asyncio.to_thread(_process_batch, raw, "facebook")
        _log_scrape_run("facebook", len(raw), imported)
        return imported
    except Exception as exc:
        logger.error("FB scheduler: scrape failed: %s", exc)
        SCHEDULER_STATUS["facebook"]["last_error"] = str(exc)
        _log_scrape_run("facebook", 0, 0, str(exc))
        return 0


async def _run_ebay_sched() -> int:
    from scrapers.ebay import scrape_ebay as do_scrape
    imported = 0
    for cat in ("motorcycles", "heavy-equipment", "tools"):
        try:
            result = await asyncio.to_thread(
                do_scrape, category=cat, listing_type="buy-it-now", max_results=30
            )
            batch, _ = await asyncio.to_thread(_process_batch, result.get("listings", []), "ebay")
            imported += batch
            _log_scrape_run(f"ebay/{cat}", result.get("total_found", 0), batch, result.get("error"))
        except Exception as exc:
            logger.warning("eBay scheduler (%s): %s", cat, exc)
            _log_scrape_run(f"ebay/{cat}", 0, 0, str(exc))
    return imported


async def _schedule_loop(source: str, fn, interval_minutes: int, initial_delay_secs: int = 0) -> None:
    if initial_delay_secs:
        await asyncio.sleep(initial_delay_secs)
    while True:
        now = datetime.now(timezone.utc)
        SCHEDULER_STATUS[source]["running"] = True
        SCHEDULER_STATUS[source]["last_run"] = now.isoformat()
        next_ts = now.timestamp() + interval_minutes * 60
        SCHEDULER_STATUS[source]["next_run"] = datetime.fromtimestamp(next_ts, tz=timezone.utc).isoformat()
        try:
            count = await fn()
            SCHEDULER_STATUS[source]["last_imported"] = count
            if source != "facebook" or SCHEDULER_STATUS[source].get("last_error") in (None, ""):
                SCHEDULER_STATUS[source]["last_error"] = None
            logger.info("Scheduler [%s]: %d new imports", source, count)
            if count > 0:
                await asyncio.to_thread(_rescore_sync)
        except Exception as exc:
            logger.error("Scheduler [%s] failed: %s", source, exc)
            SCHEDULER_STATUS[source]["last_error"] = str(exc)
        finally:
            SCHEDULER_STATUS[source]["running"] = False
        elapsed = datetime.now(timezone.utc).timestamp() - now.timestamp()
        await asyncio.sleep(max(10, interval_minutes * 60 - elapsed))


# ---------------------------------------------------------------------------
# Scraper status endpoint
# ---------------------------------------------------------------------------

@app.get("/api/scraper/status")
def get_scraper_status():
    """Return scheduler state per source plus last 10 log entries."""
    SCHEDULER_STATUS["facebook"]["auth"] = _inspect_facebook_cookies()
    if not SCHEDULER_STATUS["facebook"]["auth"]["valid"]:
        SCHEDULER_STATUS["facebook"]["last_error"] = SCHEDULER_STATUS["facebook"]["auth"]["message"]
    return {
        "sources": SCHEDULER_STATUS,
        "log_path": str(SCRAPE_LOG_PATH),
        "recent_log": _get_recent_log(10),
    }


# ---------------------------------------------------------------------------
# Auto-seed and populate scored_opportunities on startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup_event():
    if listings_col.count_documents({}) == 0:
        logger.info("No listings found, seeding sample data...")
        seed_data()
        logger.info("Seed complete")

    if scored_opportunities_col.count_documents({}) == 0:
        logger.info("Populating scored_opportunities from existing listings...")
        count = 0
        for doc in listings_col.find({"is_sold": {"$ne": True}}):
            listing_id = str(doc["_id"])
            clean = {k: v for k, v in doc.items() if k != "_id"}
            if notifier.is_opportunity(clean):
                _upsert_opportunity(clean, listing_id)
                count += 1
        logger.info("scored_opportunities populated with %d items", count)
        top = list(
            scored_opportunities_col.find({"score": {"$gte": 70}})
            .sort("score", DESCENDING)
            .limit(200)
        )
        _write_scored_json([{k: v for k, v in d.items() if k != "_id"} for d in top])

    # Launch background scrape scheduler
    # Staggered initial delays prevent all sources hammering at once on startup
    logger.info("Launching background scrape scheduler...")
    asyncio.create_task(_schedule_loop("craigslist",    _run_craigslist_sched,    interval_minutes=30, initial_delay_secs=15))
    asyncio.create_task(_schedule_loop("govplanet",     _run_govplanet_sched,     interval_minutes=60, initial_delay_secs=45))
    asyncio.create_task(_schedule_loop("publicsurplus", _run_publicsurplus_sched, interval_minutes=60, initial_delay_secs=75))
    asyncio.create_task(_schedule_loop("facebook",      _run_facebook_sched,      interval_minutes=45, initial_delay_secs=105))
    asyncio.create_task(_schedule_loop("ebay",          _run_ebay_sched,          interval_minutes=60, initial_delay_secs=135))
    logger.info(
        "Scheduler running: craigslist=30m, govplanet=60m, publicsurplus=60m, facebook=45m, ebay=60m"
    )
