"""Cross-book matching: find the same real fixture across the two books.

Deterministic core (Step 2.2):
  * dedup within each book,
  * block by kickoff time (both UTC),
  * gate on hard tags (gender, age/reserve) — never fuzzy-matched,
  * score per side (home<->home, away<->away),
  * one-to-one greedy assignment (no game claimed twice).

Output buckets:
  matched   -> confident pairs (per-side min >= high_threshold)
  gray      -> tag/time-compatible, avg score in [gray_low, high) -> AI/review
  only_vbet -> VBet games with no acceptable candidate
  only_toto -> Toto games left unmatched

The AI layer (Step 2.3) consumes `gray` and promotes confirmed pairs.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import Match, MatchedPair
from .normalize import norm_team


@dataclass
class _G:
    """A game with precomputed normalized keys + tags."""
    match: Match
    hk: str
    ak: str
    gender: str
    age: str
    ts: float

    @property
    def dedup_key(self):
        return (self.hk, self.ak, int(self.ts // 60), self.gender, self.age)


@dataclass
class MatchResult:
    matched: list[MatchedPair]
    gray: list[dict]          # {"vbet":Match,"toto":Match,"home":int,"away":int,"score":float}
    only_vbet: list[Match]
    only_toto: list[Match]


def _prep(games: list[Match], aliases: dict[str, str]) -> list[_G]:
    out, seen = [], set()
    for m in games:
        h = norm_team(m.home_team, aliases)
        a = norm_team(m.away_team, aliases)
        g = _G(match=m, hk=h.key, ak=a.key,
               gender=h.gender if h.gender == a.gender else "M",
               age=h.age or a.age, ts=m.start_utc.timestamp())
        if g.dedup_key in seen:      # within-book duplicate (multi-league listing)
            continue
        seen.add(g.dedup_key)
        out.append(g)
    return out


def _side_scores(v: _G, t: _G) -> tuple[float, float]:
    return (fuzz.token_set_ratio(v.hk, t.hk),
            fuzz.token_set_ratio(v.ak, t.ak))


def match_games(vbet: list[Match], toto: list[Match], aliases: dict[str, str],
                high_threshold: float, gray_low: float,
                window_minutes: int) -> MatchResult:
    V = _prep(vbet, aliases)
    T = _prep(toto, aliases)
    window = window_minutes * 60

    T_sorted = sorted(T, key=lambda g: g.ts)
    T_ts = [g.ts for g in T_sorted]

    def candidates(v: _G) -> list[_G]:
        lo = bisect.bisect_left(T_ts, v.ts - window)
        hi = bisect.bisect_right(T_ts, v.ts + window)
        return T_sorted[lo:hi]

    # Score every tag/time-compatible pair once.
    scored = []  # (side_min, side_avg, home, away, v, t)
    for v in V:
        for t in candidates(v):
            if v.gender != t.gender or v.age != t.age:
                continue  # hard gate
            home, away = _side_scores(v, t)
            scored.append((min(home, away), (home + away) / 2, home, away, v, t))

    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)

    used_v, used_t = set(), set()
    matched: list[MatchedPair] = []
    # Pass 1 — confident (per-side min >= high), one-to-one greedy.
    for side_min, side_avg, home, away, v, t in scored:
        if side_min < high_threshold:
            continue
        if id(v) in used_v or id(t) in used_t:
            continue
        used_v.add(id(v)); used_t.add(id(t))
        matched.append(MatchedPair(toto=t.match, vbet=v.match,
                                   match_score=round(side_avg, 1), method="fuzzy"))

    # Pass 2 — gray zone: best remaining candidate per unused game, avg in [gray_low, high).
    gray: list[dict] = []
    for side_min, side_avg, home, away, v, t in scored:
        if id(v) in used_v or id(t) in used_t:
            continue
        if side_avg < gray_low or side_min >= high_threshold:
            continue
        used_v.add(id(v)); used_t.add(id(t))   # tentatively reserve for AI
        gray.append({"vbet": v.match, "toto": t.match,
                     "home": round(home), "away": round(away),
                     "score": round(side_avg, 1)})

    only_vbet = [v.match for v in V if id(v) not in used_v]
    only_toto = [t.match for t in T if id(t) not in used_t]
    return MatchResult(matched=matched, gray=gray,
                       only_vbet=only_vbet, only_toto=only_toto)
