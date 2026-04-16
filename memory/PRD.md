# DealScope — Marketplace Deal Intelligence System

## Original Problem Statement
Turn the TechCraft956/marketplace-scraper GitHub repo into a production-ready local-first deal intelligence system that ingests marketplace listings, scores them 0-100, and presents actionable flipping opportunities via a clean dashboard.

## Architecture
- **Backend**: FastAPI (Python 3.11) + MongoDB
- **Frontend**: React 18 + Tailwind CSS
- **Scoring Engine**: Adapted from repo's `scorer.py` with category-aware extensions
- **Ingestion**: CSV/JSON file import + manual entry (scraper preserved but optional)

## User Persona
- Single-user local tool operator
- Marketplace flipper/reseller
- Deals in vehicles, equipment, electronics, furniture

## Core Requirements (Static)
1. Multi-source ingestion pipeline (CSV, JSON import) — DONE
2. Deal scoring 0-100 with explainable breakdown — DONE
3. Category-aware scoring (vehicles, equipment, electronics, furniture) — DONE
4. Dashboard with filter/sort/search — DONE
5. Mark sold/contacted/delete actions — DONE
6. Stats overview with category & score distribution — DONE
7. No auth needed — single-user — DONE
8. MongoDB storage with deduplication — DONE

## What's Been Implemented (Jan 2026)
- Full FastAPI backend with 12 API endpoints
- MongoDB storage with dedup, scoring, import tracking
- Scoring engine from repo (ResaleScorer) extended with vehicle/equipment price references
- Category detection using weighted keyword matching
- CSV and JSON import endpoints
- 29 seed listings across all 4 priority categories
- React dashboard with tactical dark theme (Outfit/IBM Plex Sans/JetBrains Mono)
- Deal cards with score badges, urgency chips, % below median
- Filter bar: search, category tabs, max price, score slider
- Sort by score/price/recency
- Sidebar: stats panel, score distribution, category breakdown, import panel
- Expandable card details with score breakdown visualization
- All tests passed (100% backend + frontend)

## What Was Reused vs Modified from Repo
- **Reused**: scorer.py (ResaleScorer, ScoreBreakdown, CATEGORY_PRICE_REFERENCE, URGENCY_KEYWORDS)
- **Reused**: filters.py (FilterEngine, apply_standard_filters)
- **Reused**: config_schema.py (MarketplaceScraperConfig)
- **Preserved**: scraper.py (Playwright scraper, made optional)
- **Modified**: __init__.py (simplified to avoid requiring all deps)
- **New**: server.py (full FastAPI app wrapping the module)
- **New**: App.js (complete React dashboard)
- **Replaced**: SQLite storage → MongoDB

## Prioritized Backlog
### P0 (Critical for real use)
- [ ] Screenshot/OCR ingestion for phone-captured listings
- [ ] Craigslist scraper module
- [ ] GovPlanet structured listing scraper
- [ ] Bulk actions (mark multiple as sold)

### P1 (High value)
- [ ] Price trend tracking over time
- [ ] Alert system for high-score new listings
- [ ] Export deals to CSV
- [ ] Saved searches / watchlists

### P2 (Nice to have)
- [ ] Cross-platform listing detection (same item on multiple sites)
- [ ] Seller reputation tracking
- [ ] ROI calculator per listing
- [ ] Mobile-responsive improvements
- [ ] Dark/light theme toggle

## Next Tasks
1. Add Craigslist scraper module (preferred source per user)
2. Add screenshot OCR ingestion
3. Add GovPlanet structured scraper
4. Add bulk actions and export
