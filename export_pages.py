#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════════════════
  export_pages.py — Static GitHub Pages export           (READ-ONLY · ADDITIVE)
═══════════════════════════════════════════════════════════════════════════════

  Takes the dashboard HTML that build_dashboard.py already generates and turns
  it into a self-contained static page suitable for public hosting:

    • strips the live-polling <script> that calls /api/live (that endpoint only
      exists on the private server and would 404 publicly)
    • stamps a "static snapshot" banner with the generation time
    • writes docs/index.html  (GitHub Pages serves /docs on the main branch)
    • copies the backtest tear sheet alongside it

  Publishes NO credentials: the source dashboard.html is already key-free, and
  this script only ever removes content, never adds server data.

  Writes NO trading state and touches nothing the frozen agent reads.
"""
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
SRC = BASE / "dashboard.html"
DOCS = BASE / "docs"
OUT = DOCS / "index.html"

# Anything matching these must never reach the public page. Belt-and-braces:
# dashboard.html is already clean, but we re-verify on every export.
SECRET_PAT = re.compile(
    r"APCA[_A-Z]*|api[_-]?key|secret[_-]?key|Bearer\s+[A-Za-z0-9._-]{10,}|PK[A-Z0-9]{16,}",
    re.I,
)

BANNER = """<div style="background:#1f2937;color:#e5e7eb;padding:10px 14px;
border-radius:8px;margin:0 0 14px 0;font:13px/1.5 system-ui,sans-serif">
<b>Static snapshot</b> — generated {ts}. This is a point-in-time export of a
private monitoring dashboard; live auto-refresh is disabled. Paper-trading
account (simulated capital) — not investment advice.
</div>"""


def strip_live_js(html: str) -> str:
    """Remove the <script> block that polls /api/live (kept out of static build)."""
    out, removed = [], 0
    pos = 0
    while True:
        i = html.find("<script", pos)
        if i == -1:
            out.append(html[pos:])
            break
        j = html.find("</script>", i)
        if j == -1:
            out.append(html[pos:])
            break
        j += len("</script>")
        block = html[i:j]
        if "/api/live" in block:
            out.append(html[pos:i])   # keep everything before, drop the block
            removed += 1
        else:
            out.append(html[pos:j])
        pos = j
    print(f"  stripped {removed} live-polling script block(s)")
    return "".join(out)


def main():
    if not SRC.exists():
        raise SystemExit(f"missing {SRC} — run build_dashboard.py first")

    html = SRC.read_text()
    html = strip_live_js(html)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    banner = BANNER.format(ts=ts)
    # Insert the banner just inside <body> (fall back to prepending).
    m = re.search(r"<body[^>]*>", html, re.I)
    html = (html[: m.end()] + banner + html[m.end():]) if m else banner + html

    # Final safety gate — refuse to publish if anything key-shaped appears.
    hits = [h for h in SECRET_PAT.findall(html)]
    if hits:
        raise SystemExit(f"ABORT: possible secret material in output: {set(hits)}")

    DOCS.mkdir(exist_ok=True)
    (DOCS / ".nojekyll").write_text("")   # serve files verbatim, no Jekyll
    OUT.write_text(html)
    print(f"  wrote {OUT} ({len(html):,} bytes)")

    tear = BASE / "backtest_tearsheet.png"
    if tear.exists():
        shutil.copy2(tear, DOCS / "backtest_tearsheet.png")
        print("  copied backtest_tearsheet.png")

    print("static export OK — no secret material detected")


if __name__ == "__main__":
    main()
