"""TotoGaming adapter (Digitain platform, behind Akamai).

Strategy — see ARCHITECTURE.md §2c:
  * A real Chromium (Playwright) navigates to the sport page; Akamai issues
    clearance cookies to the genuine browser, and the sportiframe origin loads
    a WebAssembly decryptor (window.createDecryptor).
  * Digitain's prematch endpoints return either plain JSON or an ENCRYPTED
    {payload:<b64>, timestamp}. Rather than reverse the compiled crypto, we
    reuse the site's own WASM decryptor from inside the sportiframe origin.
  * FULL COVERAGE: enumerate every football championship via
    GetSportsWithChampionships, then fetch GetEventsListWithStakeTypes per
    championship (batched, same-origin fetch + transparent decrypt), and map
    the decrypted events (StakeType "Result" -> W1/X/W2 factors) to Match.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from ..models import Match, Odds
from .base import Scraper, with_retries

# 1X2 selection code -> Odds slot, within the "Result" stake type.
_SEL = {"W1": "home", "X": "draw", "W2": "away"}

# Stake type ids requested (1 == Result / 1X2; the rest mirror the site).
_STAKE_IDS = (1, 702, 37, 3, 2, 2532, 2533)

# In-frame helper: same-origin fetch with transparent WASM decrypt of any
# {payload} response. Defined once, reused for both the championship list and
# the per-championship event fetches (batched via Promise.all).
_BATCH_JS = r"""
async ({base, champIds, stakesQ, batchSize}) => {
  const p = await window.createDecryptor();
  const wa=p.cwrap("wasm_alloc","number",["number"]), wf=p.cwrap("wasm_free",null,["number"]);
  const wd=p.cwrap("wasm_decrypt","number",["number","number"]);
  const wgr=p.cwrap("wasm_get_result","number",[]), wgl=p.cwrap("wasm_get_result_len","number",[]);
  const wfr=p.cwrap("wasm_free_result",null,[]);
  const decode = (b64) => {
    const t=atob(b64); const b=new Uint8Array(t.length);
    for(let i=0;i<t.length;i++) b[i]=t.charCodeAt(i);
    const r=wa(b.length); p.HEAPU8.set(b,r); const n=wd(r,b.length); wf(r);
    if(n!==0) return null;
    const o=wgr(), a=wgl(); const out=p.UTF8ToString(o,a); wfr();
    try { return JSON.parse(out); } catch(e){ return null; }
  };
  const get = async (url) => {
    try {
      const r = await fetch(url, {credentials:"same-origin"});
      let data = await r.json();
      if (data && data.payload) data = decode(data.payload);
      return data;
    } catch(e){ return null; }
  };
  const evUrl = (cid) => `${base}/prematch/geteventslistwithstaketypes?champId=${cid}`
      + `&timeFilter=0&${stakesQ}&langId=2&partnerId=555&countryCode=AM`;
  let all = [];
  for (let i=0;i<champIds.length;i+=batchSize){
    const chunk = champIds.slice(i, i+batchSize);
    const res = await Promise.all(chunk.map(c => get(evUrl(c))));
    for (const d of res){
      if (Array.isArray(d)) all.push(...d);
      else if (d && Array.isArray(d.Events)) all.push(...d.Events);
    }
  }
  return all;
}
"""

# In-frame decrypt-aware fetch for a single URL (the championship list).
_GET_JS = r"""
async (url) => {
  const p = await window.createDecryptor();
  const r = await fetch(url, {credentials:"same-origin"});
  let data = await r.json();
  if (data && data.payload) {
    const wa=p.cwrap("wasm_alloc","number",["number"]), wf=p.cwrap("wasm_free",null,["number"]);
    const wd=p.cwrap("wasm_decrypt","number",["number","number"]);
    const wgr=p.cwrap("wasm_get_result","number",[]), wgl=p.cwrap("wasm_get_result_len","number",[]);
    const wfr=p.cwrap("wasm_free_result",null,[]);
    const t=atob(data.payload); const b=new Uint8Array(t.length);
    for(let i=0;i<t.length;i++) b[i]=t.charCodeAt(i);
    const rp=wa(b.length); p.HEAPU8.set(b,rp); const n=wd(rp,b.length); wf(rp);
    if(n!==0) return null;
    const o=wgr(), a=wgl(); const out=p.UTF8ToString(o,a); wfr();
    return JSON.parse(out);
  }
  return data;
}
"""


class TotoGamingScraper(Scraper):
    name = "totogaming"

    def __init__(self, settings: dict):
        self.settings = settings
        self.raw_dir = Path(settings["paths"]["raw_dir"])

    def fetch_games(self) -> list[Match]:
        events = with_retries(self._capture, tries=2, label=self.name)
        self._save_raw(events)
        return self._map_raw(events)

    def _capture(self) -> list[dict]:
        cfg = self.settings["totogaming"]
        browser_cfg = self.settings["browser"]
        sport_id = cfg.get("sport_id", 1)   # 1 == Football
        stakes_q = "&".join(f"stakesId={s}" for s in _STAKE_IDS)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=browser_cfg["headless"])
            page = browser.new_page()
            page.goto(cfg["url"], timeout=browser_cfg["timeout_seconds"] * 1000)
            page.wait_for_timeout(browser_cfg["settle_seconds"] * 1000)

            frame = self._await_decryptor_frame(page)
            if frame is None:
                browser.close()
                raise RuntimeError("totogaming: decryptor frame never appeared")

            pr = urlparse(frame.url)
            base = f"{pr.scheme}://{pr.netloc}/{pr.path.strip('/').split('/')[0]}"

            # 1) all championships for the sport, then filter to our sport id.
            sw = frame.evaluate(
                _GET_JS,
                f"{base}/prematch/getsportswithchampionships?stakeTypes=1"
                f"&timeFilter=0&langId=2&partnerId=555&countryCode=AM")
            sport = next((s for s in (sw or []) if s.get("Id") == sport_id), None)
            champ_ids = [c["Id"] for c in (sport or {}).get("CSH", []) if c.get("Id")]
            print(f"[{self.name}] {len(champ_ids)} championships for sport {sport_id}")

            # 2) events per championship (batched, decrypted).
            events = frame.evaluate(_BATCH_JS, {
                "base": base, "champIds": champ_ids,
                "stakesQ": stakes_q, "batchSize": 20})
            browser.close()

        uniq = {e.get("Id"): e for e in events if isinstance(e, dict) and e.get("Id")}
        print(f"[{self.name}] {len(events)} events -> {len(uniq)} unique")
        return list(uniq.values())

    @staticmethod
    def _await_decryptor_frame(page, tries: int = 20):
        for _ in range(tries):
            for fr in page.frames:
                try:
                    if fr.evaluate("() => typeof window.createDecryptor") == "function":
                        return fr
                except Exception:
                    continue
            page.wait_for_timeout(1000)
        return None

    def _save_raw(self, events: list[dict]) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{self.name}_decrypted.json"   # fixed name; overwrites each run
        path.write_text(json.dumps(events, ensure_ascii=False, indent=1))
        print(f"[{self.name}] saved {len(events)} decrypted events -> {path}")

    def _map_raw(self, events: list[dict]) -> list[Match]:
        fetched = datetime.now(timezone.utc)
        out: list[Match] = []
        for ev in events:
            odds = self._result_odds(ev)
            if odds is None or not odds.is_valid():
                continue
            out.append(Match(
                bookmaker="totogaming",
                sport=ev.get("SN", ""),
                league=ev.get("CN", ""),
                home_team=ev.get("HT", ""),
                away_team=ev.get("AT", ""),
                start_utc=_parse_dt(ev.get("D")),
                odds=odds,
                source_id=str(ev.get("Id")),
                fetched_at=fetched,
            ))
        return out

    @staticmethod
    def _result_odds(ev: dict) -> Odds | None:
        """Pull W1/X/W2 factors from the 'Result' (Id==1) stake type."""
        for st in ev.get("StakeTypes") or []:
            if st.get("Id") != 1 and st.get("N") != "Result":
                continue
            prices: dict[str, float] = {}
            for stake in st.get("Stakes") or []:
                slot = _SEL.get(stake.get("SN"))
                if slot and stake.get("F") is not None:
                    prices[slot] = float(stake["F"])
            if "home" in prices and "away" in prices:
                return Odds(home=prices["home"],
                            draw=prices.get("draw"),
                            away=prices["away"])
        return None


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
