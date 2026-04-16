# DealScope — Marketplace Deal Intelligence System

## Original Problem Statement
Turn the TechCraft956/marketplace-scraper GitHub repo into a production-ready local-first deal intelligence system that ingests marketplace listings, scores them 0-100, and presents actionable flipping opportunities via a clean dashboard.

## Architecture
- **Backend**: FastAPI (Python 3.11) + MongoDB
- **Frontend**: React 18 + Tailwind CSS
- **Scoring Engine**: Adapted from repo's `scorer.py` with category-aware extensions
- **Ingestion Pipeline**: 
  - CSV/JSON file import
  - Screenshot OCR (GPT-4o Vision primary + Tesseract fallback)
  - Craigslist scraper (requests + BeautifulSoup)
  - GovPlanet scraper (requests + BeautifulSoup)
  - Manual entry API
  - Original Playwright FB scraper (preserved, optional)

## User Persona
- Single-user local tool operator
- Marketplace flipper/reseller
- Deals in vehicles, equipment, electronics, furniture

## Core Requirements (Static)
1. Multi-source ingestion pipeline (CSV, JSON, Screenshot, Scrapers) — DONE
2. Deal scoring 0-100 with explainable breakdown — DONE
3. Category-aware scoring (vehicles, equipment, electronics, furniture) — DONE
4. Dashboard with filter/sort/search — DONE
5. Mark sold/contacted/delete actions — DONE
6. Stats overview with category & score distribution — DONE
7. No auth needed — single-user — DONE
8. MongoDB storage with deduplication — DONE

## What's Been Implemented

### V1 MVP (Session 1)
- Full FastAPI backend with 12 API endpoints
- MongoDB storage with dedup, scoring, import tracking
- Scoring engine from repo (ResaleScorer) extended with vehicle/equipment price references
- Category detection using weighted keyword matching
- CSV and JSON import endpoints
- 29 seed listings across all 4 priority categories
- React dashboard with tactical dark theme
- All tests passed (100%)

### P0 Features (Session 2)
- **Craigslist Scraper**: Full scraper using requests + BeautifulSoup
  - 20 supported cities (Austin, Houston, Dallas, etc.)
  - 12 categories (vehicles, motorcycles, electronics, tools, etc.)
  - Configurable search query, price range, distance
  - Detail page scraping for descriptions (optional)
  - Rate limiting and human-like delays
- **Screenshot OCR Ingestion**: Dual-method extraction
  - GPT-4o Vision (primary) — extracts title, price, location, description, distance, urgency signals
  - Tesseract OCR (fallback) — regex-based text parsing
  - Supports JPEG, PNG, WebP up to 20MB
- **GovPlanet Scraper**: Equipment auction scraper
  - Handles JS-rendered sites gracefully (returns empty with helpful error)
  - 8 equipment categories
- **Frontend Updates**: 
  - Import panel now accepts screenshots alongside CSV/JSON
  - Web Scrapers panel with Craigslist + GovPlanet tabs
  - City/category/query/price controls for each scraper
  - Scrape Now button with result feedback
- **API**: 6 new endpoints (/api/scrape/craigslist, /api/scrape/govplanet, /api/import/screenshot, /api/scrapers)
- All 39 backend tests + all frontend tests passed (100%)

## What Was Reused vs Modified from Repo
- **Reused**: scorer.py, filters.py, config_schema.py, scraper.py (preserved intact)
- **Modified**: __init__.py (simplified)
- **New**: server.py, scrapers/craigslist.py, scrapers/govplanet.py, scrapers/ocr.py, App.js

## Prioritized Backlog
### P1 (High value)
- [ ] Price trend tracking over time
- [ ] Alert system for high-score new listings
- [ ] Export deals to CSV
- [ ] Saved searches / watchlists
- [ ] Bulk actions (mark multiple as sold)

### P2 (Nice to have)
- [ ] Cross-platform listing detection
- [ ] Seller reputation tracking
- [ ] ROI calculator per listing
- [ ] Mobile-responsive improvements
- [ ] Dark/light theme toggle
- [ ] GovPlanet JS-rendered scraping (Playwright)

## Next Tasks
1. Add price trend tracking and alerts
2. Add export to CSV functionality
3. Improve GovPlanet scraping with headless browser
4. Add saved searches
