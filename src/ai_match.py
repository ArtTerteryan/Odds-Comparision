"""AI adjudication for the gray-zone pairs (Step 2.3).

Sends all ambiguous pairs to Claude in ONE batched call with a strict JSON
schema, gets back {same, confidence, canonical names} per pair, and returns
confirmed matches plus alias entries to learn (so future runs resolve them
deterministically and the AI cost trends to zero).

Pluggable: if no ANTHROPIC_API_KEY is set, adjudicate() returns no confirmations
and the caller reports the gray zone as "needs review".
"""
from __future__ import annotations

import json
import os

from .env import load_env
from .normalize import normalize_team

_SYSTEM = (
    "You are matching football (soccer) fixtures listed by two different "
    "bookmakers. For each numbered pair, decide whether the two entries refer "
    "to the SAME real-world match (same two teams, same fixture). Account for "
    "abbreviations (PSG = Paris Saint-Germain), local codes (America MG = "
    "America Mineiro), club renames (Dinamo City = Dinamo Tirana), and "
    "transliteration. The kickoff times already match. Different teams that "
    "merely share a city or sponsor are NOT the same. When unsure, set "
    "same=false. Provide the canonical full English club names."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "same": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "canonical_home": {"type": "string"},
                    "canonical_away": {"type": "string"},
                },
                "required": ["index", "same", "confidence",
                            "canonical_home", "canonical_away"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _pair_lines(gray: list[dict]) -> str:
    lines = []
    for i, g in enumerate(gray):
        v, t = g["vbet"], g["toto"]
        lines.append(
            f"[{i}] VBet: {v.home_team} vs {v.away_team} "
            f"(league: {v.league}) | "
            f"Toto: {t.home_team} vs {t.away_team} (league: {t.league})")
    return "\n".join(lines)


def adjudicate(gray: list[dict], model: str,
               min_confidence: float = 0.7) -> tuple[list[dict], dict[str, str]]:
    """Return (confirmed_gray_pairs, alias_updates).

    confirmed_gray_pairs is the subset of `gray` the model judged same with
    confidence >= min_confidence. alias_updates maps cleaned team names to a
    cleaned canonical form so the deterministic matcher catches them next time.
    """
    load_env()
    if not gray or not os.environ.get("ANTHROPIC_API_KEY"):
        return [], {}

    try:
        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model,
            max_tokens=8000,
            system=_SYSTEM,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content":
                       "Judge each pair:\n\n" + _pair_lines(gray)}],
        )
        text = next(b.text for b in resp.content if b.type == "text")
        results = json.loads(text)["results"]
    except Exception as e:
        # Never let an AI hiccup break the run — fall back to no confirmations;
        # the gray pairs are reported as unmatched (deterministic result stands).
        print(f"[ai] adjudication skipped ({type(e).__name__}: {e})")
        return [], {}

    confirmed: list[dict] = []
    aliases: dict[str, str] = {}
    for r in results:
        idx = r["index"]
        if not (0 <= idx < len(gray)):
            continue
        if not r["same"] or r["confidence"] < min_confidence:
            continue
        g = gray[idx]
        confirmed.append({**g, "confidence": r["confidence"]})
        # Learn aliases: map each book's cleaned name to the cleaned canonical.
        _learn(aliases, g["vbet"].home_team, g["toto"].home_team, r["canonical_home"])
        _learn(aliases, g["vbet"].away_team, g["toto"].away_team, r["canonical_away"])
    return confirmed, aliases


def _learn(aliases: dict[str, str], name_v: str, name_t: str, canonical: str):
    """Point both books' cleaned names at a single cleaned canonical string."""
    cv = normalize_team(name_v, {})
    ct = normalize_team(name_t, {})
    canon = normalize_team(canonical, {}) or cv or ct
    for k in (cv, ct):
        if k and k != canon:
            aliases[k] = canon
