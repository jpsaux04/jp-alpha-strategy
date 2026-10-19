/* EXEC-10 — behavioural test for the live poller's row reconciliation.
 *
 * The poller patches the positions table by symbol from /api/live. Until
 * EXEC-10 it only ever WROTE cells for symbols present in the payload, so a
 * position that left the book mid-session (a queued exit filling at the open)
 * kept its row, frozen at the previous run's mark and still badged EXIT
 * QUEUED, while the tiles updated by the same tick had already dropped it.
 *
 * Extracted from dashboard.html and run against a minimal DOM stub: this
 * asserts what the code DOES, not merely that the source mentions the right
 * identifiers. Source-level checks live in tests/test_execution.py.
 *
 * Usage: node tests/test_live_poller.js   (expects dashboard.html alongside)
 */
'use strict';
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'dashboard.html');
const src = fs.readFileSync(HTML, 'utf8');

//  The poller is the inline block that opens with the "Live poller" comment.
const blocks = [...src.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)]
      .map(m => m[1]).filter(b => b.includes('Live poller'));
if (blocks.length !== 1) {
  console.error(`FATAL: expected exactly 1 "Live poller" script block, found ${blocks.length}`);
  process.exit(1);
}
const POLLER = blocks[0];

let PASS = 0; const FAIL = [];
function check(name, cond, detail) {
  if (cond) { PASS++; console.log(`  ok    ${name}`); }
  else { FAIL.push(name); console.log(`  FAIL  ${name}${detail ? '  — ' + detail : ''}`); }
}

// ── minimal DOM ─────────────────────────────────────────────────────────────
function mkCell(attrs) {
  return {
    attrs: attrs || {}, innerHTML: '', textContent: '', className: '',
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = v; },
  };
}
function mkRow(sym, tagHTML) {
  const tag = mkCell({ 'data-sym': sym, 'data-f': 'tag' });
  tag.innerHTML = tagHTML || '';
  const risk = mkCell({ 'data-sym': sym, 'data-f': 'risk' });
  risk.textContent = '$1,603'; risk.className = 'mono amb';
  const cur = mkCell({ 'data-sym': sym, 'data-f': 'cur' });
  cur.textContent = '0.00';
  const plpc = mkCell({ 'data-sym': sym, 'data-f': 'plpc' });
  const cls = new Set();
  return {
    sym, cells: { tag, risk, cur, plpc },
    attrs: { 'data-row-sym': sym },
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    classList: {
      add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c),
      _set: cls,
    },
    querySelector(sel) {
      const m = /\[data-f="([^"]+)"\]/.exec(sel);
      return (m && this.cells[m[1]]) ? this.cells[m[1]] : null;
    },
  };
}

function mkDoc(rows) {
  const all = [];
  rows.forEach(r => { Object.values(r.cells).forEach(c => all.push(c)); });
  return {
    rows,
    els: {},
    getElementById(id) {               // tiles absent; banner stubbed on demand
      return this.els[id] || null;
    },
    querySelectorAll(sel) {
      if (sel === 'tr[data-row-sym]') return rows;
      const ms = /\[data-sym="([^"]+)"\]\[data-f="([^"]+)"\]/.exec(sel);
      if (ms) return all.filter(c => c.getAttribute('data-sym') === ms[1]
                                  && c.getAttribute('data-f') === ms[2]);
      return [];
    },
  };
}

//  Run the poller once against a given payload and DOM.
async function run(doc, payload) {
  const sandbox = {
    location: { protocol: 'http:' },
    document: doc,
    setInterval: () => 0,
    fetch: async () => ({ ok: true, json: async () => payload }),
    console,
  };
  const fn = new Function('location', 'document', 'setInterval', 'fetch', 'console',
                          POLLER + '\n;');
  fn(sandbox.location, sandbox.document, sandbox.setInterval, sandbox.fetch, console);
  await new Promise(r => setTimeout(r, 0));   // let the async tick settle
}

const QUEUED = ' <span class="tag" style="background:var(--amb);color:#111" title="...">EXIT QUEUED</span>';

(async () => {
  // ── 1. the real NKE case: filled at the open, gone from the payload ───────
  {
    const nke = mkRow('NKE', QUEUED), sbux = mkRow('SBUX', '');
    const doc = mkDoc([nke, sbux]);
    await run(doc, {
      equity: 110102, total_return_pct: 10.14, day_pl: -163, day_pl_pct: -0.15,
      open_risk: 3851, open_risk_pct: 3.5, drawdown_pct: -0.93, ts: '2026-10-19T12:56:51',
      market_open: true,
      positions: [{ symbol: 'SBUX', cur: 98.11, plpc: 6.11 }],
      pending: [{ symbol: 'NKE', side: 'sell', qty: 317, status: 'filled',
                  filled_at_et: '09:35 ET', filled_avg_price: '37.115678' }],
    });
    check('a position absent from the payload is marked closed',
          nke.classList.contains('row-closed'), 'NKE row was left as a live position');
    check('a position still held is NOT marked closed',
          !sbux.classList.contains('row-closed'), 'SBUX was wrongly retired');
    check('the stale EXIT QUEUED badge is replaced by the fill',
          nke.cells.tag.innerHTML.includes('FILLED 09:35 ET')
          && !nke.cells.tag.innerHTML.includes('EXIT QUEUED'),
          nke.cells.tag.innerHTML);
    check('the fill price reaches the tooltip',
          (nke.cells.tag.getAttribute('title') || '').includes('37.115678'),
          nke.cells.tag.getAttribute('title'));
    check('the closed row no longer claims capital at risk',
          nke.cells.risk.textContent === '—' && nke.cells.risk.className === 'mono mut',
          `${nke.cells.risk.textContent} / ${nke.cells.risk.className}`);
    check('a held position still gets its mark updated',
          sbux.cells.cur.textContent === '98.11', sbux.cells.cur.textContent);
    check('a held position keeps its risk cell',
          sbux.cells.risk.textContent === '$1,603', sbux.cells.risk.textContent);
  }

  // ── 2. NEGATIVE: not held, but the order has NOT filled ───────────────────
  //  Must not announce a fill it cannot see. This is the case that matters:
  //  claiming "FILLED" for an order still sitting at the broker would be the
  //  same false-reassurance bug EXEC-9 fixed, pointed the other way.
  {
    const nke = mkRow('NKE', QUEUED);
    await run(mkDoc([nke]), {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [],
      pending: [{ symbol: 'NKE', side: 'sell', qty: 317, status: 'accepted',
                  filled_at_et: '', filled_avg_price: null }],
    });
    check('an unfilled order is labelled CLOSED, never FILLED',
          nke.cells.tag.innerHTML.includes('CLOSED')
          && !nke.cells.tag.innerHTML.includes('FILLED'),
          nke.cells.tag.innerHTML);
  }

  // ── 3. NEGATIVE: no pending record at all (a true disappearance) ──────────
  {
    const x = mkRow('XOM', '');
    await run(mkDoc([x]), {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [], pending: [],
    });
    check('a row with no pending record is still reconciled away',
          x.classList.contains('row-closed') && x.cells.tag.innerHTML.includes('CLOSED'),
          x.cells.tag.innerHTML);
  }

  // ── 4. a partial fill is its own state, not a close ──────────────────────
  {
    const nke = mkRow('NKE', QUEUED);
    await run(mkDoc([nke]), {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [],
      pending: [{ symbol: 'NKE', side: 'sell', qty: 317, status: 'partially_filled',
                  filled_qty: '100', filled_at_et: '', filled_avg_price: null }],
    });
    check('a partial fill is labelled as such',
          nke.cells.tag.innerHTML.includes('PARTIAL FILL'), nke.cells.tag.innerHTML);
  }

  // ── 5. the queued-order BANNER is corrected too ──────────────────────────
  //  The banner is server-rendered prose asserting the order is "held by the
  //  broker until the next open". That is the same claim the badge makes, and
  //  it stops being true at the same instant. One idea, two renderings: keep
  //  them correct together — the exact failure EXEC-9 was about.
  {
    const nke = mkRow('NKE', QUEUED);
    const doc = mkDoc([nke]);
    const txt = mkCell({});
    txt.textContent = 'SELL 317 NKE (exit). Submitted after the close and held by the broker until the next open';
    doc.els['pending-banner-txt'] = txt;
    await run(doc, {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [],
      pending: [{ symbol: 'NKE', side: 'sell', qty: 317, status: 'filled',
                  filled_at_et: '09:35 ET', filled_avg_price: '37.115678' }],
    });
    check('the queued banner stops saying "held until the next open" once filled',
          txt.textContent.includes('FILLED') && !txt.textContent.includes('until the next open'),
          txt.textContent);
    check('the banner names the fill',
          txt.textContent.includes('09:35 ET') && txt.textContent.includes('37.115678'),
          txt.textContent);
  }

  // ── 6. NEGATIVE: banner untouched while an order is genuinely still queued ─
  {
    const doc = mkDoc([mkRow('NKE', QUEUED)]);
    const txt = mkCell({});
    const ORIG = 'SELL 317 NKE (exit). Submitted after the close and held by the broker until the next open';
    txt.textContent = ORIG;
    doc.els['pending-banner-txt'] = txt;
    await run(doc, {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [],
      pending: [{ symbol: 'NKE', side: 'sell', qty: 317, status: 'accepted',
                  filled_at_et: '', filled_avg_price: null }],
    });
    check('a genuinely queued order leaves the banner alone',
          txt.textContent === ORIG, txt.textContent);
  }

  // ── 7. a row that comes BACK is un-marked (idempotence both ways) ─────────
  {
    const nke = mkRow('NKE', QUEUED);
    const doc = mkDoc([nke]);
    const gone = {
      equity: 1, total_return_pct: 0, day_pl: 0, day_pl_pct: 0, open_risk: 0,
      open_risk_pct: 0, drawdown_pct: 0, ts: '2026-10-19T12:56:51', market_open: true,
      positions: [], pending: [],
    };
    await run(doc, gone);
    const marked = nke.classList.contains('row-closed');
    await run(doc, Object.assign({}, gone, {
      positions: [{ symbol: 'NKE', cur: 37.14, plpc: 9.9 }] }));
    check('a position that reappears is restored to live',
          marked && !nke.classList.contains('row-closed'),
          `marked=${marked} still=${nke.classList.contains('row-closed')}`);
  }

  console.log('\n' + '='.repeat(70));
  console.log(`  ${PASS} passed, ${FAIL.length} failed`);
  if (FAIL.length) { FAIL.forEach(f => console.log(`    FAILED: ${f}`)); process.exit(1); }
  console.log('  LIVE POLLER RECONCILIATION TESTS PASSED');
})();
