"""
NURI · probe.py — ¿qué más mercado real publica ESPN?
  1) casas con momio en NFL/NBA/NHL/universitario (¿hay alguna apostable en México además de DraftKings?)
  2) qué mercados de Bet365 vienen en fútbol (nombres de los campos, no momios)
  3) dónde hay props de jugadores de DraftKings (NHL, universitario, fútbol)
Sólo corre si existe cloud/PROBE. Escribe _out/probe.json (sólo conteos y nombres, nada sensible).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "_out"
OUT.mkdir(exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (NURI probe)"}
SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://sports.core.api.espn.com/v2/sports"

US = [("football", "nfl"), ("basketball", "nba"), ("hockey", "nhl"), ("football", "college-football"),
      ("basketball", "mens-college-basketball"), ("basketball", "womens-college-basketball")]
B365 = ["eng.1", "ita.1", "ger.1", "fra.1", "uefa.champions", "eng.2", "sco.1"]
PROPS = [("hockey", "nhl"), ("football", "college-football"), ("basketball", "mens-college-basketball"),
         ("soccer", "eng.1"), ("soccer", "esp.1"), ("soccer", "mex.1"), ("soccer", "uefa.champions"),
         ("soccer", "ita.1"), ("soccer", "ger.1"), ("football", "nfl"), ("basketball", "nba")]


async def get(client, url, params=None):
    try:
        r = await client.get(url, params=params)
        return r.status_code, (r.json() if r.status_code == 200 else None)
    except Exception as exc:
        return 0, str(exc)


async def pre_events(client, sp, lg, days=3, cap=4):
    d = datetime.now(timezone(timedelta(hours=-6)))
    ids = []
    for k in range(days):
        s, j = await get(client, f"{SITE}/{sp}/{lg}/scoreboard", {"dates": (d + timedelta(days=k)).strftime("%Y%m%d"), "limit": 200})
        for e in (j or {}).get("events") or [] if isinstance(j, dict) else []:
            if (e.get("status") or {}).get("type", {}).get("state") == "pre":
                ids.append(e["id"])
        if len(ids) >= cap:
            break
    return ids[:cap]


async def odds_items(client, sp, lg, eid):
    s, j = await get(client, f"{CORE}/{sp}/leagues/{lg}/events/{eid}/competitions/{eid}/odds", {"limit": 50})
    return (j or {}).get("items") or [] if isinstance(j, dict) else []


async def us_league(client, sp, lg, sem):
    async with sem:
        ids = await pre_events(client, sp, lg)
        provs: Counter = Counter()
        names: dict = {}
        keys: dict = {}
        for eid in ids:
            for it in await odds_items(client, sp, lg, eid):
                p = it.get("provider") or {}
                pid = str(p.get("id"))
                provs[pid] += 1
                names[pid] = p.get("name")
                keys.setdefault(pid, set()).update(k for k in it.keys() if not k.startswith("$"))
                bo = it.get("bettingOdds") or {}
                if bo:
                    keys[pid].update("bo." + k for k in (bo.get("teamOdds") or {}).keys())
        return f"{sp}/{lg}", {"events": len(ids), "providers": {k: [names[k], v, sorted(keys[k])[:60]] for k, v in provs.items()}}


async def b365_league(client, lg, sem):
    async with sem:
        ids = await pre_events(client, "soccer", lg)
        team: Counter = Counter()
        player: Counter = Counter()
        n = 0
        for eid in ids:
            for it in await odds_items(client, "soccer", lg, eid):
                if str((it.get("provider") or {}).get("id")) != "2000":
                    continue
                n += 1
                bo = it.get("bettingOdds") or {}
                team.update((bo.get("teamOdds") or {}).keys())
                for k, v in (bo.get("playerOdds") or {}).items():
                    player[f"{k}({len(v) if isinstance(v, list) else '?'})"] += 1
                for k in bo.keys():
                    if k not in ("teamOdds", "playerOdds"):
                        team["<" + k + ">"] += 1
        return lg, {"events": len(ids), "b365": n, "teamOdds": dict(team), "playerOdds": dict(player)}


async def props_league(client, sp, lg, sem):
    async with sem:
        ids = await pre_events(client, sp, lg, cap=2)
        types: Counter = Counter()
        n = 0
        for eid in ids:
            s, j = await get(client, f"{CORE}/{sp}/leagues/{lg}/events/{eid}/competitions/{eid}/odds/100/propBets", {"limit": 1000})
            items = (j or {}).get("items") or [] if isinstance(j, dict) else []
            n += len(items)
            types.update(((x.get("type") or {}).get("name") or "?") for x in items)
        return f"{sp}/{lg}", {"events": len(ids), "items": n, "types": dict(types.most_common(40))}


async def main():
    t0 = time.time()
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=20.0, headers=UA, follow_redirects=True) as client:
        us = dict(await asyncio.gather(*(us_league(client, sp, lg, sem) for sp, lg in US)))
        b3 = dict(await asyncio.gather(*(b365_league(client, lg, sem) for lg in B365)))
        pr = dict(await asyncio.gather(*(props_league(client, sp, lg, sem) for sp, lg in PROPS)))
    out = {"us_providers": us, "bet365_soccer": b3, "dk_props": pr, "seconds": round(time.time() - t0, 1)}
    (OUT / "probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("[probe] listo", out["seconds"], "s")


if __name__ == "__main__":
    asyncio.run(main())
