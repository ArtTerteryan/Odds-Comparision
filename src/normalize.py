"""Team-name normalization for cross-book matching.

Produces, for a raw team name:
  * a cleaned comparison string (accents/club-tokens/years stripped, alias
    applied) used for fuzzy scoring, and
  * hard tags (gender, age/reserve) that must be EQUAL for two teams to match —
    these are constraints, never fuzzy-scored, so men never match women and
    seniors never match youth/reserve sides.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Generic club-type tokens (safe to drop — they carry no identifying info).
# Deliberately conservative: we do NOT strip meaningful qualifiers like city
# codes (America "MG") — those are resolved via aliases / AI instead.
NOISE_TOKENS = {
    "fc", "cf", "sc", "ac", "ca", "aa", "sk", "nk", "bk", "kf", "if", "il",
    "sv", "us", "cd", "cs", "hnk", "nd", "afc", "rfc", "ss", "ssd", "club",
    "fk", "fs", "sd", "ec", "ce", "cd",
}

_GENDER_RE = re.compile(r"\(\s*(w|wom|women|f)\s*\)|women|жен", re.I)
_AGE_RE = re.compile(r"\bu[- ]?(1[5-9]|2[0-3])\b", re.I)
_RESERVE_RE = re.compile(r"\b(ii|b|reserves?|res|amateur|amateure)\b", re.I)
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|0[0-9]|[2-9]\d)\b")  # founding years like 1899, 08, 79


@dataclass(frozen=True)
class NormTeam:
    key: str        # cleaned comparison string
    gender: str     # "M" | "W"
    age: str        # "" | "U19" | "II" ...


def team_gender(name: str) -> str:
    return "W" if _GENDER_RE.search(name or "") else "M"


def team_age(name: str) -> str:
    m = _AGE_RE.search(name or "")
    if m:
        return "U" + m.group(1)
    if _RESERVE_RE.search(name or ""):
        return "II"
    return ""


def normalize_team(name: str, aliases: dict[str, str]) -> str:
    """Cleaned comparison string (tags stripped, accents removed, alias applied)."""
    text = name or ""
    # strip gender/age markers so they don't pollute the fuzzy score
    text = _GENDER_RE.sub(" ", text)
    text = _AGE_RE.sub(" ", text)
    # de-accent
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # lowercase, keep only word chars
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    text = _YEAR_RE.sub(" ", text)
    tokens = [t for t in text.split() if t not in NOISE_TOKENS and len(t) > 0]
    cleaned = " ".join(tokens).strip()
    # apply alias on the cleaned string (aliases are stored cleaned)
    return aliases.get(cleaned, cleaned)


def norm_team(name: str, aliases: dict[str, str]) -> NormTeam:
    return NormTeam(key=normalize_team(name, aliases),
                    gender=team_gender(name),
                    age=team_age(name))
