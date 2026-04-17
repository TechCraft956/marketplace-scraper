"""
SQLite-backed storage for marketplace listings, scores, and scrape run history.

Uses aiosqlite for async I/O. Designed to be local-first with zero external
dependencies — just a single .db file.

Tables:
  - listings    : deduplicated listing records (keyed on listing_url)
  - scores      : score + breakdown per listing
  - scrape_runs : run history with metadata
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_LISTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS listings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_url     TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    price           REAL,
    price_raw       TEXT,
    location        TEXT,
    distance        REAL,
    image_url       TEXT,
    image_count     INTEGER DEFAULT 0,
    posted_at       TEXT,
    posted_raw      TEXT,
    description     TEXT,
    category        TEXT,
    seller_name     TEXT,
    is_sold         INTEGER DEFAULT 0,
    is_contacted    INTEGER DEFAULT 0,
    first_seen_at   TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL,
    run_id          INTEGER
);
"""

CREATE_SCORES_TABLE = """
CREATE TABLE IF NOT EXISTS scores (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_url          TEXT    NOT NULL UNIQUE,
    score                REAL    NOT NULL,
    price_score          REAL,
    urgency_score        REAL,
    recency_score        REAL,
    image_score          REAL,
    distance_score       REAL,
    price_vs_median_pct  REAL,
    category_median      REAL,
    matched_keywords     TEXT,   -- JSON array
    days_listed          REAL,
    explanation          TEXT,
    scored_at            TEXT    NOT NULL,
    FOREIGN KEY (listing_url) REFERENCES listings (listing_url)
);
"""

CREATE_SCRAPE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT    NOT NULL,
    completed_at        TEXT,
    status              TEXT    NOT NULL DEFAULT 'running',  -- running|completed|failed
    queries             TEXT,   -- JSON array
    location            TEXT,
    total_scraped       INTEGER DEFAULT 0,
    total_after_filter  INTEGER DEFAULT 0,
    total_stored        INTEGER DEFAULT 0,
    new_listings        INTEGER DEFAULT 0,
    error_message       TEXT
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_listings_url ON listings (listing_url);",
    "CREATE INDEX IF NOT EXISTS idx_listings_score ON listings (listing_url);",
    "CREATE INDEX IF NOT EXISTS idx_scores_score ON scores (score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings (first_seen_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_listings_sold ON listings (is_sold);",
]


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------

class MarketplaceStorage:
    """
    Async SQLite storage for marketplace data.

    Usage:
        storage = MarketplaceStorage("data/marketplace.db")
        await storage.initialize()

        run_id = await storage.start_run(queries=["macbook"], location="austin")
        count = await storage.save_listings(listings, run_id=run_id)
        await storage.complete_run(run_id, total_scraped=50, new=count)

        deals = await storage.get_opportunities(min_score=60, limit=20)
    """

    def __init__(self, db_path: str = "data/marketplace.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def initialize(self) -> None:
        """Create tables and indexes if they don't exist."""
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")

            await db.execute(CREATE_LISTINGS_TABLE)
            await db.execute(CREATE_SCORES_TABLE)
            await db.execute(CREATE_SCRAPE_RUNS_TABLE)

            for idx_sql in CREATE_INDEXES:
                await db.execute(idx_sql)

            await db.commit()

        self._initialized = True
        logger.info("Storage initialized: %s", self.db_path)

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "Storage not initialized. Call await storage.initialize() first."
            )

    # ------------------------------------------------------------------
    # Scrape run management
    # ------------------------------------------------------------------

    async def start_run(
        self,
        queries: list[str],
        location: str,
    ) -> int:
        """Insert a new scrape run record and return its ID."""
        self._ensure_initialized()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(str(self.db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO scrape_runs (started_at, status, queries, location)
                VALUES (?, 'running', ?, ?)
                """,
                (now, json.dumps(queries), location),
            )
            await db.commit()
            run_id = cursor.lastrowid

        logger.info("Started scrape run %d for queries %s", run_id, queries)
        return run_id  # type: ignore[return-value]

    async def complete_run(
        self,
        run_id: int,
        total_scraped: int = 0,
        total_after_filter: int = 0,
        total_stored: int = 0,
        new_listings: int = 0,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> None:
        """Update a run record on completion."""
        self._ensure_initialized()
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                """
                UPDATE scrape_runs
                SET completed_at=?, status=?, total_scraped=?,
                    total_after_filter=?, total_stored=?,
                    new_listings=?, error_message=?
                WHERE id=?
                """,
                (
                    now, status, total_scraped, total_after_filter,
                    total_stored, new_listings, error_message, run_id,
                ),
            )
            await db.commit()

        logger.info(
            "Completed run %d: scraped=%d, stored=%d, new=%d",
            run_id, total_scraped, total_stored, new_listings,
        )

    # ------------------------------------------------------------------
    # Listing persistence
    # ------------------------------------------------------------------

    async def save_listings(
        self,
        listings: list[dict[str, Any]],
        run_id: Optional[int] = None,
    ) -> int:
        """
        Upsert a list of listing dicts into storage.
        Deduplicates by listing_url.

        Returns the count of NEW listings inserted (vs. updated).
        """
        self._ensure_initialized()
        if not listings:
            return 0

        now = datetime.utcnow().isoformat()
        new_count = 0

        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute("PRAGMA foreign_keys=ON;")

            for listing in listings:
                url = listing.get("listing_url", "")
                if not url:
                    logger.warning("Skipping listing with no URL")
                    continue

                # Check if already exists
                cursor = await db.execute(
                    "SELECT id FROM listings WHERE listing_url = ?", (url,)
                )
                existing = await cursor.fetchone()

                if existing:
                    # Update last_seen_at and mutable fields
                    await db.execute(
                        """
                        UPDATE listings
                        SET last_seen_at=?, title=?, price=?, price_raw=?,
                            location=?, distance=?, image_url=?, image_count=?,
                            posted_at=?, posted_raw=?, description=?,
                            category=?, seller_name=?, run_id=?
                        WHERE listing_url=?
                        """,
                        (
                            now,
                            listing.get("title"),
                            listing.get("price"),
                            listing.get("price_raw"),
                            listing.get("location"),
                            listing.get("distance"),
                            listing.get("image_url"),
                            listing.get("image_count", 0),
                            listing.get("posted_at"),
                            listing.get("posted_raw"),
                            listing.get("description"),
                            listing.get("category"),
                            listing.get("seller_name"),
                            run_id,
                            url,
                        ),
                    )
                else:
                    # Insert new listing
                    await db.execute(
                        """
                        INSERT INTO listings (
                            listing_url, title, price, price_raw, location,
                            distance, image_url, image_count, posted_at,
                            posted_raw, description, category, seller_name,
                            first_seen_at, last_seen_at, run_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            url,
                            listing.get("title"),
                            listing.get("price"),
                            listing.get("price_raw"),
                            listing.get("location"),
                            listing.get("distance"),
                            listing.get("image_url"),
                            listing.get("image_count", 0),
                            listing.get("posted_at"),
                            listing.get("posted_raw"),
                            listing.get("description"),
                            listing.get("category"),
                            listing.get("seller_name"),
                            now,
                            now,
                            run_id,
                        ),
                    )
                    new_count += 1

                # Upsert score if present
                score = listing.get("score")
                if score is not None:
                    breakdown = listing.get("score_breakdown", {})
                    await db.execute(
                        """
                        INSERT INTO scores (
                            listing_url, score, price_score, urgency_score,
                            recency_score, image_score, distance_score,
                            price_vs_median_pct, category_median,
                            matched_keywords, days_listed, explanation, scored_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(listing_url) DO UPDATE SET
                            score=excluded.score,
                            price_score=excluded.price_score,
                            urgency_score=excluded.urgency_score,
                            recency_score=excluded.recency_score,
                            image_score=excluded.image_score,
                            distance_score=excluded.distance_score,
                            price_vs_median_pct=excluded.price_vs_median_pct,
                            category_median=excluded.category_median,
                            matched_keywords=excluded.matched_keywords,
                            days_listed=excluded.days_listed,
                            explanation=excluded.explanation,
                            scored_at=excluded.scored_at
                        """,
                        (
                            url,
                            score,
                            breakdown.get("price_score"),
                            breakdown.get("urgency_score"),
                            breakdown.get("recency_score"),
                            breakdown.get("image_score"),
                            breakdown.get("distance_score"),
                            breakdown.get("price_vs_median_pct"),
                            breakdown.get("category_median"),
                            json.dumps(breakdown.get("matched_keywords", [])),
                            breakdown.get("days_listed"),
                            breakdown.get("explanation"),
                            now,
                        ),
                    )

            await db.commit()

        logger.info(
            "Saved %d listings (%d new, %d updated)",
            len(listings), new_count, len(listings) - new_count,
        )
        return new_count

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def get_opportunities(
        self,
        min_score: float = 0.0,
        limit: int = 50,
        offset: int = 0,
        exclude_sold: bool = True,
        exclude_contacted: bool = False,
        max_price: Optional[float] = None,
        min_price: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve top scoring opportunities from storage.

        Returns a list of listing dicts enriched with score data,
        sorted by score descending.
        """
        self._ensure_initialized()

        conditions = ["s.score >= ?"]
        params: list[Any] = [min_score]

        if exclude_sold:
            conditions.append("l.is_sold = 0")
        if exclude_contacted:
            conditions.append("l.is_contacted = 0")
        if max_price is not None:
            conditions.append("(l.price IS NULL OR l.price <= ?)")
            params.append(max_price)
        if min_price is not None:
            conditions.append("(l.price IS NULL OR l.price >= ?)")
            params.append(min_price)

        where_clause = " AND ".join(conditions)

        sql = f"""
            SELECT
                l.listing_url, l.title, l.price, l.price_raw,
                l.location, l.distance, l.image_url, l.image_count,
                l.posted_at, l.posted_raw, l.description, l.category,
                l.seller_name, l.is_sold, l.is_contacted,
                l.first_seen_at, l.last_seen_at,
                s.score, s.price_score, s.urgency_score, s.recency_score,
                s.image_score, s.distance_score, s.price_vs_median_pct,
                s.category_median, s.matched_keywords, s.days_listed,
                s.explanation
            FROM listings l
            INNER JOIN scores s ON l.listing_url = s.listing_url
            WHERE {where_clause}
            ORDER BY s.score DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            d = dict(row)
            # Parse JSON fields
            try:
                d["matched_keywords"] = json.loads(d.get("matched_keywords") or "[]")
            except Exception:
                d["matched_keywords"] = []
            # Build nested score_breakdown for API compatibility
            d["score_breakdown"] = {
                "score": d.get("score"),
                "price_score": d.get("price_score"),
                "urgency_score": d.get("urgency_score"),
                "recency_score": d.get("recency_score"),
                "image_score": d.get("image_score"),
                "distance_score": d.get("distance_score"),
                "price_vs_median_pct": d.get("price_vs_median_pct"),
                "category_median": d.get("category_median"),
                "matched_keywords": d.get("matched_keywords"),
                "days_listed": d.get("days_listed"),
                "explanation": d.get("explanation"),
            }
            results.append(d)

        return results

    async def get_listing(self, listing_url: str) -> Optional[dict[str, Any]]:
        """Retrieve a single listing by URL."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT l.*, s.score, s.explanation, s.matched_keywords
                FROM listings l
                LEFT JOIN scores s ON l.listing_url = s.listing_url
                WHERE l.listing_url = ?
                """,
                (listing_url,),
            )
            row = await cursor.fetchone()

        if not row:
            return None
        d = dict(row)
        try:
            d["matched_keywords"] = json.loads(d.get("matched_keywords") or "[]")
        except Exception:
            d["matched_keywords"] = []
        return d

    # ------------------------------------------------------------------
    # Status updates
    # ------------------------------------------------------------------

    async def mark_sold(self, listing_url: str) -> None:
        """Mark a listing as sold (hide from opportunities view)."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "UPDATE listings SET is_sold=1 WHERE listing_url=?",
                (listing_url,),
            )
            await db.commit()
        logger.info("Marked as sold: %s", listing_url)

    async def mark_contacted(self, listing_url: str) -> None:
        """Mark a listing as contacted by the user."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            await db.execute(
                "UPDATE listings SET is_contacted=1 WHERE listing_url=?",
                (listing_url,),
            )
            await db.commit()
        logger.info("Marked as contacted: %s", listing_url)

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    async def get_run_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent scrape run records."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM scrape_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()

        return [dict(r) for r in rows]

    async def get_stats(self) -> dict[str, Any]:
        """Return summary statistics for the dashboard."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            db.row_factory = aiosqlite.Row

            total_listings = (await (await db.execute(
                "SELECT COUNT(*) as c FROM listings"
            )).fetchone())["c"]

            active_listings = (await (await db.execute(
                "SELECT COUNT(*) as c FROM listings WHERE is_sold=0"
            )).fetchone())["c"]

            high_score_count = (await (await db.execute(
                "SELECT COUNT(*) as c FROM scores WHERE score >= 70"
            )).fetchone())["c"]

            avg_score_row = await (await db.execute(
                "SELECT AVG(score) as avg FROM scores"
            )).fetchone()
            avg_score = avg_score_row["avg"] if avg_score_row else None

            last_run_row = await (await db.execute(
                "SELECT completed_at, new_listings FROM scrape_runs "
                "WHERE status='completed' ORDER BY completed_at DESC LIMIT 1"
            )).fetchone()

        return {
            "total_listings": total_listings,
            "active_listings": active_listings,
            "high_score_count": high_score_count,
            "avg_score": round(avg_score, 1) if avg_score else None,
            "last_run_at": last_run_row["completed_at"] if last_run_row else None,
            "last_run_new": last_run_row["new_listings"] if last_run_row else None,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def prune_old_listings(
        self,
        max_count: int = 1000,
        min_score_to_keep: float = 0.0,
    ) -> int:
        """
        Delete oldest, lowest-scored listings if total count exceeds max_count.
        Returns the number of records deleted.
        """
        self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as db:
            count_row = await (await db.execute(
                "SELECT COUNT(*) as c FROM listings"
            )).fetchone()
            total = count_row[0]

            if total <= max_count:
                return 0

            to_delete = total - max_count
            await db.execute(
                """
                DELETE FROM listings WHERE listing_url IN (
                    SELECT l.listing_url FROM listings l
                    LEFT JOIN scores s ON l.listing_url = s.listing_url
                    WHERE l.is_sold = 0
                    ORDER BY COALESCE(s.score, 0) ASC, l.first_seen_at ASC
                    LIMIT ?
                )
                """,
                (to_delete,),
            )
            await db.execute(
                """
                DELETE FROM scores WHERE listing_url NOT IN (
                    SELECT listing_url FROM listings
                )
                """
            )
            await db.commit()

        logger.info("Pruned %d old listings (max_count=%d)", to_delete, max_count)
        return to_delete
