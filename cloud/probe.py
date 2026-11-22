"""
NURI · probe.py — revisa qué datos ofrece ESPN (estructura, no contenido sensible) para planear mejoras.
Sólo corre si existe cloud/PROBE. Escribe _out/probe.json.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "_out"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (NURI probe)"}
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://sports.core.api.espn.com/v2/sports"
WEB = "https://site.web.api.espn.com/apis/common/v3/sports"


def short(o, n=600):
    return json.dumps(o, ensure_ascii=False)[:n]


async def get(client, url, params=None):
    try:
        r = await client.get(url, params=params)
        return r.status_code, (r.json() if r.status_code == 200 else None)
    except Exception as exc:
        return 0, str(exc)


async def gamelog(client, sport, lg, aid):
    s, j = await get(client, f"{WEB}/{sport}/{lg}/athletes/{aid}/gamelog")
    if not isinstance(j, dict):
        return {"status": s}
    st = (j.get("seasonTypes") or [{}])[0]
    cats = st.get("categories") or []
    ev = (cats[0].get("events") if cats else []) or []
    events = j.get("events") or {}
    return {"status": s, "keys": list(j.keys()), "labels": j.get("labels"), "names": j.get("names"),
            "seasonType": st.get("displayName"), "cats": [[c.get("displayName"), len(c.get("events") or [])] for c in cats],
            "ev_sample": short(ev[:2], 500), "events_n": len(events),
            "event0": short(next(iter(events.values()), None), 500)}


async def league(client, sport, lg):
    d = datetime.now(timezone(timedelta(hours=-6)))
    out = {"days": {}}
    first = None
    for k in range(0, 3):
        ds = (d + timedelta(days=k)).strftime("%Y%m%d")
        s, j = await get(client, f"{SITE}/{sport}/{lg}/scoreboard", {"dates": ds, "limit": 300})
        if not isinstance(j, dict):
            out["days"][ds] = {"status": s}
            continue
        evs = j.get("events") or []
        with_odds = 0
        for e in evs:
            comp = (e.get("competitions") or [{}])[0]
            if comp.get("odds"):
                with_odds += 1
                first = first or e.get("id")
        out["days"][ds] = {"events": len(evs), "with_odds": with_odds}
    if first:
        s, j = await get(client, f"{CORE}/{sport}/leagues/{lg}/events/{first}/competitions/{first}/odds", {"limit": 50})
        if isinstance(j, dict):
            out["providers"] = [((x.get("provider") or {}).get("id"), (x.get("provider") or {}).get("name"),
                                 bool(x.get("bettingOdds")), bool(x.get("propBets"))) for x in j.get("items") or []]
        s2, sm = await get(client, f"{SITE}/{sport}/{lg}/summary", {"event": first})
        if isinstance(sm, dict):
            out["summary_keys"] = list(sm.keys())
            out["predictor"] = short(sm.get("predictor"), 300)
            out["pickcenter"] = [((x.get("provider") or {}).get("name")) for x in sm.get("pickcenter") or []]
    return out


async def main():
    t0 = time.time()
    res = {}
    async with httpx.AsyncClient(timeout=15.0, headers=UA, follow_redirects=True) as client:
        res["gamelog_nfl_rb"] = await gamelog(client, "football", "nfl", "3929630")
        res["gamelog_nfl_qb"] = await gamelog(client, "football", "nfl", "3918298")
        res["gamelog_nba"] = await gamelog(client, "basketball", "nba", "4433249")
        leagues = [("soccer", x) for x in ("eng.2", "ned.1", "por.1", "uefa.europa", "uefa.europa.conf", "arg.1", "bra.1",
                                          "sco.1", "bel.1", "tur.1", "mex.2", "usa.nwsl", "eng.fa", "esp.copa_del_rey",
                                          "conmebol.libertadores", "ger.2", "ita.2", "fra.2", "esp.2")]
        leagues += [("football", "college-football"), ("basketball", "mens-college-basketball"),
                    ("basketball", "wnba"), ("baseball", "mlb"), ("hockey", "nhl")]
        tasks = {f"{s}/{l}": league(client, s, l) for s, l in leagues}
        vals = await asyncio.gather(*tasks.values())
        res["leagues"] = dict(zip(tasks.keys(), vals))
        # props de NBA/NHL/soccer: ¿cuántos traen momio?
        s, sb = await get(client, f"{SITE}/basketball/nba/scoreboard")
        if isinstance(sb, dict) and sb.get("events"):
            eid = sb["events"][0]["id"]
            s, pj = await get(client, f"{CORE}/basketball/leagues/nba/events/{eid}/competitions/{eid}/odds/100/propBets",
                              {"lang": "en", "region": "us", "limit": 1000})
            if isinstance(pj, dict):
                its = pj.get("items") or []
                res["nba_props"] = {"event": eid, "n": len(its), "with_odds": sum(1 for x in its if x.get("odds"))}
    res["seconds"] = round(time.time() - t0, 1)
    (OUT / "probe.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print("[probe] listo", res["seconds"], "s")


if __name__ == "__main__":
    asyncio.run(main())
