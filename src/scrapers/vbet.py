"""VBet adapter (BetConstruct platform, Swarm JSON-RPC over WebSocket).

Strategy — see ARCHITECTURE.md:
  * Playwright opens the popular-matches page; the SPA connects to
    wss://swarm-newm.vbet.am and subscribes to the popular pre-match games.
  * We register page.on("websocket") -> framereceived and collect every
    JSON frame arriving from the swarm host.
  * Phase 1: dump all frames to data/raw/ to learn the exact response shape
    (game objects carry team names, start time, and P1XP2 market odds).
  * Phase 2: implement _map_raw() -> Match. Later optimization: speak Swarm
    directly over websockets (no browser) using the recorded requests.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from ..models import Match, Odds
from .base import Scraper, with_retries

# Swarm selection names for the 1X2 market.
_SEL = {"W1": "home", "Draw": "draw", "W2": "away"}


def _extract_p1xp2(game: dict) -> Odds | None:
    """Pull W1/Draw/W2 prices from a game's Match Result (P1XP2) market."""
    markets = game.get("market")
    if not isinstance(markets, dict):
        return None
    for market in markets.values():
        if not isinstance(market, dict) or market.get("type") != "P1XP2":
            continue
        events = market.get("event")
        if not isinstance(events, dict):
            continue
        prices: dict[str, float] = {}
        for ev in events.values():
            slot = _SEL.get(ev.get("name"))
            if slot and ev.get("price") is not None:
                prices[slot] = float(ev["price"])
        if "home" in prices and "away" in prices:
            return Odds(home=prices["home"],
                        draw=prices.get("draw"),
                        away=prices["away"])
    return None


def _emit_game(game: dict, sport: str, league: str, out: dict, fetched) -> None:
    if not {"team1_name", "team2_name"} <= game.keys():
        return
    odds = _extract_p1xp2(game)
    if odds is None or not odds.is_valid():
        return
    gid = str(game.get("id"))
    out[gid] = Match(
        bookmaker="vbet",
        sport=sport,
        league=league,
        home_team=game["team1_name"],
        away_team=game["team2_name"],
        start_utc=datetime.fromtimestamp(game.get("start_ts", 0), tz=timezone.utc),
        odds=odds,
        source_id=gid,
        fetched_at=fetched,
    )


def _walk_for_games(node, out: dict, fetched, sport="", league="") -> None:
    """Descend the Swarm tree tracking the nearest sport/competition names.

    The tree is a chain of {level: {id: {name, <next-level>}}} maps —
    sport -> region -> competition -> game. We label each game with its sport
    and competition (league) name. Frames may deliver partial subtrees, so we
    handle games found at any depth and merge by id (last write wins).
    """
    if isinstance(node, dict):
        # Named container levels: recurse into each id-keyed child, updating
        # the sport/league context from that child's own name.
        for level in ("sport", "region", "competition"):
            container = node.get(level)
            if isinstance(container, dict):
                for child in container.values():
                    if not isinstance(child, dict):
                        continue
                    name = child.get("name", "")
                    s = name if level == "sport" else sport
                    lg = name if level == "competition" else league
                    _walk_for_games(child, out, fetched, s, lg)

        games = node.get("game")
        if isinstance(games, dict):
            for game in games.values():
                if isinstance(game, dict):
                    _emit_game(game, sport, league, out, fetched)

        # A game object may also appear directly (partial frame).
        _emit_game(node, sport, league, out, fetched)

        # Continue into any other nested structures (result/data/details wrappers).
        for key, val in node.items():
            if key not in ("sport", "region", "competition", "game", "market"):
                _walk_for_games(val, out, fetched, sport, league)
    elif isinstance(node, list):
        for val in node:
            _walk_for_games(val, out, fetched, sport, league)


# Field selection for the Swarm `get` (mirrors the site's own query so the
# server returns the tree exactly as our mapper expects).
_WHAT = {
    "sport": ["id", "name", "alias"],
    "region": ["id", "name"],
    "competition": ["id", "name"],
    "game": [["id", "team1_name", "team2_name", "start_ts", "is_live"]],
    "market": ["type", "name"],
    "event": ["price", "name", "type_1"],
}

# In-page Swarm client: open our own socket (same origin -> Cloudflare-approved),
# replay the site's request_session, then send our get and collect frames until
# the response for our rid arrives (or timeout).
_CLIENT_JS = r"""
async ({url, sessionFrame, getQuery, timeoutMs}) => {
  return await new Promise((resolve) => {
    const frames = []; let done = false;
    const ws = new WebSocket(url);
    const getRid = "coverage_get";
    getQuery.rid = getRid;
    const finish = () => { if (!done){ done=true; try{ws.close();}catch(e){} resolve(frames);} };
    const timer = setTimeout(finish, timeoutMs);
    ws.onopen = () => ws.send(JSON.stringify(sessionFrame));
    ws.onmessage = (e) => {
      let msg; try { msg = JSON.parse(e.data); } catch(_) { return; }
      frames.push(msg);
      if (msg.rid === sessionFrame.rid) ws.send(JSON.stringify(getQuery));
      if (msg.rid === getRid) { clearTimeout(timer); setTimeout(finish, 500); }
    };
    ws.onerror = () => finish();
  });
}
"""


class VBetScraper(Scraper):
    name = "vbet"

    def __init__(self, settings: dict):
        self.settings = settings
        self.raw_dir = Path(settings["paths"]["raw_dir"])

    def fetch_games(self) -> list[Match]:
        frames = with_retries(self._capture, tries=2, label=self.name)
        self._save_raw(frames)
        return self._map_raw(frames)

    def _capture(self) -> list[dict]:
        """Open our own Swarm socket in the page context and query ALL
        pre-match games for the configured sport with the P1XP2 market."""
        cfg = self.settings["vbet"]
        browser_cfg = self.settings["browser"]
        sport_alias = cfg.get("sport_alias", "Soccer")
        session = {}

        def on_websocket(ws):
            if cfg["swarm_host"] not in ws.url:
                return
            session.setdefault("url", ws.url)

            def on_sent(payload):
                try:
                    msg = json.loads(payload)
                except (TypeError, ValueError):
                    return
                if isinstance(msg, dict) and msg.get("command") == "request_session":
                    session["frame"] = msg

            ws.on("framesent", on_sent)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=browser_cfg["headless"])
            page = browser.new_page()
            page.on("websocket", on_websocket)
            page.goto(cfg["url"], timeout=browser_cfg["timeout_seconds"] * 1000)
            page.wait_for_timeout(browser_cfg["settle_seconds"] * 1000)

            if "frame" not in session or "url" not in session:
                browser.close()
                raise RuntimeError("vbet: could not capture Swarm session/url")

            get_query = {
                "command": "get",
                "params": {
                    "source": "betting",
                    "what": _WHAT,
                    "where": {
                        "sport": {"alias": sport_alias},
                        "game": {"@or": [{"visible_in_prematch": 1},
                                         {"type": {"@in": [0, 2]}}]},
                        "market": {"type": "P1XP2"},
                    },
                    "subscribe": False,
                },
            }
            frames = page.evaluate(_CLIENT_JS, {
                "url": session["url"],
                "sessionFrame": session["frame"],
                "getQuery": get_query,
                "timeoutMs": 20000,
            })
            browser.close()
        print(f"[{self.name}] Swarm returned {len(frames)} frames "
              f"(sport={sport_alias})")
        return frames

    def _save_raw(self, frames: list[dict]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{self.name}.json"   # fixed name; overwrites each run
        path.write_text(json.dumps(frames, ensure_ascii=False, indent=1))
        print(f"[{self.name}] saved {len(frames)} swarm frames -> {path}")

    def _map_raw(self, frames: list[dict]) -> list[Match]:
        """Walk every Swarm frame, collect games carrying a P1XP2 market with
        W1/Draw/W2 prices. Swarm streams incrementally, so a game can appear
        across frames; we merge by game id, last write wins."""
        games: dict[str, Match] = {}
        fetched = datetime.now(timezone.utc)
        for frame in frames:
            _walk_for_games(frame.get("data", {}), games, fetched)
        return list(games.values())
