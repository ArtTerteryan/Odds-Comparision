"""Canonical data model — the contract every scraper adapter must emit.

Everything downstream (matching, comparison, reporting) depends only on
these types, never on bookmaker-specific payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Odds:
    """Decimal odds for the 1X2 (match result) market."""
    home: float
    draw: float | None  # None for sports without a draw outcome
    away: float

    def margin(self) -> float:
        """Bookmaker overround: how far implied probabilities exceed 100%."""
        total = 1 / self.home + 1 / self.away
        if self.draw:
            total += 1 / self.draw
        return total - 1.0

    def is_valid(self) -> bool:
        """Reject malformed odds: decimal odds are always >= 1.0, and a real
        1X2 market has a small positive overround (never negative, never huge)."""
        vals = [self.home, self.away] + ([self.draw] if self.draw is not None else [])
        if any(v is None or v < 1.01 or v > 1000 for v in vals):
            return False
        return 0.0 < self.margin() < 1.0


@dataclass(frozen=True)
class Match:
    bookmaker: str          # "totogaming" | "vbet"
    sport: str
    league: str
    home_team: str
    away_team: str
    start_utc: datetime
    odds: Odds
    source_id: str          # the bookmaker's own game id, for traceability
    fetched_at: datetime


@dataclass(frozen=True)
class MatchedPair:
    toto: Match
    vbet: Match
    match_score: float      # fuzzy-name confidence, 0–100
    method: str = "fuzzy"   # "fuzzy" | "alias" | "ai"
