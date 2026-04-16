import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Crosshair, CaretDown, CaretUp, ArrowSquareOut, CheckCircle,
  ChatCircle, Trash, Upload, MagnifyingGlass, Funnel,
  Lightning, TrendUp, Package, Car, Wrench, Desktop,
  Couch, ArrowsClockwise, X, Fire, WarningCircle,
  Plus, FileArrowUp, DownloadSimple, SortAscending, SortDescending,
  MapPin, Clock, Tag, Eye, CaretRight
} from '@phosphor-icons/react';
import './App.css';

const API = process.env.REACT_APP_BACKEND_URL || '';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const scoreColor = (s) => {
  if (s >= 85) return { text: 'text-emerald-400', bg: 'bg-emerald-400/10', border: 'border-emerald-400/20', dot: 'bg-emerald-400' };
  if (s >= 70) return { text: 'text-emerald-400', bg: 'bg-emerald-400/10', border: 'border-emerald-400/20', dot: 'bg-emerald-400' };
  if (s >= 50) return { text: 'text-amber-400', bg: 'bg-amber-400/10', border: 'border-amber-400/20', dot: 'bg-amber-400' };
  return { text: 'text-rose-500', bg: 'bg-rose-500/10', border: 'border-rose-500/20', dot: 'bg-rose-500' };
};

const scoreLabel = (s) => {
  if (s >= 85) return 'HOT';
  if (s >= 70) return 'GOOD';
  if (s >= 50) return 'FAIR';
  return 'LOW';
};

const fmtPrice = (p) => {
  if (p === 0) return 'FREE';
  if (p == null) return '--';
  return '$' + p.toLocaleString('en-US', { maximumFractionDigits: 0 });
};

const fmtTime = (iso) => {
  if (!iso) return '';
  try {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
  } catch { return ''; }
};

const catIcon = (c) => {
  switch (c) {
    case 'vehicles': return <Car size={14} weight="bold" />;
    case 'equipment': return <Wrench size={14} weight="bold" />;
    case 'electronics': return <Desktop size={14} weight="bold" />;
    case 'furniture': return <Couch size={14} weight="bold" />;
    default: return <Package size={14} weight="bold" />;
  }
};

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------
function ScoreBadge({ score }) {
  const c = scoreColor(score);
  return (
    <div data-testid={`score-badge-${Math.round(score)}`} className={`font-mono text-xl font-bold px-3 py-1 border ${c.border} ${c.bg} ${c.text} backdrop-blur-md rounded-sm flex items-center gap-1.5`}>
      <span className={`w-1.5 h-1.5 rounded-full ${c.dot}`} />
      {Math.round(score)}
    </div>
  );
}

function DealCard({ listing, onMarkSold, onMarkContacted, onDelete, index }) {
  const [expanded, setExpanded] = useState(false);
  const score = listing.score ?? 0;
  const bd = listing.score_breakdown ?? {};
  const c = scoreColor(score);

  return (
    <div
      data-testid={`deal-card-${listing.id}`}
      className="animate-card-in bg-[#18181B]/50 border border-zinc-800 rounded-sm overflow-hidden flex flex-col hover:border-zinc-600 transition-colors duration-200"
      style={{ animationDelay: `${index * 40}ms` }}
    >
      {/* Image + Score */}
      <div className="relative h-48 w-full bg-zinc-950 border-b border-zinc-800">
        {listing.image_url ? (
          <img src={listing.image_url} alt={listing.title} className="w-full h-full object-cover" loading="lazy" onError={(e) => { e.target.style.display = 'none'; }} />
        ) : (
          <div className="w-full h-full flex items-center justify-center text-zinc-700">
            <Package size={48} />
          </div>
        )}
        <div className="absolute top-3 right-3">
          <ScoreBadge score={score} />
        </div>
        <div className={`absolute top-3 left-3 text-[10px] uppercase tracking-[0.2em] font-medium px-2 py-0.5 rounded-sm ${c.bg} ${c.text} border ${c.border}`}>
          {scoreLabel(score)}
        </div>
        {listing.is_contacted && (
          <div className="absolute bottom-3 left-3 text-[10px] uppercase tracking-[0.2em] font-medium px-2 py-0.5 rounded-sm bg-blue-500/10 text-blue-400 border border-blue-500/20">
            CONTACTED
          </div>
        )}
      </div>

      {/* Content */}
      <div className="p-4 flex flex-col flex-1">
        <div className="flex justify-between items-baseline mb-2">
          <span className="font-mono text-2xl font-bold text-zinc-50">{fmtPrice(listing.price)}</span>
          {bd.price_vs_median_pct != null && bd.price_vs_median_pct > 0 && (
            <span className="font-mono text-xs font-medium text-emerald-400">
              -{Math.round(bd.price_vs_median_pct)}% vs median
            </span>
          )}
        </div>

        <h3 className="text-base font-medium text-zinc-300 line-clamp-2 mb-1 font-heading">{listing.title}</h3>

        <div className="flex items-center gap-3 text-[10px] text-zinc-500 font-mono uppercase tracking-[0.15em] mt-auto pt-3">
          {listing.category && (
            <span className="flex items-center gap-1">{catIcon(listing.category)} {listing.category}</span>
          )}
          {listing.location && (
            <span className="flex items-center gap-1"><MapPin size={10} /> {listing.location}</span>
          )}
          {listing.distance != null && (
            <span>{listing.distance.toFixed(0)} MI</span>
          )}
          {listing.posted_at && (
            <span className="flex items-center gap-1"><Clock size={10} /> {fmtTime(listing.posted_at)}</span>
          )}
        </div>

        {/* Urgency chips */}
        {bd.matched_keywords && bd.matched_keywords.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-2">
            {bd.matched_keywords.slice(0, 3).map(kw => (
              <span key={kw} className="text-[10px] px-1.5 py-0.5 rounded-sm bg-amber-400/10 text-amber-400 border border-amber-400/20 font-mono uppercase tracking-wider">
                {kw}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Expand details */}
      {expanded && (
        <div className="px-4 pb-3 border-t border-zinc-800/50 pt-3 space-y-3">
          {listing.description && (
            <p className="text-xs text-zinc-400 leading-relaxed line-clamp-4">{listing.description}</p>
          )}
          {bd.explanation && (
            <p className="text-xs text-blue-400/70 italic flex items-start gap-1">
              <Lightning size={12} className="flex-shrink-0 mt-0.5" />
              {bd.explanation}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2 text-xs">
            {[
              { label: 'PRICE', val: bd.price_score, max: 40 },
              { label: 'URGENCY', val: bd.urgency_score, max: 20 },
              { label: 'RECENCY', val: bd.recency_score, max: 15 },
              { label: 'DISTANCE', val: bd.distance_score, max: 15 },
            ].map(({ label, val, max }) => (
              <div key={label} className="bg-zinc-950/50 border border-zinc-800/50 rounded-sm p-2">
                <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-500 mb-1">{label}</div>
                <div className="flex items-end gap-1">
                  <span className="font-mono font-bold text-zinc-200">{val?.toFixed(0) ?? '--'}</span>
                  <span className="text-zinc-600 font-mono">/{max}</span>
                </div>
                <div className="mt-1 h-1 bg-zinc-800 rounded-full overflow-hidden">
                  <div className={`h-full rounded-full ${val / max > 0.7 ? 'bg-emerald-500' : val / max > 0.4 ? 'bg-amber-500' : 'bg-rose-500'}`} style={{ width: `${Math.min(100, (val || 0) / max * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="grid grid-cols-2 gap-2 mx-4 mb-4 pt-3 border-t border-zinc-800/50">
        <a
          href={listing.listing_url || '#'}
          target="_blank"
          rel="noopener noreferrer"
          data-testid={`view-listing-${listing.id}`}
          className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-sm text-xs font-medium bg-blue-600 hover:bg-blue-500 text-white transition-colors"
        >
          <ArrowSquareOut size={14} /> View Listing
        </a>
        <button
          data-testid={`toggle-details-${listing.id}`}
          onClick={() => setExpanded(!expanded)}
          className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-sm text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-100 transition-colors"
        >
          <Eye size={14} /> {expanded ? 'Less' : 'Details'}
        </button>
      </div>

      {expanded && (
        <div className="flex items-center gap-1 px-4 pb-3">
          {!listing.is_contacted && (
            <button data-testid={`mark-contacted-${listing.id}`} onClick={() => onMarkContacted(listing.id)} className="flex items-center gap-1 px-2 py-1 rounded-sm text-[11px] text-zinc-500 hover:text-blue-400 hover:bg-blue-500/10 transition-colors">
              <ChatCircle size={12} /> Contacted
            </button>
          )}
          <button data-testid={`mark-sold-${listing.id}`} onClick={() => onMarkSold(listing.id)} className="flex items-center gap-1 px-2 py-1 rounded-sm text-[11px] text-zinc-500 hover:text-amber-400 hover:bg-amber-500/10 transition-colors">
            <CheckCircle size={12} /> Sold
          </button>
          <button data-testid={`delete-listing-${listing.id}`} onClick={() => onDelete(listing.id)} className="flex items-center gap-1 px-2 py-1 rounded-sm text-[11px] text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 transition-colors ml-auto">
            <Trash size={12} /> Remove
          </button>
        </div>
      )}
    </div>
  );
}

function StatsPanel({ stats }) {
  if (!stats) return null;
  const widgets = [
    { label: 'ACTIVE DEALS', value: stats.active_listings ?? 0, color: 'text-zinc-50' },
    { label: 'HOT DEALS', value: stats.hot_deals ?? 0, color: 'text-emerald-400' },
    { label: 'AVG SCORE', value: stats.avg_score ?? '--', color: 'text-amber-400' },
    { label: 'AVG PRICE', value: stats.avg_price ? fmtPrice(stats.avg_price) : '--', color: 'text-blue-400' },
  ];

  return (
    <div data-testid="stats-panel" className="grid grid-cols-2 gap-3">
      {widgets.map(w => (
        <div key={w.label} className="bg-[#18181B] border border-zinc-800 p-4 rounded-sm flex flex-col">
          <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-400 font-medium">{w.label}</span>
          <span className={`font-mono text-2xl font-bold mt-1 ${w.color}`}>{w.value}</span>
        </div>
      ))}
    </div>
  );
}

function CategoryBreakdown({ stats }) {
  if (!stats?.category_counts) return null;
  const cats = Object.entries(stats.category_counts).sort((a, b) => b[1] - a[1]);
  if (cats.length === 0) return null;
  const total = cats.reduce((s, [, c]) => s + c, 0);

  return (
    <div data-testid="category-breakdown" className="bg-[#18181B] border border-zinc-800 rounded-sm p-4">
      <h3 className="text-[10px] uppercase tracking-[0.2em] text-zinc-400 font-medium mb-3">BY CATEGORY</h3>
      <div className="space-y-2">
        {cats.map(([cat, count]) => (
          <div key={cat} className="flex items-center gap-2">
            <span className="text-zinc-400 w-5">{catIcon(cat)}</span>
            <span className="text-xs text-zinc-300 capitalize flex-1 font-medium">{cat}</span>
            <span className="font-mono text-xs text-zinc-500">{count}</span>
            <div className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-blue-600 rounded-full" style={{ width: `${(count / total) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ScoreDistribution({ stats }) {
  if (!stats?.score_distribution) return null;
  const d = stats.score_distribution;
  const items = [
    { label: 'HOT (70+)', count: d.hot || 0, color: 'bg-emerald-500' },
    { label: 'GOOD (50-69)', count: d.good || 0, color: 'bg-amber-500' },
    { label: 'FAIR (30-49)', count: d.fair || 0, color: 'bg-orange-500' },
    { label: 'LOW (<30)', count: d.low || 0, color: 'bg-rose-500' },
  ];
  const total = items.reduce((s, i) => s + i.count, 0) || 1;

  return (
    <div data-testid="score-distribution" className="bg-[#18181B] border border-zinc-800 rounded-sm p-4">
      <h3 className="text-[10px] uppercase tracking-[0.2em] text-zinc-400 font-medium mb-3">SCORE DISTRIBUTION</h3>
      <div className="space-y-2">
        {items.map(i => (
          <div key={i.label} className="flex items-center gap-2">
            <span className="text-[10px] text-zinc-400 uppercase tracking-wider w-24">{i.label}</span>
            <div className="flex-1 h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div className={`h-full ${i.color} rounded-full transition-all duration-500`} style={{ width: `${(i.count / total) * 100}%` }} />
            </div>
            <span className="font-mono text-xs text-zinc-500 w-6 text-right">{i.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ImportPanel({ onImportDone }) {
  const [dragging, setDragging] = useState(false);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const handleFile = async (file) => {
    if (!file) return;
    setImporting(true);
    setResult(null);
    const ext = file.name.split('.').pop().toLowerCase();
    const endpoint = ext === 'csv' ? '/api/import/csv' : '/api/import/json';
    const form = new FormData();
    form.append('file', file);
    try {
      const res = await fetch(`${API}${endpoint}`, { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Import failed');
      setResult({ success: true, ...data });
      onImportDone();
    } catch (err) {
      setResult({ success: false, error: err.message });
    } finally {
      setImporting(false);
    }
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    handleFile(file);
  };

  return (
    <div data-testid="import-panel" className="bg-[#18181B] border border-zinc-800 rounded-sm p-4">
      <h3 className="text-[10px] uppercase tracking-[0.2em] text-zinc-400 font-medium mb-3">IMPORT DATA</h3>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
        className={`border border-dashed rounded-sm p-6 text-center cursor-pointer transition-all duration-200 ${dragging ? 'border-blue-500/50 bg-blue-500/5' : 'border-zinc-700 bg-zinc-900/30 hover:border-zinc-500 hover:bg-zinc-900/50'}`}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.json"
          className="hidden"
          data-testid="import-file-input"
          onChange={(e) => handleFile(e.target.files[0])}
        />
        {importing ? (
          <div className="flex flex-col items-center gap-2">
            <ArrowsClockwise size={24} className="text-blue-400 animate-spin" />
            <span className="text-xs text-zinc-400">Processing...</span>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2">
            <FileArrowUp size={24} className="text-zinc-500" />
            <span className="text-xs text-zinc-400">Drop CSV or JSON file</span>
            <span className="text-[10px] text-zinc-600">or click to browse</span>
          </div>
        )}
      </div>
      {result && (
        <div className={`mt-2 text-xs p-2 rounded-sm ${result.success ? 'bg-emerald-400/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
          {result.success ? `Imported ${result.imported} listings (${result.skipped} skipped)` : result.error}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------
export default function App() {
  const [listings, setListings] = useState([]);
  const [stats, setStats] = useState(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [filters, setFilters] = useState({
    search: '',
    category: 'all',
    minScore: 0,
    maxPrice: '',
    sortBy: 'score',
    sortOrder: 'desc',
  });

  const CATEGORIES = [
    { value: 'all', label: 'All', icon: <Package size={14} weight="bold" /> },
    { value: 'vehicles', label: 'Vehicles', icon: <Car size={14} weight="bold" /> },
    { value: 'equipment', label: 'Equipment', icon: <Wrench size={14} weight="bold" /> },
    { value: 'electronics', label: 'Electronics', icon: <Desktop size={14} weight="bold" /> },
    { value: 'furniture', label: 'Furniture', icon: <Couch size={14} weight="bold" /> },
    { value: 'other', label: 'Other', icon: <Tag size={14} weight="bold" /> },
  ];

  const fetchListings = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filters.minScore > 0) params.set('min_score', filters.minScore);
      if (filters.maxPrice) params.set('max_price', filters.maxPrice);
      if (filters.category !== 'all') params.set('category', filters.category);
      if (filters.search) params.set('search', filters.search);
      params.set('sort_by', filters.sortBy);
      params.set('sort_order', filters.sortOrder);
      params.set('limit', '200');

      const res = await fetch(`${API}/api/listings?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setListings(data.listings || []);
      setTotal(data.total || 0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/stats`);
      if (!res.ok) return;
      setStats(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchListings();
    fetchStats();
  }, [fetchListings, fetchStats]);

  const handleMarkSold = async (id) => {
    await fetch(`${API}/api/listings/${id}/mark-sold`, { method: 'POST' });
    setListings(prev => prev.filter(l => l.id !== id));
    fetchStats();
  };

  const handleMarkContacted = async (id) => {
    await fetch(`${API}/api/listings/${id}/mark-contacted`, { method: 'POST' });
    setListings(prev => prev.map(l => l.id === id ? { ...l, is_contacted: true } : l));
  };

  const handleDelete = async (id) => {
    await fetch(`${API}/api/listings/${id}`, { method: 'DELETE' });
    setListings(prev => prev.filter(l => l.id !== id));
    fetchStats();
  };

  const handleImportDone = () => {
    fetchListings();
    fetchStats();
  };

  const updateFilter = (key, val) => {
    setFilters(prev => ({ ...prev, [key]: val }));
  };

  const toggleSort = (field) => {
    setFilters(prev => ({
      ...prev,
      sortBy: field,
      sortOrder: prev.sortBy === field && prev.sortOrder === 'desc' ? 'asc' : 'desc',
    }));
  };

  return (
    <div className="min-h-screen bg-[#09090B] text-zinc-50 font-body">
      {/* Header */}
      <header data-testid="app-header" className="bg-[#09090B]/80 backdrop-blur-xl border-b border-zinc-800 z-50 sticky top-0">
        <div className="max-w-[1600px] mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-sm bg-blue-600 flex items-center justify-center">
              <Crosshair size={18} weight="bold" className="text-white" />
            </div>
            <div>
              <h1 className="font-heading font-bold text-lg tracking-tight text-zinc-50">DEALSCOPE</h1>
              <p className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">DEAL INTELLIGENCE</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500 font-mono">
              {total} LISTINGS
            </span>
            <button
              data-testid="refresh-btn"
              onClick={() => { fetchListings(); fetchStats(); }}
              className="flex items-center gap-1.5 px-3 py-2 rounded-sm text-xs font-medium bg-zinc-800 hover:bg-zinc-700 text-zinc-100 border border-zinc-700 transition-colors"
            >
              <ArrowsClockwise size={14} /> Refresh
            </button>
          </div>
        </div>
      </header>

      {/* Filter bar */}
      <div data-testid="filter-bar" className="bg-[#18181B] border-b border-zinc-800 sticky top-[65px] z-40">
        <div className="max-w-[1600px] mx-auto px-6 py-3 flex flex-wrap items-center gap-4">
          {/* Search */}
          <div className="relative flex-1 min-w-[180px] max-w-[260px]">
            <MagnifyingGlass size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              data-testid="filter-search"
              type="text"
              placeholder="Search listings..."
              value={filters.search}
              onChange={(e) => updateFilter('search', e.target.value)}
              className="w-full pl-8 pr-3 py-2 rounded-sm text-xs bg-zinc-950 border border-zinc-800 text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-blue-500/50 transition-colors"
            />
          </div>

          {/* Category tabs */}
          <div className="flex space-x-1 bg-zinc-950 p-1 rounded-sm border border-zinc-800">
            {CATEGORIES.map(cat => (
              <button
                key={cat.value}
                data-testid={`category-tab-${cat.value}`}
                onClick={() => updateFilter('category', cat.value)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-sm text-[11px] font-medium transition-colors ${filters.category === cat.value ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                {cat.icon} {cat.label}
              </button>
            ))}
          </div>

          {/* Max price */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">MAX $</span>
            <input
              data-testid="filter-max-price"
              type="number"
              min={0}
              step={100}
              placeholder="any"
              value={filters.maxPrice}
              onChange={(e) => updateFilter('maxPrice', e.target.value)}
              className="w-20 px-2 py-2 rounded-sm text-xs bg-zinc-950 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-500/50"
            />
          </div>

          {/* Min score slider */}
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">SCORE</span>
            <input
              data-testid="filter-min-score"
              type="range"
              min={0}
              max={100}
              step={5}
              value={filters.minScore}
              onChange={(e) => updateFilter('minScore', Number(e.target.value))}
              className="w-20"
            />
            <span className="font-mono text-xs text-zinc-300 w-6 text-right">{filters.minScore}</span>
          </div>

          {/* Sort buttons */}
          <div className="flex items-center gap-1 ml-auto">
            {[
              { field: 'score', label: 'Score' },
              { field: 'price', label: 'Price' },
              { field: 'created_at', label: 'Recent' },
            ].map(s => (
              <button
                key={s.field}
                data-testid={`sort-${s.field}`}
                onClick={() => toggleSort(s.field)}
                className={`flex items-center gap-1 px-2 py-1.5 rounded-sm text-[11px] font-medium transition-colors ${filters.sortBy === s.field ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}`}
              >
                {s.label}
                {filters.sortBy === s.field && (
                  filters.sortOrder === 'desc' ? <SortDescending size={12} /> : <SortAscending size={12} />
                )}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="max-w-[1600px] mx-auto px-6 mt-4">
          <div data-testid="error-banner" className="flex items-center gap-3 p-3 rounded-sm bg-rose-500/10 border border-rose-500/20 text-sm text-rose-400">
            <WarningCircle size={16} />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError(null)}><X size={14} /></button>
          </div>
        </div>
      )}

      {/* Main layout */}
      <div className="max-w-[1600px] mx-auto px-6 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Feed area */}
          <div className="lg:col-span-8 space-y-6">
            {loading ? (
              <div className="flex items-center justify-center py-20">
                <div className="flex flex-col items-center gap-3">
                  <ArrowsClockwise size={24} className="text-blue-500 animate-spin" />
                  <span className="text-xs text-zinc-500 font-mono uppercase tracking-wider">Loading deals...</span>
                </div>
              </div>
            ) : listings.length === 0 ? (
              <div data-testid="empty-state" className="flex flex-col items-center justify-center py-20">
                <Crosshair size={48} className="text-zinc-700 mb-4" />
                <h3 className="text-zinc-300 font-heading font-semibold text-lg mb-2">No Deals Found</h3>
                <p className="text-zinc-500 text-sm max-w-md text-center">Import listings via CSV/JSON or adjust your filters to see opportunities.</p>
              </div>
            ) : (
              <div data-testid="listings-grid" className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {listings.map((listing, i) => (
                  <DealCard
                    key={listing.id}
                    listing={listing}
                    index={i}
                    onMarkSold={handleMarkSold}
                    onMarkContacted={handleMarkContacted}
                    onDelete={handleDelete}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Sidebar */}
          <div className="lg:col-span-4 space-y-4">
            <StatsPanel stats={stats} />
            <ScoreDistribution stats={stats} />
            <CategoryBreakdown stats={stats} />
            <ImportPanel onImportDone={handleImportDone} />

            {/* Last import info */}
            {stats?.last_import && (
              <div className="bg-[#18181B] border border-zinc-800 rounded-sm p-4">
                <h3 className="text-[10px] uppercase tracking-[0.2em] text-zinc-400 font-medium mb-2">LAST IMPORT</h3>
                <div className="text-xs text-zinc-400">
                  <span className="text-zinc-300 font-medium">{stats.last_import.count}</span> listings from{' '}
                  <span className="text-zinc-300 font-mono">{stats.last_import.source}</span>
                </div>
                {stats.last_import.created_at && (
                  <div className="text-[10px] text-zinc-600 mt-1 font-mono">{fmtTime(stats.last_import.created_at)}</div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="border-t border-zinc-800/50 mt-8 py-4">
        <div className="max-w-[1600px] mx-auto px-6 flex items-center justify-between text-[10px] text-zinc-600 uppercase tracking-[0.15em]">
          <span>DealScope v1.0</span>
          <span>Deal Intelligence System</span>
        </div>
      </footer>
    </div>
  );
}
