"""Orchestrator: scrape (or load saved) -> match -> AI-adjudicate -> compare
-> report. Runs on SAVED data by default (no website contact); set
SCRAPE=1 to fetch fresh snapshots first.

    python -m src.main            # pure-data run on the latest saved snapshots
    SCRAPE=1 python -m src.main   # fetch fresh, then run
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from .ai_match import adjudicate
from .compare import build_rows
from .env import load_env
from .matching import match_games
from .models import MatchedPair
from .report import write_reports
from .scrapers.totogaming import TotoGamingScraper
from .scrapers.vbet import VBetScraper

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
RAW_DIR = ROOT / "data" / "raw"


def load_games(settings: dict, scrape: bool):
    """Return (vbet_games, toto_games) from fresh scrape or saved snapshots.

    Saved snapshots use fixed names (data/raw/vbet.json,
    totogaming_decrypted.json); each scrape overwrites them.
    """
    if scrape:
        toto = TotoGamingScraper(settings).fetch_games()
        vbet = VBetScraper(settings).fetch_games()
        return vbet, toto
    vbet = VBetScraper(settings)._map_raw(json.load(open(RAW_DIR / "vbet.json")))
    toto = TotoGamingScraper(settings)._map_raw(
        json.load(open(RAW_DIR / "totogaming_decrypted.json")))
    print(f"[saved] loaded VBet={len(vbet)} Toto={len(toto)} (no website contact)")
    return vbet, toto


def persist_aliases(path: Path, new: dict[str, str]) -> None:
    """Merge newly-learned aliases into the YAML file (kept human-readable)."""
    if not new:
        return
    existing = yaml.safe_load(path.read_text()) or {}
    if not isinstance(existing, dict):
        existing = {}
    existing.update(new)
    header = ("# Manual + AI-learned cross-book team-name mappings.\n"
              "# key: normalized name -> value: canonical normalized name.\n"
              "# Both books' spellings point to the same canonical string so\n"
              "# the deterministic matcher pairs them without AI next time.\n")
    body = yaml.safe_dump(existing, allow_unicode=True, sort_keys=True)
    path.write_text(header + body)
    print(f"[aliases] persisted {len(new)} new -> {path} "
          f"({len(existing)} total)")


def main() -> None:
    load_env()
    settings = yaml.safe_load((CONFIG_DIR / "settings.yaml").read_text())
    aliases_path = CONFIG_DIR / "team_aliases.yaml"
    aliases = yaml.safe_load(aliases_path.read_text()) or {}
    if not isinstance(aliases, dict):
        aliases = {}
    m = settings["matching"]

    vbet, toto = load_games(settings, scrape=bool(os.environ.get("SCRAPE")))

    result = match_games(vbet, toto, aliases,
                         high_threshold=m["high_threshold"],
                         gray_low=m["gray_low"],
                         window_minutes=m["kickoff_window_minutes"])
    print(f"deterministic: matched={len(result.matched)} gray={len(result.gray)} "
          f"only_vbet={len(result.only_vbet)} only_toto={len(result.only_toto)}")

    # AI adjudication of the gray zone (pluggable).
    confirmed, new_aliases = ([], {})
    if m.get("use_ai") and result.gray:
        confirmed, new_aliases = adjudicate(result.gray, m["ai_model"])
        print(f"AI: {len(confirmed)}/{len(result.gray)} gray pairs confirmed")
        persist_aliases(aliases_path, new_aliases)

    # Promote AI-confirmed pairs into the matched set.
    matched = list(result.matched)
    for c in confirmed:
        matched.append(MatchedPair(toto=c["toto"], vbet=c["vbet"],
                                   match_score=c.get("confidence", 1.0) * 100,
                                   method="ai"))

    # Gray pairs the AI did NOT confirm fall back to "only on X".
    confirmed_ids = {(c["vbet"].source_id, c["toto"].source_id) for c in confirmed}
    unconfirmed = [g for g in result.gray
                   if (g["vbet"].source_id, g["toto"].source_id) not in confirmed_ids]
    only_vbet = list(result.only_vbet) + [g["vbet"] for g in unconfirmed]
    only_toto = list(result.only_toto) + [g["toto"] for g in unconfirmed]

    print(f"FINAL matched={len(matched)} "
          f"(fuzzy={sum(1 for p in matched if p.method=='fuzzy')}, "
          f"ai={sum(1 for p in matched if p.method=='ai')})  "
          f"only_vbet={len(only_vbet)} only_toto={len(only_toto)}")

    rows = build_rows(matched)
    write_reports(rows, only_vbet, only_toto, settings["paths"]["out_dir"])


if __name__ == "__main__":
    main()
