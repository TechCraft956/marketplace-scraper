"""
FilterEngine — Chainable filter pipeline for marketplace listings.

Usage:
    engine = FilterEngine(listings)
    results = (
        engine
        .by_price(min_price=50, max_price=500)
        .by_keywords(include=["macbook", "laptop"], exclude=["broken", "parts only"])
        .by_distance(max_miles=25)
        .by_category(["electronics"])
        .by_score(min_score=60)
        .results()
    )
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FilterEngine:
    """
    Chainable filter engine for marketplace listing dicts.

    Each filter method returns `self` so calls can be chained.
    Call `.results()` to get the final filtered list.

    Thread-safety: Each chain creates a new internal list copy — safe for
    concurrent use if you create separate FilterEngine instances.
    """

    def __init__(self, listings: list[dict[str, Any]]) -> None:
        self._listings: list[dict[str, Any]] = list(listings)
        self._applied_filters: list[str] = []

    # ------------------------------------------------------------------
    # Chain entry point (class method alternative constructor)
    # ------------------------------------------------------------------

    @classmethod
    def from_list(cls, listings: list[dict[str, Any]]) -> "FilterEngine":
        return cls(listings)

    # ------------------------------------------------------------------
    # Price filters
    # ------------------------------------------------------------------

    def by_price(
        self,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        include_free: bool = True,
        include_unknown_price: bool = True,
    ) -> "FilterEngine":
        """
        Filter listings by price range.

        Args:
            min_price: Minimum acceptable price (inclusive). None = no minimum.
            max_price: Maximum acceptable price (inclusive). None = no maximum.
            include_free: If True, keep listings with price=0 regardless of min_price.
            include_unknown_price: If True, keep listings where price is None.
        """
        before = len(self._listings)
        filtered = []

        for listing in self._listings:
            price = listing.get("price")

            if price is None:
                if include_unknown_price:
                    filtered.append(listing)
                continue

            if price == 0:
                if include_free:
                    filtered.append(listing)
                continue

            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue

            filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        desc = f"by_price(min={min_price}, max={max_price})"
        self._applied_filters.append(f"{desc}: removed {removed}")
        logger.debug("%s → %d remaining", desc, len(self._listings))
        return self

    # ------------------------------------------------------------------
    # Keyword filters
    # ------------------------------------------------------------------

    def by_keywords(
        self,
        include: Optional[list[str]] = None,
        exclude: Optional[list[str]] = None,
        fields: Optional[list[str]] = None,
        case_sensitive: bool = False,
    ) -> "FilterEngine":
        """
        Filter listings by keyword presence/absence in specified text fields.

        Args:
            include: List of keywords; listing must contain AT LEAST ONE (OR logic).
                     Pass None to skip include filtering.
            exclude: List of keywords; listing is dropped if it contains ANY of these.
                     Pass None to skip exclude filtering.
            fields: List of listing dict keys to search. Defaults to
                    ['title', 'description'].
            case_sensitive: If False, matching is case-insensitive.
        """
        if not include and not exclude:
            return self

        search_fields = fields or ["title", "description"]
        before = len(self._listings)
        filtered = []

        for listing in self._listings:
            text_parts = []
            for f in search_fields:
                val = listing.get(f)
                if val:
                    text_parts.append(str(val))
            combined = " ".join(text_parts)

            if not case_sensitive:
                combined = combined.lower()

            # Exclude check (drop if ANY exclude keyword matches)
            excluded = False
            if exclude:
                for kw in exclude:
                    kw_check = kw if case_sensitive else kw.lower()
                    if kw_check in combined:
                        excluded = True
                        break
            if excluded:
                continue

            # Include check (keep if AT LEAST ONE include keyword matches)
            if include:
                matched = False
                for kw in include:
                    kw_check = kw if case_sensitive else kw.lower()
                    if kw_check in combined:
                        matched = True
                        break
                if not matched:
                    continue

            filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        include_str = f"include={include}" if include else ""
        exclude_str = f"exclude={exclude}" if exclude else ""
        desc = f"by_keywords({include_str}{', ' if include and exclude else ''}{exclude_str})"
        self._applied_filters.append(f"{desc}: removed {removed}")
        logger.debug("%s → %d remaining", desc, len(self._listings))
        return self

    def exclude_keywords(self, keywords: list[str]) -> "FilterEngine":
        """Convenience wrapper — exclude listings containing any of these keywords."""
        return self.by_keywords(exclude=keywords)

    def require_keywords(self, keywords: list[str]) -> "FilterEngine":
        """Convenience wrapper — only keep listings containing at least one keyword."""
        return self.by_keywords(include=keywords)

    # ------------------------------------------------------------------
    # Distance filter
    # ------------------------------------------------------------------

    def by_distance(
        self,
        max_miles: float,
        include_unknown_distance: bool = True,
    ) -> "FilterEngine":
        """
        Filter listings by distance from user.

        Args:
            max_miles: Maximum acceptable distance in miles.
            include_unknown_distance: If True, keep listings where distance is None.
        """
        before = len(self._listings)
        filtered = []

        for listing in self._listings:
            distance = listing.get("distance")

            if distance is None:
                if include_unknown_distance:
                    filtered.append(listing)
                continue

            if distance <= max_miles:
                filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        desc = f"by_distance(max={max_miles} mi)"
        self._applied_filters.append(f"{desc}: removed {removed}")
        logger.debug("%s → %d remaining", desc, len(self._listings))
        return self

    # ------------------------------------------------------------------
    # Category filter
    # ------------------------------------------------------------------

    def by_category(
        self,
        categories: list[str],
        match_title: bool = True,
    ) -> "FilterEngine":
        """
        Filter by category label or title keyword match.

        Args:
            categories: List of category slugs or keywords to keep.
                        Case-insensitive matching against listing['category']
                        and optionally listing['title'].
            match_title: If True, also match against title text when category field
                         is missing.
        """
        if not categories:
            return self

        before = len(self._listings)
        filtered = []
        cat_lower = [c.lower() for c in categories]

        for listing in self._listings:
            listing_cat = (listing.get("category") or "").lower()
            title = (listing.get("title") or "").lower()

            matched = False
            for cat in cat_lower:
                if cat in listing_cat:
                    matched = True
                    break
                if match_title and cat in title:
                    matched = True
                    break

            if matched:
                filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        desc = f"by_category({categories})"
        self._applied_filters.append(f"{desc}: removed {removed}")
        logger.debug("%s → %d remaining", desc, len(self._listings))
        return self

    # ------------------------------------------------------------------
    # Score filter (applied after scoring)
    # ------------------------------------------------------------------

    def by_score(
        self,
        min_score: float,
        score_field: str = "score",
    ) -> "FilterEngine":
        """
        Filter listings by minimum deal score.

        Args:
            min_score: Minimum score to keep (0-100).
            score_field: Dict key containing the score value.
        """
        before = len(self._listings)
        filtered = []

        for listing in self._listings:
            score = listing.get(score_field)

            if score is None:
                # No score attached yet — pass through
                filtered.append(listing)
                continue

            if score >= min_score:
                filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        desc = f"by_score(min={min_score})"
        self._applied_filters.append(f"{desc}: removed {removed}")
        logger.debug("%s → %d remaining", desc, len(self._listings))
        return self

    # ------------------------------------------------------------------
    # Image filter
    # ------------------------------------------------------------------

    def require_images(self, min_images: int = 1) -> "FilterEngine":
        """Keep only listings with at least `min_images` images."""
        before = len(self._listings)
        self._listings = [
            l for l in self._listings
            if (l.get("image_count") or 0) >= min_images
        ]
        removed = before - len(self._listings)
        self._applied_filters.append(
            f"require_images(min={min_images}): removed {removed}"
        )
        return self

    # ------------------------------------------------------------------
    # Price sanity filter
    # ------------------------------------------------------------------

    def exclude_suspicious_prices(self) -> "FilterEngine":
        """
        Remove listings with price patterns that are commonly spam/scams:
        - Price of exactly 1 (often a placeholder)
        - Price > $50,000 (likely error or vehicle)
        """
        before = len(self._listings)
        filtered = []

        for listing in self._listings:
            price = listing.get("price")
            if price is not None:
                if price == 1.0:
                    continue
                if price > 50000:
                    continue
            filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        self._applied_filters.append(
            f"exclude_suspicious_prices: removed {removed}"
        )
        return self

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def deduplicate(self, key: str = "listing_url") -> "FilterEngine":
        """
        Remove duplicate listings by the specified key.
        Keeps the first occurrence (highest score if pre-sorted).
        """
        before = len(self._listings)
        seen: set[str] = set()
        filtered = []

        for listing in self._listings:
            val = listing.get(key)
            if val and val in seen:
                continue
            if val:
                seen.add(val)
            filtered.append(listing)

        self._listings = filtered
        removed = before - len(filtered)
        self._applied_filters.append(f"deduplicate({key}): removed {removed}")
        return self

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def sort_by_score(self, descending: bool = True) -> "FilterEngine":
        """Sort listings by score."""
        self._listings.sort(
            key=lambda x: x.get("score") or 0,
            reverse=descending,
        )
        return self

    def sort_by_price(self, descending: bool = False) -> "FilterEngine":
        """Sort listings by price (ascending by default = cheapest first)."""
        self._listings.sort(
            key=lambda x: x.get("price") if x.get("price") is not None else float("inf"),
            reverse=descending,
        )
        return self

    def sort_by_posted_at(self, newest_first: bool = True) -> "FilterEngine":
        """Sort listings by posted_at date."""
        self._listings.sort(
            key=lambda x: x.get("posted_at") or "",
            reverse=newest_first,
        )
        return self

    # ------------------------------------------------------------------
    # Limit
    # ------------------------------------------------------------------

    def limit(self, n: int) -> "FilterEngine":
        """Keep only the first n listings."""
        self._listings = self._listings[:n]
        return self

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def results(self) -> list[dict[str, Any]]:
        """Return the filtered list of listing dicts."""
        return list(self._listings)

    def count(self) -> int:
        """Return the count of listings currently passing all filters."""
        return len(self._listings)

    def summary(self) -> dict[str, Any]:
        """Return a summary of applied filters and remaining count."""
        return {
            "remaining": len(self._listings),
            "filters_applied": self._applied_filters,
        }

    def __len__(self) -> int:
        return len(self._listings)

    def __repr__(self) -> str:
        return (
            f"FilterEngine({len(self._listings)} listings, "
            f"{len(self._applied_filters)} filters applied)"
        )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def apply_standard_filters(
    listings: list[dict[str, Any]],
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    max_distance: Optional[float] = None,
    categories: Optional[list[str]] = None,
    include_keywords: Optional[list[str]] = None,
    exclude_keywords: Optional[list[str]] = None,
    min_score: Optional[float] = None,
    require_images: bool = False,
    deduplicate: bool = True,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Apply the standard filter pipeline in the recommended order and return results.
    This is the primary entry point used by module.py.
    """
    engine = FilterEngine(listings)

    if deduplicate:
        engine.deduplicate()

    engine.exclude_suspicious_prices()

    if min_price is not None or max_price is not None:
        engine.by_price(min_price=min_price, max_price=max_price)

    if max_distance is not None:
        engine.by_distance(max_miles=max_distance)

    if categories:
        engine.by_category(categories)

    if include_keywords or exclude_keywords:
        engine.by_keywords(
            include=include_keywords,
            exclude=exclude_keywords,
        )

    if require_images:
        engine.require_images(min_images=1)

    # Sort by score descending before applying score filter
    engine.sort_by_score(descending=True)

    if min_score is not None:
        engine.by_score(min_score=min_score)

    if limit is not None:
        engine.limit(limit)

    summary = engine.summary()
    logger.info(
        "Filter pipeline complete: %d listings remaining. Filters: %s",
        summary["remaining"],
        summary["filters_applied"],
    )

    return engine.results()
