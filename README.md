# Odds Radar — TotoGaming vs VBet

Scrape every **pre-match football** game from two Armenian sportsbooks, find the
**same fixtures** on both, and report the **odds differences** between them.

- **TotoGaming** — Digitain platform
- **VBet** — BetConstruct platform

## Result (last run)

- **633** VBet games and **1,175** TotoGaming games extracted.
- **562 identical fixtures** matched across both books.
- **Average bookmaker margin: Toto 7.64% vs VBet 6.97%** — VBet prices tighter.
- **Best price per outcome: VBet 874 / Toto 651 / 161 equal** — VBet pays better
  more often, but Toto wins a real minority. Not one-sided.

## What it does

```
extract both books ─▶ normalize ─▶ match same fixtures ─▶ compare odds ─▶ report
```

1. **Extract** — pull all pre-match football games (teams, kickoff, 1X2 odds) from each book.
2. **Match** — pair the same real fixture across the two books (kickoff + team names).
3. **Compare** — for each matched game, put both books' odds side by side and compute the difference and each book's margin.
4. **Report** — write an HTML report plus CSV and JSON.

How the extraction actually works (both sites are JS apps with no odds in the
HTML, behind anti-bot walls) is written up in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Output

Everything lands in `data/out/` — one of each, overwritten on every run:

| File | What it is |
|------|------------|
| `report.html` | The deliverable — open in a browser. KPI tiles + a sortable, filterable table of every matched game's odds on both books, the better-paying side, and each book's margin. Theme-aware, no external assets. |
| `comparison.csv` | The same data as a flat table (one row per matched game). |
| `comparison.json` | `matched` rows plus `only_totogaming` / `only_vbet` lists of games with no cross-book match. |

**A row from `comparison.csv`:**

```
kickoff_utc,league,game,toto_home,vbet_home,diff_home,best_home,...
2026-07-23T17:00:00+00:00,Club Friendlies,FC Bayern Alzenau vs VfR Wormatia 08 Worms,1.82,1.8,0.02,toto,...
```

**A matched game in `comparison.json`:**

```json
{
  "kickoff_utc": "2026-07-23T17:00:00+00:00",
  "league": "Club Friendlies",
  "game": "FC Bayern Alzenau vs VfR Wormatia 08 Worms",
  "toto_margin_pct": 7.33, "vbet_margin_pct": 6.76,
  "toto_home": 1.82, "vbet_home": 1.8, "diff_home": 0.02, "best_home": "toto",
  "toto_draw": 4.2,  "vbet_draw": 4.2, "diff_draw": 0.0,  "best_draw": "equal",
  "toto_away": 3.5,  "vbet_away": 3.65, "diff_away": -0.15, "best_away": "vbet"
}
```

## Running it

```bash
pip install -r requirements.txt
python -m playwright install chromium

python -m src.main            # run on the saved snapshots (no website contact)
SCRAPE=1 python -m src.main   # fetch fresh snapshots first, then run
```

Optional: the matcher can send a small residual of ambiguous name pairs to an
LLM. Set `ANTHROPIC_API_KEY` in a `.env` file (gitignored) to enable it; without
it the deterministic matcher runs alone.

## Layout

```
digitain_ai_automation/
├── README.md · ARCHITECTURE.md
├── requirements.txt · .env (gitignored)
├── config/   settings.yaml · team_aliases.yaml
├── data/     raw/ (snapshots)  ·  out/ (report.html, comparison.csv/.json)
└── src/
    ├── models.py          # canonical Match / Odds (the contract)
    ├── scrapers/          # base.py · vbet.py · totogaming.py
    ├── normalize.py       # team-name cleaning + gender/age tags
    ├── matching.py        # cross-book pairing
    ├── ai_match.py        # LLM adjudication of ambiguous pairs
    ├── compare.py         # per-outcome diffs + margins
    ├── report.py          # CSV / JSON / HTML output
    └── main.py            # orchestrator
```

## Notes

- Scope is **football, 1X2 market**. Both feeds expose other sports and markets — the sport is one config value away.
- Matching is precision-first: unmatched games are reported as *only on X*, never force-matched.
- Odds are a snapshot at scrape time; both books are scraped in one run so the comparison is time-consistent.
