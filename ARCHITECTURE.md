# Architecture

How the two books are read, how fixtures are matched, and how the comparison is
produced. For the headline result and how to run it, see the
[README](README.md).

## The problem

Both sportsbooks are JavaScript apps: **the HTML contains no odds**, and both sit
behind anti-bot walls (TotoGaming behind Akamai, VBet behind Cloudflare). Plain
`curl`/`requests` are blocked at the TLS layer. The odds only exist in the
structured data the apps fetch at runtime.

Two decisions follow from that:

- **Don't parse the DOM.** The odds aren't reliably in the readable DOM, and CSS
  selectors break on every redesign. The apps' own data feeds are a far more
  stable contract.
- **Don't fake a browser — use one.** A real Chromium driven by Playwright
  passes both walls (even headless). We navigate like a user and read the data
  in flight.

Each book is read by its own adapter, and both emit the same canonical `Match`
object, so everything downstream is book-agnostic.

## Reading VBet (BetConstruct Swarm)

VBet's app talks to a **Swarm** JSON-RPC service over a WebSocket
(`wss://…swarm…vbet.am`). The odds arrive as clean JSON in a tree:

```
sport → region → competition → game → market (type "P1XP2") → event (W1 / Draw / W2 price)
```

The adapter lets the real page establish the Cloudflare-approved session, then
opens its **own** WebSocket inside the page origin, replays the site's session
handshake, and issues a single `get` query for **all** soccer pre-match games
carrying the 1X2 (`P1XP2`) market. That one query returns the full catalogue
(~633 games) instead of only the handful shown on screen. Swarm streams
incrementally, so a game can arrive across several frames — the mapper merges by
game id.

## Reading TotoGaming (Digitain, encrypted)

Digitain's endpoints are REST, but the useful ones return
`{ payload: <base64>, timestamp }` where the payload is **AES-encrypted and
decrypted client-side by a WebAssembly module** (`decrypt.wasm`, loaded as
`window.createDecryptor`). The key is compiled into the WASM.

Rather than reverse the compiled crypto, the adapter **reuses the site's own
decryptor**: it waits for the frame that exposes `window.createDecryptor`, then,
inside that frame, fetches each endpoint same-origin and runs the site's own
`decrypt → JSON.parse` on the response. No key is broken — the site decrypts its
own data; we just ask it to from inside its own origin.

For full coverage it enumerates every football championship
(`GetSportsWithChampionships`), then fetches and decrypts each championship's
events in batches (`GetEventsListWithStakeTypes`), reading the `Result` market
(`W1`/`X`/`W2` → home/draw/away). That yields ~1,175 games.

## Canonical model (`src/models.py`)

Every adapter emits this and nothing else:

```python
Odds(home, draw, away)          # decimal odds, 1X2
  .margin()                     # bookmaker overround = 1/home + 1/draw + 1/away − 1
  .is_valid()                   # rejects impossible odds / overround

Match(bookmaker, sport, league, home_team, away_team,
      start_utc, odds, source_id, fetched_at)

MatchedPair(toto, vbet, match_score, method)   # method: fuzzy | ai
```

Adapters also save a raw snapshot to `data/raw/` before mapping, so the whole
pipeline can be re-run offline without touching the sites.

## Matching the same fixture (`src/matching.py`)

Precision-first, so a wrong pairing never produces a bogus odds diff:

1. **Normalize names** (`src/normalize.py`) — strip accents, club tokens (`fc`,
   `cf`, …) and founding years; tag gender and age/reserve.
2. **Block by kickoff** — only games within **±3 minutes** (both UTC) are
   candidates; a real same-match shares its kickoff. This also keeps it fast.
3. **Hard gates** — gender and age/reserve must be **equal** (a men's game never
   matches a women's; seniors never match youth). These are constraints, never
   fuzzy-scored.
4. **Per-side fuzzy score** — `rapidfuzz.token_set_ratio` on home↔home and
   away↔away separately. Confident pairs (both sides ≥ threshold) are matched
   one-to-one; a small ambiguous residual is set aside.
5. **LLM adjudication** (`src/ai_match.py`, optional) — the residual (things like
   `PSG` = `Paris Saint-Germain`, `Dinamo City` = `Dinamo Tirana`) goes to an LLM
   in one batched call. Confirmed decisions are written back to
   `config/team_aliases.yaml`, so the next run resolves them deterministically —
   the LLM cost trends to zero as that file matures.

## Compare & report (`src/compare.py`, `src/report.py`)

For each matched pair: both books' odds per outcome, the difference, which book
pays better, and each book's margin. Written to `data/out/` as `report.html`
(the deliverable), `comparison.csv`, and `comparison.json`. Games with no
cross-book match are listed as *only on Toto* / *only on VBet*, never dropped.

Orchestrated by `src/main.py` (`python -m src.main`).

## Results (last run)

- Extracted: **VBet 633**, **TotoGaming 1,175**.
- **Matched: 562** | only VBet: 71 | only Toto: 613.
- **Average margin: Toto 7.64% vs VBet 6.97%** — VBet prices tighter.
- **Best price per outcome: VBet 874 / Toto 651 / 161 equal.**

## Design properties

- **Adapter pattern** — a third book is one new file implementing `Scraper`.
- **Config over code** — URLs, thresholds and aliases live in `config/*.yaml`.
- **Raw snapshots** — every run is reproducible offline from `data/raw/`.
- **Precision-first matching** — unmatched is honest; force-matching is not.

## Limitations

- Football, 1X2 only (both feeds carry more; the sport is one config value away).
- Odds are a point-in-time snapshot, not a live feed.
- Both adapters need a real browser session; a browser-free path is possible but unbuilt.
