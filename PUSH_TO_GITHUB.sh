#!/bin/bash
# Push Marketplace Scraper to GitHub

set -e

echo "📦 Facebook Marketplace Scraper — GitHub Push"
echo "=============================================="
echo ""

# Check if we're in the right directory
if [ ! -f "README.md" ] || [ ! -d "backend/modules/marketplace_scraper" ]; then
    echo "❌ Error: Run this script from the marketplace_scraper directory"
    exit 1
fi

# Configure git
echo "🔧 Configuring git..."
git config user.email "pdestroyer12387@gmail.com"
git config user.name "Airam"

# Stage all files
echo "📝 Staging files..."
git add -A

# Commit
echo "💾 Creating commit..."
git commit -m "feat: Complete Facebook Marketplace deal scraper module

Production-ready system (4,075 lines) with:
- Playwright browser automation with stealth config
- Deal scoring (0-100) across 5 factors
- Chainable filter pipeline
- SQLite storage with dedup
- FastAPI BaseModule integration
- React dashboard panel with live SSE updates
- Full documentation and setup guides

Components:
- scraper.py (952 lines)
- scorer.py (558 lines)
- filters.py (503 lines)
- storage.py (597 lines)
- module.py (355 lines)
- config_schema.py (317 lines)
- MarketplacePanel.jsx (739 lines)

Architecture, setup instructions, and monetization paths included."

echo ""
echo "✅ Local commit complete!"
echo ""
echo "📤 Next steps to push to GitHub:"
echo ""
echo "1. Create a new repository on GitHub (if you don't have one)"
echo "   → Visit: https://github.com/new"
echo "   → Name it: marketplace-scraper"
echo ""
echo "2. Add the remote:"
echo "   git remote add origin https://github.com/YOUR_USERNAME/marketplace-scraper.git"
echo ""
echo "3. Push to GitHub:"
echo "   git branch -M main"
echo "   git push -u origin main"
echo ""
echo "Or run both at once:"
echo "   GITHUB_URL=https://github.com/YOUR_USERNAME/marketplace-scraper.git"
echo "   git remote add origin \$GITHUB_URL"
echo "   git branch -M main && git push -u origin main"
echo ""
