"""Turn matched pairs into comparison rows: odds side by side, differences,
better-paying book per outcome, and each book's margin (overround)."""
from __future__ import annotations

from .models import MatchedPair


def build_rows(pairs: list[MatchedPair]) -> list[dict]:
    rows = []
    for p in pairs:
        row = {
            "kickoff_utc": p.toto.start_utc.isoformat(),
            "league": p.vbet.league,
            "game": f"{p.vbet.home_team} vs {p.vbet.away_team}",
            "method": p.method,
            "match_score": round(p.match_score, 1),
            "toto_margin_pct": round(p.toto.odds.margin() * 100, 2),
            "vbet_margin_pct": round(p.vbet.odds.margin() * 100, 2),
        }
        for outcome in ("home", "draw", "away"):
            t = getattr(p.toto.odds, outcome)
            v = getattr(p.vbet.odds, outcome)
            row[f"toto_{outcome}"] = t
            row[f"vbet_{outcome}"] = v
            if t is not None and v is not None:
                row[f"diff_{outcome}"] = round(t - v, 3)
                row[f"best_{outcome}"] = (
                    "toto" if t > v else "vbet" if v > t else "equal"
                )
        rows.append(row)
    return rows
