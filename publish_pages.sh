#!/usr/bin/env bash
# publish_pages.sh — refresh the public GitHub Pages snapshot.
# Read-only w.r.t. trading: regenerates docs/ from the already-built
# dashboard.html and pushes ONLY the docs/ folder. Never touches strategy state.
set -euo pipefail
cd /root/jp_strategy

./venv/bin/python export_pages.py

# Stage only the published folder — never sweep in anything else.
git add docs

if git diff --cached --quiet; then
    echo "publish_pages: no change to publish"
    exit 0
fi

git commit -q -m "chore(pages): refresh dashboard snapshot $(date -u '+%Y-%m-%d %H:%M UTC')"
git push -q origin main
echo "publish_pages: snapshot published"
