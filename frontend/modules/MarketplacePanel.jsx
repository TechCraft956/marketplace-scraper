/**
 * MarketplacePanel.jsx
 * Operator Dashboard module panel for the Facebook Marketplace deal scraper.
 *
 * Features:
 *  - Sorted opportunity cards with score badges
 *  - Filter bar: price range, min score, category, keyword search
 *  - SSE-based auto-refresh (falls back to polling)
 *  - "Mark Sold" / "Mark Contacted" actions
 *  - Dark operator aesthetic matching the existing dashboard theme
 */

import { useState, useEffect, useCallback, useRef } from "react";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE = "/api/modules/marketplace_scraper";

const CATEGORIES = [
  { value: "", label: "All Categories" },
  { value: "electronics", label: "Electronics" },
  { value: "furniture", label: "Furniture" },
  { value: "tools", label: "Tools" },
  { value: "sporting_goods", label: "Sporting Goods" },
  { value: "musical_instruments", label: "Musical Instruments" },
  { value: "vehicles", label: "Vehicles" },
  { value: "clothing", label: "Clothing" },
];

const SCORE_COLOR = (score) => {
  if (score >= 80) return { bg: "bg-emerald-500", text: "text-emerald-400", ring: "ring-emerald-500/30" };
  if (score >= 65) return { bg: "bg-blue-500", text: "text-blue-400", ring: "ring-blue-500/30" };
  if (score >= 50) return { bg: "bg-amber-500", text: "text-amber-400", ring: "ring-amber-500/30" };
  return { bg: "bg-zinc-500", text: "text-zinc-400", ring: "ring-zinc-500/30" };
};

const SCORE_LABEL = (score) => {
  if (score >= 80) return "🔥 Hot Deal";
  if (score >= 65) return "✓ Good Deal";
  if (score >= 50) return "~ Fair";
  return "Low";
};

const formatPrice = (price, priceRaw) => {
  if (price === 0) return "FREE";
  if (price == null) return priceRaw || "—";
  return `$${price.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
};

const formatTime = (isoStr) => {
  if (!isoStr) return "";
  try {
    const d = new Date(isoStr);
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  } catch {
    return "";
  }
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ScoreBadge({ score }) {
  const colors = SCORE_COLOR(score);
  return (
    <span
      className={`
        inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-bold
        ring-1 ${colors.ring} bg-black/40 ${colors.text}
      `}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full ${colors.bg}`}
      />
      {Math.round(score)}
    </span>
  );
}

function UrgencyChips({ keywords }) {
  if (!keywords || keywords.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {keywords.slice(0, 3).map((kw) => (
        <span
          key={kw}
          className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-300 border border-amber-700/30"
        >
          {kw}
        </span>
      ))}
    </div>
  );
}

function ListingCard({ listing, onMarkSold, onMarkContacted }) {
  const [expanded, setExpanded] = useState(false);
  const [acting, setActing] = useState(false);

  const score = listing.score ?? 0;
  const breakdown = listing.score_breakdown ?? {};
  const colors = SCORE_COLOR(score);

  const handleAction = async (action) => {
    setActing(true);
    try {
      await action();
    } finally {
      setActing(false);
    }
  };

  return (
    <div
      className={`
        group relative flex flex-col rounded-xl overflow-hidden
        bg-zinc-900 border border-zinc-800
        hover:border-zinc-600 transition-all duration-200
        ${listing.is_contacted ? "opacity-60" : ""}
      `}
    >
      {/* Score glow accent line */}
      <div className={`h-0.5 w-full ${colors.bg} opacity-70`} />

      <div className="flex gap-3 p-3">
        {/* Thumbnail */}
        <div className="flex-shrink-0 w-20 h-20 rounded-lg overflow-hidden bg-zinc-800 border border-zinc-700">
          {listing.image_url ? (
            <img
              src={listing.image_url}
              alt={listing.title}
              className="w-full h-full object-cover"
              loading="lazy"
              onError={(e) => {
                e.target.style.display = "none";
              }}
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-zinc-600 text-2xl">
              📷
            </div>
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-semibold text-zinc-100 leading-tight line-clamp-2">
              {listing.title}
            </h3>
            <ScoreBadge score={score} />
          </div>

          <div className="flex items-center gap-2 mt-1.5">
            <span className="text-base font-bold text-white">
              {formatPrice(listing.price, listing.price_raw)}
            </span>
            {breakdown.category_median && listing.price && (
              <span className="text-xs text-zinc-500">
                / ${Math.round(breakdown.category_median)} median
              </span>
            )}
            {breakdown.price_vs_median_pct != null && breakdown.price_vs_median_pct > 0 && (
              <span className="text-xs font-medium text-emerald-400">
                -{Math.round(breakdown.price_vs_median_pct)}%
              </span>
            )}
          </div>

          <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500">
            {listing.location && (
              <span className="flex items-center gap-1">
                📍 {listing.location}
              </span>
            )}
            {listing.distance != null && (
              <span>{listing.distance.toFixed(1)} mi</span>
            )}
            {listing.posted_at && (
              <span>{formatTime(listing.posted_at)}</span>
            )}
          </div>

          <UrgencyChips keywords={breakdown.matched_keywords} />
        </div>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-zinc-800 mt-1 pt-2">
          {listing.description && (
            <p className="text-xs text-zinc-400 leading-relaxed line-clamp-4 mb-2">
              {listing.description}
            </p>
          )}
          {breakdown.explanation && (
            <p className="text-xs text-blue-400/80 italic">
              💡 {breakdown.explanation}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
            <div className="bg-zinc-800/50 rounded p-1.5">
              <span className="text-zinc-500">Price score</span>
              <span className="float-right text-zinc-300">
                {breakdown.price_score?.toFixed(0) ?? "—"}/40
              </span>
            </div>
            <div className="bg-zinc-800/50 rounded p-1.5">
              <span className="text-zinc-500">Urgency</span>
              <span className="float-right text-zinc-300">
                {breakdown.urgency_score?.toFixed(0) ?? "—"}/20
              </span>
            </div>
            <div className="bg-zinc-800/50 rounded p-1.5">
              <span className="text-zinc-500">Recency</span>
              <span className="float-right text-zinc-300">
                {breakdown.recency_score?.toFixed(0) ?? "—"}/15
              </span>
            </div>
            <div className="bg-zinc-800/50 rounded p-1.5">
              <span className="text-zinc-500">Distance</span>
              <span className="float-right text-zinc-300">
                {breakdown.distance_score?.toFixed(0) ?? "—"}/15
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Actions row */}
      <div className="flex items-center justify-between px-3 py-2 border-t border-zinc-800/60 bg-zinc-900/60">
        <div className="flex items-center gap-2">
          <a
            href={listing.listing_url}
            target="_blank"
            rel="noopener noreferrer"
            className="
              inline-flex items-center gap-1 px-2.5 py-1 rounded-lg
              text-xs font-medium text-blue-400
              bg-blue-500/10 border border-blue-500/20
              hover:bg-blue-500/20 hover:border-blue-500/40
              transition-colors duration-150
            "
          >
            View on FB ↗
          </a>
          <button
            onClick={() => setExpanded((e) => !e)}
            className="
              inline-flex items-center gap-1 px-2 py-1 rounded-lg
              text-xs text-zinc-500
              hover:text-zinc-300 hover:bg-zinc-800
              transition-colors duration-150
            "
          >
            {expanded ? "Less ▲" : "Details ▼"}
          </button>
        </div>

        <div className="flex items-center gap-1.5">
          {!listing.is_contacted && (
            <button
              disabled={acting}
              onClick={() => handleAction(() => onMarkContacted(listing.listing_url))}
              className="
                px-2 py-1 rounded-lg text-[11px] text-zinc-500
                hover:text-zinc-300 hover:bg-zinc-800
                disabled:opacity-40 transition-colors
              "
              title="Mark as contacted"
            >
              💬
            </button>
          )}
          {!listing.is_sold && (
            <button
              disabled={acting}
              onClick={() => handleAction(() => onMarkSold(listing.listing_url))}
              className="
                px-2 py-1 rounded-lg text-[11px] text-zinc-500
                hover:text-red-400 hover:bg-red-900/20
                disabled:opacity-40 transition-colors
              "
              title="Mark as sold / remove"
            >
              ✗
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function StatsBar({ stats, meta, isScraping, onTriggerScrape }) {
  return (
    <div className="flex items-center gap-4 px-4 py-2.5 bg-zinc-900/80 border-b border-zinc-800 text-xs">
      <div className="flex items-center gap-1.5">
        <span className={`w-2 h-2 rounded-full ${isScraping ? "bg-amber-400 animate-pulse" : "bg-emerald-500"}`} />
        <span className="text-zinc-400">
          {isScraping ? "Scanning..." : "Idle"}
        </span>
      </div>

      {stats && (
        <>
          <span className="text-zinc-600">|</span>
          <span className="text-zinc-400">
            <span className="text-white font-medium">{stats.active_listings ?? 0}</span> active
          </span>
          <span className="text-zinc-400">
            <span className="text-emerald-400 font-medium">{stats.high_score_count ?? 0}</span> hot deals
          </span>
          {stats.avg_score && (
            <span className="text-zinc-400">
              avg score <span className="text-zinc-300 font-medium">{stats.avg_score}</span>
            </span>
          )}
          {stats.last_run_at && (
            <span className="text-zinc-500 ml-auto">
              Last scan: {formatTime(stats.last_run_at)}
            </span>
          )}
        </>
      )}

      <button
        onClick={onTriggerScrape}
        disabled={isScraping}
        className="
          ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
          bg-zinc-800 text-zinc-300 border border-zinc-700
          hover:bg-zinc-700 hover:text-white
          disabled:opacity-50 disabled:cursor-not-allowed
          transition-all duration-150
        "
      >
        {isScraping ? (
          <>
            <span className="animate-spin">⟳</span> Scanning
          </>
        ) : (
          <>⟳ Scan Now</>
        )}
      </button>
    </div>
  );
}

function FilterBar({ filters, onFiltersChange, totalCount, filteredCount }) {
  return (
    <div className="flex flex-wrap items-center gap-3 px-4 py-3 border-b border-zinc-800 bg-zinc-950/50">
      {/* Keyword search */}
      <input
        type="text"
        placeholder="Search title..."
        value={filters.keyword}
        onChange={(e) => onFiltersChange({ ...filters, keyword: e.target.value })}
        className="
          flex-1 min-w-[140px] max-w-[200px] px-3 py-1.5 rounded-lg text-xs
          bg-zinc-800 border border-zinc-700 text-zinc-200 placeholder-zinc-600
          focus:outline-none focus:border-zinc-500 focus:ring-1 focus:ring-zinc-500/30
        "
      />

      {/* Category */}
      <select
        value={filters.category}
        onChange={(e) => onFiltersChange({ ...filters, category: e.target.value })}
        className="
          px-3 py-1.5 rounded-lg text-xs
          bg-zinc-800 border border-zinc-700 text-zinc-300
          focus:outline-none focus:border-zinc-500
        "
      >
        {CATEGORIES.map((c) => (
          <option key={c.value} value={c.value}>{c.label}</option>
        ))}
      </select>

      {/* Max price */}
      <div className="flex items-center gap-1.5 text-xs text-zinc-500">
        <span>Max $</span>
        <input
          type="number"
          min={0}
          max={10000}
          step={50}
          placeholder="any"
          value={filters.maxPrice}
          onChange={(e) => onFiltersChange({ ...filters, maxPrice: e.target.value })}
          className="
            w-20 px-2 py-1.5 rounded-lg
            bg-zinc-800 border border-zinc-700 text-zinc-200 text-xs
            focus:outline-none focus:border-zinc-500
          "
        />
      </div>

      {/* Min score */}
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        <span>Score ≥</span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={filters.minScore}
          onChange={(e) => onFiltersChange({ ...filters, minScore: Number(e.target.value) })}
          className="w-20 accent-blue-500"
        />
        <span className="text-zinc-300 w-6 text-right">{filters.minScore}</span>
      </div>

      {/* Count */}
      <span className="ml-auto text-xs text-zinc-500">
        {filteredCount}
        {filteredCount !== totalCount && (
          <span className="text-zinc-600"> / {totalCount}</span>
        )}
        {" "}listings
      </span>
    </div>
  );
}

function EmptyState({ isScraping, hasCookies }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center px-8">
      <div className="text-4xl mb-4">{isScraping ? "🔍" : "🛒"}</div>
      <h3 className="text-zinc-300 font-semibold mb-2">
        {isScraping ? "Scanning Marketplace..." : "No Deals Found Yet"}
      </h3>
      <p className="text-zinc-500 text-sm max-w-sm">
        {isScraping
          ? "Browsing Facebook Marketplace for deals matching your criteria. This may take a few minutes."
          : hasCookies === false
            ? "No Facebook session found. Add your cookies.json file to authenticate, then click 'Scan Now'."
            : "Click 'Scan Now' to start scanning, or wait for the next scheduled refresh."}
      </p>
    </div>
  );
}

function ErrorBanner({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div className="flex items-start gap-3 mx-4 mt-3 p-3 rounded-lg bg-red-900/20 border border-red-800/40 text-sm text-red-300">
      <span className="text-red-400 mt-0.5">⚠</span>
      <span className="flex-1">{error}</span>
      <button
        onClick={onDismiss}
        className="text-red-500 hover:text-red-300 ml-2 text-xs flex-shrink-0"
      >
        ✕
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel component
// ---------------------------------------------------------------------------

export default function MarketplacePanel({ moduleConfig = {} }) {
  const [listings, setListings] = useState([]);
  const [meta, setMeta] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isScraping, setIsScraping] = useState(false);

  const [filters, setFilters] = useState({
    keyword: "",
    category: "",
    minScore: 0,
    maxPrice: "",
  });

  const sseRef = useRef(null);
  const refreshInterval = moduleConfig.refresh_interval_minutes ?? 30;

  // ------------------------------------------------------------------
  // Data fetching
  // ------------------------------------------------------------------

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/fetch`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const payload = await res.json();

      if (payload.error) {
        setError(payload.error);
      } else {
        setListings(payload.data ?? []);
        setMeta(payload.meta ?? {});
        setIsScraping(payload.meta?.is_scraping ?? false);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // SSE streaming connection
  const connectSSE = useCallback(() => {
    if (sseRef.current) {
      sseRef.current.close();
    }
    try {
      const sse = new EventSource(`${API_BASE}/stream`);

      sse.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.data) setListings(payload.data);
          if (payload.meta) {
            setMeta(payload.meta);
            setIsScraping(payload.meta.is_scraping ?? false);
          }
          if (payload.error) setError(payload.error);
        } catch (parseErr) {
          console.warn("SSE parse error:", parseErr);
        }
      };

      sse.onerror = () => {
        // SSE connection error — fall back to polling
        sse.close();
        sseRef.current = null;
      };

      sseRef.current = sse;
    } catch {
      // SSE not supported or blocked — polling fallback is active
    }
  }, []);

  // Polling fallback
  useEffect(() => {
    fetchData();
    connectSSE();

    const pollId = setInterval(fetchData, refreshInterval * 60 * 1000);

    return () => {
      clearInterval(pollId);
      if (sseRef.current) {
        sseRef.current.close();
      }
    };
  }, [fetchData, connectSSE, refreshInterval]);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const handleTriggerScrape = async () => {
    setIsScraping(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/trigger`, { method: "POST" });
      const data = await res.json();
      if (data.error) {
        setError(data.error);
        setIsScraping(false);
      } else {
        // Refresh data after trigger (scrape is async on backend)
        setTimeout(fetchData, 3000);
        setTimeout(fetchData, 15000);
        setTimeout(() => {
          fetchData();
          setIsScraping(false);
        }, 45000);
      }
    } catch (err) {
      setError(err.message);
      setIsScraping(false);
    }
  };

  const handleMarkSold = async (listingUrl) => {
    try {
      await fetch(`${API_BASE}/action/mark_sold`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listing_url: listingUrl }),
      });
      setListings((prev) => prev.filter((l) => l.listing_url !== listingUrl));
    } catch (err) {
      setError(err.message);
    }
  };

  const handleMarkContacted = async (listingUrl) => {
    try {
      await fetch(`${API_BASE}/action/mark_contacted`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ listing_url: listingUrl }),
      });
      setListings((prev) =>
        prev.map((l) =>
          l.listing_url === listingUrl ? { ...l, is_contacted: 1 } : l
        )
      );
    } catch (err) {
      setError(err.message);
    }
  };

  // ------------------------------------------------------------------
  // Client-side filtering
  // ------------------------------------------------------------------

  const filteredListings = listings.filter((listing) => {
    if (filters.keyword) {
      const kw = filters.keyword.toLowerCase();
      const inTitle = (listing.title ?? "").toLowerCase().includes(kw);
      const inDesc = (listing.description ?? "").toLowerCase().includes(kw);
      if (!inTitle && !inDesc) return false;
    }
    if (filters.category) {
      const cat = (listing.category ?? "").toLowerCase();
      const title = (listing.title ?? "").toLowerCase();
      if (!cat.includes(filters.category) && !title.includes(filters.category)) {
        return false;
      }
    }
    if (filters.maxPrice) {
      const max = Number(filters.maxPrice);
      if (listing.price != null && listing.price > max) return false;
    }
    if (filters.minScore > 0) {
      if ((listing.score ?? 0) < filters.minScore) return false;
    }
    return true;
  });

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  const stats = meta?.stats ?? null;
  const hasCookies = meta?.cookies_present;

  return (
    <div className="flex flex-col h-full bg-zinc-950 text-zinc-100 font-sans">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800 bg-zinc-900">
        <div className="flex items-center gap-2">
          <span className="text-lg">🛒</span>
          <div>
            <h2 className="text-sm font-bold text-zinc-100 leading-tight">
              Marketplace Scanner
            </h2>
            <p className="text-[11px] text-zinc-500">
              Facebook Marketplace deal finder
            </p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2 text-xs text-zinc-500">
          {meta?.config?.location && (
            <span className="flex items-center gap-1 px-2 py-1 rounded bg-zinc-800 border border-zinc-700">
              📍 {meta.config.location}
              {meta.config.radius_miles && ` +${meta.config.radius_miles}mi`}
            </span>
          )}
        </div>
      </div>

      {/* Stats bar */}
      <StatsBar
        stats={stats}
        meta={meta}
        isScraping={isScraping}
        onTriggerScrape={handleTriggerScrape}
      />

      {/* Error banner */}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* Filter bar */}
      {listings.length > 0 && (
        <FilterBar
          filters={filters}
          onFiltersChange={setFilters}
          totalCount={listings.length}
          filteredCount={filteredListings.length}
        />
      )}

      {/* Listings grid */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="flex flex-col items-center gap-3">
              <div className="w-6 h-6 border-2 border-zinc-600 border-t-blue-500 rounded-full animate-spin" />
              <span className="text-xs text-zinc-500">Loading opportunities...</span>
            </div>
          </div>
        ) : filteredListings.length === 0 ? (
          <EmptyState isScraping={isScraping} hasCookies={hasCookies} />
        ) : (
          <div className="p-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {filteredListings.map((listing) => (
              <ListingCard
                key={listing.listing_url}
                listing={listing}
                onMarkSold={handleMarkSold}
                onMarkContacted={handleMarkContacted}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-4 py-2 border-t border-zinc-800/60 bg-zinc-950 text-[10px] text-zinc-600">
        <span>
          Refreshes every {refreshInterval}m
          {meta?.last_scrape_at && ` · Last: ${formatTime(meta.last_scrape_at)}`}
        </span>
        <span className="text-zinc-700">
          ⚠ Personal use only · Respect FB ToS
        </span>
      </div>
    </div>
  );
}
