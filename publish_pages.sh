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

# ── publish gate ────────────────────────────────────────────────────────────
# `git push origin main` pushes EVERY local commit, not just the one above.
# This job runs unattended from cron, so it must never be the thing that
# decides to make research public. If any unpushed commit touches a path
# outside docs/, refuse and leave the snapshot committed locally; a human
# pushes deliberately and the next tick then finds nothing to object to.
git fetch -q origin main || { echo "publish_pages: fetch failed, not pushing"; exit 1; }

OUTSIDE=$(git diff --name-only origin/main..HEAD -- . ':(exclude)docs/**' | head -20)
if [ -n "$OUTSIDE" ]; then
    echo "publish_pages: REFUSING to push — unpushed commits touch files outside docs/:"
    echo "$OUTSIDE" | sed 's/^/    /'
    echo "publish_pages: snapshot committed locally only. Push manually when ready."
    exit 0
fi

git push -q origin main
echo "publish_pages: snapshot published"
