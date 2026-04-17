"""
MarketplaceScraperModule — Operator Dashboard integration.

Implements the BaseModule interface:
  - name, description, version, config_schema
  - async fetch() → DashboardPayload
  - async stream() → AsyncGenerator yielding DashboardPayload updates

Orchestrates: scraper → scorer → filter → storage → surface
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, AsyncGenerator, Optional, Type

from .config_schema import MarketplaceScraperConfig
from .filters import apply_standard_filters
from .scorer import score_listings
from .scraper import PlaywrightScraper
from .storage import MarketplaceStorage

logger = logging.getLogger(__name__)

# Optional shared-feed integration — fails silently if not present
import sys as _sys
from pathlib import Path as _Path
_feed_dir = _Path(__file__).parents[4] / "shared-feed"
if str(_feed_dir) not in _sys.path:
    _sys.path.insert(0, str(_feed_dir))
try:
    from feed import write_app_status as _write_feed
except ImportError:
    _write_feed = None


# ---------------------------------------------------------------------------
# BaseModule interface (mirrors backend/modules/base.py in the host dashboard)
# ---------------------------------------------------------------------------

class DashboardPayload:
    """
    Standardized payload returned by fetch() and emitted by stream().
    Matches the interface expected by the dashboard's panel host.
    """

    def __init__(
        self,
        module_name: str,
        data: list[dict[str, Any]],
        meta: dict[str, Any],
        updated_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> None:
        self.module_name = module_name
        self.data = data
        self.meta = meta
        self.updated_at = updated_at or datetime.utcnow()
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module_name,
            "data": self.data,
            "meta": self.meta,
            "updated_at": self.updated_at.isoformat(),
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------

class MarketplaceScraperModule:
    """
    Facebook Marketplace deal scraper module for the Operator Dashboard.

    Register with the dashboard's module registry:

        from backend.modules.marketplace_scraper import MarketplaceScraperModule
        registry.register(MarketplaceScraperModule)

    Config can be passed as a dict matching MarketplaceScraperConfig fields.
    """

    # ---- BaseModule interface fields ----
    name: str = "marketplace_scraper"
    description: str = (
        "Scrapes Facebook Marketplace for deal opportunities, scores listings "
        "by resale potential, and surfaces high-value finds in the dashboard."
    )
    version: str = "1.0.0"
    config_schema: Type[MarketplaceScraperConfig] = MarketplaceScraperConfig

    def __init__(self, config: dict[str, Any] | MarketplaceScraperConfig) -> None:
        if isinstance(config, dict):
            self.config = MarketplaceScraperConfig(**config)
        else:
            self.config = config

        self.storage = MarketplaceStorage(db_path=self.config.db_path)
        self._scraper: Optional[PlaywrightScraper] = None
        self._last_scrape_at: Optional[datetime] = None
        self._is_scraping: bool = False
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize storage and any persistent resources."""
        await self.storage.initialize()
        self._initialized = True
        logger.info(
            "MarketplaceScraperModule initialized (db: %s)", self.config.db_path
        )

    async def shutdown(self) -> None:
        """Clean up browser and storage connections."""
        if self._scraper:
            await self._scraper.stop()
        logger.info("MarketplaceScraperModule shutdown")

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    async def run_scrape_cycle(self) -> dict[str, Any]:
        """
        Execute one full scrape-score-filter-store cycle.

        Returns a summary dict with run statistics.
        """
        if self._is_scraping:
            logger.warning("Scrape already in progress, skipping")
            return {"skipped": True, "reason": "scrape_in_progress"}

        self._is_scraping = True
        run_id: Optional[int] = None

        try:
            cfg = self.config

            # Start run record
            run_id = await self.storage.start_run(
                queries=cfg.search_queries,
                location=cfg.location_city,
            )

            logger.info(
                "Starting scrape cycle (run_id=%d, queries=%s, location=%s)",
                run_id, cfg.search_queries, cfg.location_city,
            )

            # Build proxy config if set
            proxy = cfg.proxy_config  # Returns None if not configured

            # Launch scraper
            raw_listings: list[dict[str, Any]] = []

            async with PlaywrightScraper(
                cookies_path=cfg.cookies_path,
                headless=cfg.headless,
                proxy=proxy,
            ) as scraper:
                raw_listings = await scraper.search_multiple(
                    queries=cfg.search_queries,
                    location=cfg.location_city,
                    min_price=cfg.price_min,
                    max_price=cfg.price_max,
                    radius_miles=cfg.radius_miles,
                    max_pages=cfg.max_pages_per_query,
                    inter_query_delay=cfg.inter_query_delay,
                )

            total_scraped = len(raw_listings)
            logger.info("Scraped %d raw listings", total_scraped)

            # Score listings
            scored = score_listings(
                listings=raw_listings,
                user_zip=cfg.location_zip,
                max_distance=float(cfg.radius_miles),
            )

            # Apply filter pipeline
            filtered = apply_standard_filters(
                listings=scored,
                min_price=cfg.price_min,
                max_price=cfg.price_max,
                max_distance=float(cfg.radius_miles),
                categories=cfg.categories or None,
                include_keywords=cfg.included_keywords or None,
                exclude_keywords=cfg.excluded_keywords or None,
                min_score=cfg.min_resale_score,
                require_images=cfg.require_images,
                deduplicate=True,
            )

            total_after_filter = len(filtered)
            logger.info(
                "%d listings after filtering (min_score=%.0f)",
                total_after_filter, cfg.min_resale_score,
            )

            # Store
            new_count = await self.storage.save_listings(filtered, run_id=run_id)

            # Prune if over limit
            await self.storage.prune_old_listings(
                max_count=cfg.max_listings_stored
            )

            await self.storage.complete_run(
                run_id=run_id,
                total_scraped=total_scraped,
                total_after_filter=total_after_filter,
                total_stored=total_after_filter,
                new_listings=new_count,
                status="completed",
            )

            self._last_scrape_at = datetime.utcnow()

            summary = {
                "run_id": run_id,
                "total_scraped": total_scraped,
                "total_after_filter": total_after_filter,
                "new_listings": new_count,
                "completed_at": self._last_scrape_at.isoformat(),
            }
            logger.info("Scrape cycle complete: %s", summary)
            if _write_feed:
                _write_feed(
                    app_id="marketplace_scraper",
                    app_name="Marketplace Scraper",
                    status="idle",
                    metrics={"new_listings": new_count, "total_filtered": total_after_filter, "total_scraped": total_scraped},
                    recent_events=[f"Scan complete — {new_count} new, {total_after_filter} passed filters"],
                    actions=["run_scan", "open_results"],
                )
            return summary

        except Exception as exc:
            logger.error("Scrape cycle failed: %s", exc, exc_info=True)
            if _write_feed:
                _write_feed(
                    app_id="marketplace_scraper",
                    app_name="Marketplace Scraper",
                    status="error",
                    recent_events=[f"Scrape failed: {str(exc)[:120]}"],
                )
            if run_id is not None:
                await self.storage.complete_run(
                    run_id=run_id,
                    status="failed",
                    error_message=str(exc),
                )
            return {"error": str(exc), "run_id": run_id}
        finally:
            self._is_scraping = False

    # ------------------------------------------------------------------
    # BaseModule interface
    # ------------------------------------------------------------------

    async def fetch(self) -> DashboardPayload:
        """
        Return the current best opportunities from storage.
        Does NOT trigger a new scrape — call run_scrape_cycle() for that.

        This is called by the dashboard on panel open and on refresh.
        """
        if not self._initialized:
            await self.initialize()

        try:
            opportunities = await self.storage.get_opportunities(
                min_score=self.config.min_resale_score,
                limit=100,
            )
            stats = await self.storage.get_stats()
            run_history = await self.storage.get_run_history(limit=5)

            return DashboardPayload(
                module_name=self.name,
                data=opportunities,
                meta={
                    "stats": stats,
                    "run_history": run_history,
                    "config": {
                        "search_queries": self.config.search_queries,
                        "location": self.config.location_city,
                        "radius_miles": self.config.radius_miles,
                        "min_score": self.config.min_resale_score,
                        "refresh_interval_minutes": self.config.refresh_interval_minutes,
                    },
                    "is_scraping": self._is_scraping,
                    "last_scrape_at": (
                        self._last_scrape_at.isoformat()
                        if self._last_scrape_at
                        else None
                    ),
                },
            )
        except Exception as exc:
            logger.error("fetch() failed: %s", exc, exc_info=True)
            return DashboardPayload(
                module_name=self.name,
                data=[],
                meta={},
                error=str(exc),
            )

    async def stream(self) -> AsyncGenerator[DashboardPayload, None]:
        """
        Async generator that yields a DashboardPayload on each scrape cycle
        and also on the configured refresh interval.

        The dashboard's SSE handler should consume this generator and push
        updates to connected frontend clients.

        Usage in dashboard router:
            async for payload in module.stream():
                await sse_manager.broadcast(payload.to_dict())
        """
        if not self._initialized:
            await self.initialize()

        interval_seconds = self.config.refresh_interval_minutes * 60

        # Yield initial snapshot immediately
        yield await self.fetch()

        while True:
            # Wait for the refresh interval (sleep in chunks for cancellation)
            elapsed = 0.0
            chunk = 15.0
            while elapsed < interval_seconds:
                await asyncio.sleep(min(chunk, interval_seconds - elapsed))
                elapsed += chunk

            logger.info("Stream: triggering scheduled scrape")
            try:
                await self.run_scrape_cycle()
            except Exception as exc:
                logger.error("Stream scrape cycle error: %s", exc)

            yield await self.fetch()

    # ------------------------------------------------------------------
    # Manual trigger endpoint (called via dashboard API)
    # ------------------------------------------------------------------

    async def trigger_scrape(self) -> dict[str, Any]:
        """
        Manually trigger a scrape cycle.
        Called by the dashboard's /api/modules/marketplace_scraper/trigger endpoint.
        """
        logger.info("Manual scrape triggered")
        return await self.run_scrape_cycle()

    # ------------------------------------------------------------------
    # Action endpoints (called by dashboard for user interactions)
    # ------------------------------------------------------------------

    async def mark_sold(self, listing_url: str) -> dict[str, Any]:
        """Mark a listing as sold. Called via dashboard action."""
        await self.storage.mark_sold(listing_url)
        return {"success": True, "listing_url": listing_url, "action": "marked_sold"}

    async def mark_contacted(self, listing_url: str) -> dict[str, Any]:
        """Mark a listing as contacted. Called via dashboard action."""
        await self.storage.mark_contacted(listing_url)
        return {"success": True, "listing_url": listing_url, "action": "marked_contacted"}

    async def get_run_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent scrape run history."""
        return await self.storage.get_run_history(limit=limit)
