"""Outputs: console summary, CSV, JSON, and a self-contained HTML report.

The HTML is the deliverable — KPI tiles + a sortable/filterable table showing
each matched game's odds on both books, the per-outcome difference, which book
pays better (bold + ▲, never colour alone), and each book's margin. Theme-aware
(light/dark), responsive, no external assets.
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .models import Match


def _summary(rows: list[dict], only_toto: list[Match], only_vbet: list[Match]) -> dict:
    methods = Counter(r.get("method", "fuzzy") for r in rows)
    best = Counter()
    for r in rows:
        for o in ("home", "draw", "away"):
            b = r.get(f"best_{o}")
            if b:
                best[b] += 1
    return {
        "matched": len(rows),
        "fuzzy": methods.get("fuzzy", 0),
        "ai": methods.get("ai", 0),
        "toto_margin": round(statistics.mean(r["toto_margin_pct"] for r in rows), 2) if rows else 0,
        "vbet_margin": round(statistics.mean(r["vbet_margin_pct"] for r in rows), 2) if rows else 0,
        "best_toto": best.get("toto", 0),
        "best_vbet": best.get("vbet", 0),
        "best_equal": best.get("equal", 0),
        "only_toto": len(only_toto),
        "only_vbet": len(only_vbet),
    }


def write_reports(rows: list[dict], unmatched_toto: list[Match],
                  unmatched_vbet: list[Match], out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # Fixed filenames: each run overwrites the previous outputs (one of each).
    # The run time is still recorded inside the report as a label.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    (out / "comparison.json").write_text(json.dumps({
        "matched": rows,
        "only_totogaming": [f"{m.home_team} vs {m.away_team}" for m in unmatched_toto],
        "only_vbet": [f"{m.home_team} vs {m.away_team}" for m in unmatched_vbet],
    }, ensure_ascii=False, indent=1))

    if rows:
        csv_path = out / "comparison.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    summary = _summary(rows, unmatched_toto, unmatched_vbet)
    html_path = out / "report.html"
    html_path.write_text(_render_html(rows, summary, stamp))

    print(f"\nMatched {summary['matched']} "
          f"(fuzzy {summary['fuzzy']}, ai {summary['ai']})   "
          f"avg margin: Toto {summary['toto_margin']}% vs VBet {summary['vbet_margin']}%   "
          f"best price: VBet {summary['best_vbet']} / Toto {summary['best_toto']} / "
          f"equal {summary['best_equal']}")
    print(f"Report:  {html_path}")
    print(f"Data:    {out}/comparison.csv|.json")


def _render_html(rows: list[dict], s: dict, stamp: str) -> str:
    data_json = json.dumps(rows, ensure_ascii=False)
    return _TEMPLATE.replace("__DATA__", data_json) \
        .replace("__STAMP__", stamp) \
        .replace("__N__", str(s["matched"])) \
        .replace("__FUZZY__", str(s["fuzzy"])) \
        .replace("__AI__", str(s["ai"])) \
        .replace("__TM__", f"{s['toto_margin']:.2f}") \
        .replace("__VM__", f"{s['vbet_margin']:.2f}") \
        .replace("__BT__", str(s["best_toto"])) \
        .replace("__BV__", str(s["best_vbet"])) \
        .replace("__BE__", str(s["best_equal"])) \
        .replace("__OT__", str(s["only_toto"])) \
        .replace("__OV__", str(s["only_vbet"]))


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Odds Radar — TotoGaming vs VBet</title>
<style>
:root{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --line:#c3c2b7; --toto:#2a78d6; --vbet:#eb6834; --good:#006300;
  --ring:rgba(11,11,11,0.10); --tint:rgba(0,99,0,0.08);
}
@media (prefers-color-scheme: dark){:root:where(:not([data-theme="light"])){
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --line:#383835; --toto:#3987e5; --vbet:#d95926; --good:#0ca30c;
  --ring:rgba(255,255,255,0.10); --tint:rgba(12,163,12,0.14);
}}
:root[data-theme="dark"]{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --line:#383835; --toto:#3987e5; --vbet:#d95926; --good:#0ca30c;
  --ring:rgba(255,255,255,0.10); --tint:rgba(12,163,12,0.14);
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:14px;line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 64px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--ink2);font-size:13px}
.toggle{border:1px solid var(--ring);background:var(--surface);color:var(--ink2);
  border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:22px 0}
.tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:14px 16px}
.tile .lab{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.tile .val{font-size:26px;margin-top:6px;font-weight:600}
.tile .note{color:var(--ink2);font-size:12px;margin-top:4px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;vertical-align:middle;margin-right:5px}
.dot.t{background:var(--toto)} .dot.v{background:var(--vbet)}
.bar{height:6px;border-radius:3px;background:var(--grid);margin-top:8px;overflow:hidden;display:flex}
.bar i{display:block;height:100%}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:6px 0 12px}
.controls input,.controls select{background:var(--surface);color:var(--ink);
  border:1px solid var(--ring);border-radius:8px;padding:7px 10px;font-size:13px}
.controls input{min-width:220px}
.count{color:var(--muted);font-size:12px;margin-left:auto}
.scroll{overflow-x:auto;border:1px solid var(--ring);border-radius:12px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
thead th{position:sticky;top:0;background:var(--surface);color:var(--ink2);font-weight:600;
  text-align:right;padding:9px 10px;border-bottom:1px solid var(--line);font-size:12px;
  white-space:nowrap;cursor:pointer;user-select:none}
thead th.l{text-align:left}
thead tr.grp th{text-align:center;color:var(--ink);border-bottom:1px solid var(--grid);cursor:default}
thead tr.grp th.tg{color:var(--toto)} thead tr.grp th.vg{color:var(--vbet)}
tbody td{padding:7px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
tbody td.l{text-align:left}
tbody tr:hover{background:var(--tint)}
.team{font-weight:500;max-width:236px;overflow:hidden;text-overflow:ellipsis}
.lg{color:var(--muted);font-size:12px;max-width:150px;overflow:hidden;text-overflow:ellipsis}
.better{font-weight:700;color:var(--good)}
.better::after{content:" \25B2";font-size:9px}
.mlow{font-weight:700}
.badge{font-size:10px;padding:1px 6px;border-radius:6px;border:1px solid var(--ring);color:var(--muted)}
.sep{border-left:2px solid var(--grid)}
.legend{color:var(--ink2);font-size:12px;margin:14px 2px 0;display:flex;gap:18px;flex-wrap:wrap}
.foot{color:var(--muted);font-size:12px;margin-top:26px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Odds Radar — <span style="color:var(--toto)">TotoGaming</span> vs <span style="color:var(--vbet)">VBet</span></h1>
      <div class="sub">Pre-match football · 1X2 odds · same-fixture comparison · run __STAMP__</div>
    </div>
    <button class="toggle" id="themeBtn">◐ Theme</button>
  </header>

  <div class="tiles">
    <div class="tile"><div class="lab">Matched games</div><div class="val">__N__</div>
      <div class="note">__FUZZY__ deterministic · __AI__ AI-resolved</div></div>
    <div class="tile"><div class="lab">Avg bookmaker margin</div>
      <div class="val"><span class="dot t"></span>__TM__% &nbsp; <span class="dot v"></span>__VM__%</div>
      <div class="note">lower = tighter pricing / better odds</div>
      <div class="bar"><i id="bt" style="background:var(--toto)"></i><i id="bv" style="background:var(--vbet)"></i></div></div>
    <div class="tile"><div class="lab">Best price (per outcome)</div>
      <div class="val"><span class="dot v"></span>__BV__ &nbsp; <span class="dot t"></span>__BT__</div>
      <div class="note">VBet vs Toto · __BE__ equal</div></div>
    <div class="tile"><div class="lab">Only on one book</div>
      <div class="val" style="font-size:20px"><span class="dot v"></span>__OV__ &nbsp; <span class="dot t"></span>__OT__</div>
      <div class="note">games without a cross-book match</div></div>
  </div>

  <div class="controls">
    <input id="q" type="search" placeholder="Filter by team or league…">
    <select id="method"><option value="">All matches</option>
      <option value="fuzzy">Deterministic only</option><option value="ai">AI-resolved only</option></select>
    <select id="sort">
      <option value="kickoff">Sort: kickoff</option>
      <option value="mgap">Sort: margin gap</option>
      <option value="ogap">Sort: biggest odds gap</option>
    </select>
    <span class="count" id="count"></span>
  </div>

  <div class="scroll">
    <table>
      <thead>
        <tr class="grp">
          <th class="l" colspan="3"></th>
          <th class="tg" colspan="4">TotoGaming</th>
          <th class="vg sep" colspan="4">VBet</th>
          <th></th>
        </tr>
        <tr>
          <th class="l" data-k="kickoff">Kickoff (UTC)</th>
          <th class="l" data-k="game">Match</th>
          <th class="l" data-k="league">League</th>
          <th data-k="toto_home">1</th><th data-k="toto_draw">X</th><th data-k="toto_away">2</th><th data-k="toto_margin_pct">M%</th>
          <th class="sep" data-k="vbet_home">1</th><th data-k="vbet_draw">X</th><th data-k="vbet_away">2</th><th data-k="vbet_margin_pct">M%</th>
          <th data-k="method">via</th>
        </tr>
      </thead>
      <tbody id="tb"></tbody>
    </table>
  </div>

  <div class="legend">
    <span><b class="better" style="color:var(--good)">bold&nbsp;▲</b> = book that pays better for that outcome</span>
    <span><b>bold</b> margin = tighter (lower overround) book</span>
    <span><span class="dot t"></span>Toto <span class="dot v" style="margin-left:10px"></span>VBet</span>
  </div>
  <div class="foot">Odds are decimal, captured at scrape time. Margin (overround) = 1/1 + 1/X + 1/2 − 1.
  Same-fixture matching: kickoff ±3 min + per-side fuzzy name score, with AI adjudication of ambiguous pairs.</div>
</div>

<script>
const DATA = __DATA__;
const tb = document.getElementById('tb');
const fmt = v => (v==null? '—' : (Math.round(v*100)/100).toFixed(2));
function odds(g,side){
  const t=g['toto_'+side], v=g['vbet_'+side], best=g['best_'+side];
  const tc = best==='toto' ? 'better':'', vc = best==='vbet' ? 'better':'';
  return {t:`<span class="${tc}">${fmt(t)}</span>`, v:`<span class="${vc}">${fmt(v)}</span>`};
}
function ogap(g){let m=0;for(const s of ['home','draw','away']){const d=g['diff_'+s];if(d!=null)m=Math.max(m,Math.abs(d));}return m;}
function row(g){
  const h=odds(g,'home'),d=odds(g,'draw'),a=odds(g,'away');
  const tm=g.toto_margin_pct, vm=g.vbet_margin_pct;
  const tmC = tm<vm?'mlow':'', vmC = vm<tm?'mlow':'';
  const kot = (g.kickoff_utc||'').replace('T',' ').replace('+00:00','').slice(5,16);
  return `<tr>
    <td class="l">${kot}</td>
    <td class="l team" title="${g.game}">${g.game}</td>
    <td class="l lg" title="${g.league||''}">${g.league||''}</td>
    <td>${h.t}</td><td>${d.t}</td><td>${a.t}</td><td class="${tmC}">${fmt(tm)}</td>
    <td class="sep">${h.v}</td><td>${d.v}</td><td>${a.v}</td><td class="${vmC}">${fmt(vm)}</td>
    <td><span class="badge">${g.method||''}</span></td></tr>`;
}
function render(){
  const q=document.getElementById('q').value.toLowerCase();
  const meth=document.getElementById('method').value;
  const sort=document.getElementById('sort').value;
  let list=DATA.filter(g=>(!meth||g.method===meth) &&
    ((g.game+' '+(g.league||'')).toLowerCase().includes(q)));
  list.sort((x,y)=> sort==='kickoff' ? (x.kickoff_utc>y.kickoff_utc?1:-1)
    : sort==='mgap' ? Math.abs(y.toto_margin_pct-y.vbet_margin_pct)-Math.abs(x.toto_margin_pct-x.vbet_margin_pct)
    : ogap(y)-ogap(x));
  tb.innerHTML=list.map(row).join('');
  document.getElementById('count').textContent=list.length+' of '+DATA.length+' games';
}
// margin bar widths (relative)
const TM=__TM__, VM=__VM__, mx=Math.max(TM,VM)||1;
document.getElementById('bt').style.width=(TM/mx*50)+'%';
document.getElementById('bv').style.width=(VM/mx*50)+'%';
// header sort
document.querySelectorAll('thead tr:last-child th[data-k]').forEach(th=>{
  th.addEventListener('click',()=>{const k=th.dataset.k;const asc=th._asc=!th._asc;
    DATA.sort((a,b)=>{const x=a[k],y=b[k];return (x>y?1:x<y?-1:0)*(asc?1:-1);});
    document.getElementById('sort').value='kickoff';render();});
});
['q','method','sort'].forEach(id=>document.getElementById(id).addEventListener('input',render));
document.getElementById('themeBtn').addEventListener('click',()=>{
  const r=document.documentElement, cur=r.getAttribute('data-theme');
  r.setAttribute('data-theme', cur==='dark'?'light':(cur==='light'?'dark':'dark'));});
render();
</script>
</body>
</html>
"""
