"""
marketplace_scraper — Facebook Marketplace deal scraper module.

Plug into the Operator Dashboard by registering MarketplaceScraperModule
with the module registry:

    from backend.modules.marketplace_scraper import MarketplaceScraperModule

    module = MarketplaceScraperModule(config={
        "search_queries": ["macbook pro", "ps5"],
        "location_city": "austin",
        "location_zip": "78701",
        "price_max": 800,
        "min_resale_score": 55,
        "refresh_interval_minutes": 30,
    })

    # In your FastAPI app startup:
    await module.initialize()

    # Fetch current opportunities:
    payload = await module.fetch()

    # Start background streaming (for SSE):
    async for update in module.stream():
        await broadcast(update.to_dict())
"""

from .config_schema import MarketplaceScraperConfig
from .filters import FilterEngine, apply_standard_filters
from .module import DashboardPayload, MarketplaceScraperModule
from .scorer import ResaleScorer, ScoreBreakdown, score_listings
from .scraper import PlaywrightScraper, RawListing
from .storage import MarketplaceStorage

__all__ = [
    # Module entrypoint
    "MarketplaceScraperModule",
    "DashboardPayload",
    # Config
    "MarketplaceScraperConfig",
    # Components
    "PlaywrightScraper",
    "RawListing",
    "ResaleScorer",
    "ScoreBreakdown",
    "score_listings",
    "FilterEngine",
    "apply_standard_filters",
    "MarketplaceStorage",
]

__version__ = "1.0.0"
__author__ = "Operator Dashboard"
