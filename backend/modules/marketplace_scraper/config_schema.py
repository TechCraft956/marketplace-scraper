"""
Pydantic config schema for the Marketplace Scraper module.

This schema is used by:
  1. The dashboard's module registry for config validation on load
  2. The module's __init__ to parse operator-provided config
  3. API endpoints that accept live config updates

Example config JSON:
{
    "search_queries": ["macbook pro", "ps5", "mechanical keyboard"],
    "location_zip": "78701",
    "location_city": "austin",
    "radius_miles": 40,
    "price_min": 20,
    "price_max": 800,
    "categories": ["electronics"],
    "excluded_keywords": ["broken", "parts only", "for parts", "cracked screen"],
    "included_keywords": [],
    "min_resale_score": 55,
    "refresh_interval_minutes": 30,
    "max_pages_per_query": 3,
    "cookies_path": "data/fb_cookies.json",
    "headless": true,
    "proxy_server": null,
    "proxy_username": null,
    "proxy_password": null,
    "detail_scrape": true,
    "max_listings_stored": 500,
    "db_path": "data/marketplace.db",
    "require_images": false,
    "inter_query_delay_min": 8.0,
    "inter_query_delay_max": 15.0
}
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MarketplaceScraperConfig(BaseModel):
    """Full configuration model for the Marketplace Scraper module."""

    # ---- Search parameters ----
    search_queries: list[str] = Field(
        default=["electronics", "furniture"],
        description=(
            "List of search queries to run on Facebook Marketplace. "
            "Each query is run separately and results are deduplicated."
        ),
        min_length=1,
    )

    location_zip: Optional[str] = Field(
        default=None,
        description=(
            "ZIP code of the user's location. Used for distance calculations "
            "and can be passed directly to the Marketplace URL."
        ),
        pattern=r"^\d{5}(-\d{4})?$",
    )

    location_city: str = Field(
        default="austin",
        description=(
            "City slug used in the Facebook Marketplace URL "
            "(e.g. 'austin', 'new-york', 'los-angeles'). "
            "Lowercase, hyphen-separated for multi-word cities."
        ),
    )

    radius_miles: int = Field(
        default=40,
        ge=1,
        le=500,
        description="Search radius in miles from the specified location.",
    )

    # ---- Price filters ----
    price_min: Optional[float] = Field(
        default=None,
        ge=0,
        description="Minimum listing price. None = no minimum.",
    )

    price_max: Optional[float] = Field(
        default=None,
        ge=0,
        description="Maximum listing price. None = no maximum.",
    )

    # ---- Category filters ----
    categories: list[str] = Field(
        default=[],
        description=(
            "Category slugs to restrict results to. "
            "Empty list = all categories. "
            "Valid values: electronics, furniture, clothing, tools, "
            "sporting_goods, musical_instruments, vehicles, all"
        ),
    )

    # ---- Keyword filters ----
    excluded_keywords: list[str] = Field(
        default=["broken", "parts only", "for parts", "cracked", "damaged"],
        description=(
            "Listings containing any of these keywords (case-insensitive) "
            "in title or description will be excluded."
        ),
    )

    included_keywords: list[str] = Field(
        default=[],
        description=(
            "If non-empty, only listings containing at least one of these "
            "keywords will be kept. Empty list = include all."
        ),
    )

    # ---- Scoring ----
    min_resale_score: float = Field(
        default=50.0,
        ge=0,
        le=100,
        description=(
            "Minimum deal score (0-100) for a listing to appear in the dashboard. "
            "Higher = more selective. Recommended: 50-70."
        ),
    )

    # ---- Scraper behavior ----
    refresh_interval_minutes: int = Field(
        default=30,
        ge=5,
        le=1440,
        description=(
            "How often to re-run the scrape. "
            "Minimum 5 minutes to avoid rate limiting."
        ),
    )

    max_pages_per_query: int = Field(
        default=3,
        ge=1,
        le=20,
        description=(
            "Number of scroll pages to load per search query. "
            "Each page yields approximately 20 listings."
        ),
    )

    detail_scrape: bool = Field(
        default=True,
        description=(
            "If True, visit each individual listing page for full data "
            "(description, image count, posted date). Slower but more accurate. "
            "If False, extract summary data from the search feed only."
        ),
    )

    inter_query_delay_min: float = Field(
        default=8.0,
        ge=3.0,
        description="Minimum delay in seconds between search queries.",
    )

    inter_query_delay_max: float = Field(
        default=15.0,
        ge=5.0,
        description="Maximum delay in seconds between search queries.",
    )

    # ---- Auth / session ----
    cookies_path: str = Field(
        default="data/fb_cookies.json",
        description=(
            "Path to Facebook session cookies JSON file. "
            "Export from browser after manual login. "
            "Relative to the module's working directory."
        ),
    )

    headless: bool = Field(
        default=True,
        description=(
            "Run Playwright in headless mode. Set to False for debugging "
            "or initial cookie setup."
        ),
    )

    # ---- Proxy (optional) ----
    proxy_server: Optional[str] = Field(
        default=None,
        description=(
            "Proxy server URL (e.g. 'http://proxy.example.com:8080'). "
            "None = direct connection."
        ),
    )

    proxy_username: Optional[str] = Field(
        default=None,
        description="Proxy authentication username.",
    )

    proxy_password: Optional[str] = Field(
        default=None,
        description="Proxy authentication password.",
    )

    # ---- Storage ----
    db_path: str = Field(
        default="data/marketplace.db",
        description="Path to the SQLite database file.",
    )

    max_listings_stored: int = Field(
        default=1000,
        ge=10,
        le=100000,
        description=(
            "Maximum number of listings to keep in storage. "
            "When exceeded, oldest low-score listings are pruned."
        ),
    )

    # ---- Display ----
    require_images: bool = Field(
        default=False,
        description="If True, exclude listings with no images.",
    )

    # ---- Validators ----

    @field_validator("location_city")
    @classmethod
    def normalize_city(cls, v: str) -> str:
        """Lowercase and replace spaces with hyphens for URL compatibility."""
        return v.strip().lower().replace(" ", "-")

    @field_validator("search_queries")
    @classmethod
    def validate_queries(cls, v: list[str]) -> list[str]:
        """Strip whitespace and remove empty strings."""
        cleaned = [q.strip() for q in v if q.strip()]
        if not cleaned:
            raise ValueError("search_queries must contain at least one non-empty query")
        return cleaned

    @model_validator(mode="after")
    def validate_price_range(self) -> "MarketplaceScraperConfig":
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min cannot be greater than price_max")
        return self

    @model_validator(mode="after")
    def validate_delay_range(self) -> "MarketplaceScraperConfig":
        if self.inter_query_delay_min > self.inter_query_delay_max:
            raise ValueError(
                "inter_query_delay_min cannot be greater than inter_query_delay_max"
            )
        return self

    @property
    def proxy_config(self) -> Optional[dict[str, str]]:
        """Return Playwright-compatible proxy dict or None."""
        if not self.proxy_server:
            return None
        config: dict[str, str] = {"server": self.proxy_server}
        if self.proxy_username:
            config["username"] = self.proxy_username
        if self.proxy_password:
            config["password"] = self.proxy_password
        return config

    @property
    def inter_query_delay(self) -> tuple[float, float]:
        """Return (min, max) delay tuple for use with random.uniform."""
        return (self.inter_query_delay_min, self.inter_query_delay_max)

    class Config:
        json_schema_extra = {
            "example": {
                "search_queries": ["macbook pro", "ps5", "mechanical keyboard"],
                "location_zip": "78701",
                "location_city": "austin",
                "radius_miles": 40,
                "price_min": 20,
                "price_max": 800,
                "categories": ["electronics"],
                "excluded_keywords": [
                    "broken",
                    "parts only",
                    "for parts",
                    "cracked screen",
                ],
                "included_keywords": [],
                "min_resale_score": 55,
                "refresh_interval_minutes": 30,
                "max_pages_per_query": 3,
                "cookies_path": "data/fb_cookies.json",
                "headless": True,
                "proxy_server": None,
                "db_path": "data/marketplace.db",
                "max_listings_stored": 500,
                "require_images": False,
                "detail_scrape": True,
                "inter_query_delay_min": 8.0,
                "inter_query_delay_max": 15.0,
            }
        }
