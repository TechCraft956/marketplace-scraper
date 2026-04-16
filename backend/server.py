"""
DealScope — Marketplace Deal Intelligence API
FastAPI backend with MongoDB storage, scoring engine, and multi-source ingestion.
"""
import os
import csv
import json
import io
import re
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient, DESCENDING
from bson import ObjectId

# Import scoring engine directly (avoid triggering full module init)
from modules.marketplace_scraper.scorer import ResaleScorer, CATEGORY_PRICE_REFERENCE, URGENCY_KEYWORDS

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

# Ensure indexes
listings_col.create_index([("score", DESCENDING)])
listings_col.create_index("listing_hash", unique=True, sparse=True)
listings_col.create_index("category")
listings_col.create_index("is_sold")


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


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "dealscope", "version": "1.0.0"}


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
    listing_data["score_breakdown"] = breakdown

    result = listings_col.insert_one(listing_data)
    return str(result.inserted_id)


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


# Auto-seed on startup
@app.on_event("startup")
async def startup_event():
    if listings_col.count_documents({}) == 0:
        logger.info("No listings found, seeding sample data...")
        seed_data()
        logger.info("Seed complete")
