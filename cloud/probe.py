"""
NURI · probe.py — ¿en qué ligas trae ESPN momios reales de Bet365 (apostables en México)?
Sólo corre si existe cloud/PROBE. Escribe _out/probe.json (sólo conteos, nada sensible).
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

SOCCER = ["eng.1", "eng.2", "eng.3", "eng.4", "eng.5", "eng.fa", "eng.league_cup", "sco.1", "sco.2", "esp.1", "esp.2",
          "ita.1", "ita.2", "ger.1", "ger.2", "fra.1", "fra.2", "por.1", "ned.1", "bel.1", "tur.1", "gre.1", "aut.1",
          "sui.1", "den.1", "nor.1", "swe.1", "irl.1", "mex.1", "mex.2", "usa.1", "bra.1", "arg.1", "col.1", "chi.1",
          "jpn.1", "aus.1", "ksa.1", "uefa.champions", "uefa.europa", "uefa.europa.conf", "conmebol.libertadores",
          "conmebol.sudamericana", "concacaf.champions", "fifa.world", "uefa.nations"]


async def get(client, url, params=None):
    try:
        r = await client.get(url, params=params)
        return r.status_code, (r.json() if r.status_code == 200 else None)
    except Exception as exc:
        return 0, str(exc)


async def league(client, lg, sem):
    async with sem:
        d = datetime.now(timezone(timedelta(hours=-6)))
        ids, n = [], 0
        for k in range(0, 4):
            s, j = await get(client, f"{SITE}/soccer/{lg}/scoreboard", {"dates": (d + timedelta(days=k)).strftime("%Y%m%d")})
            for e in (j or {}).get("events") or [] if isinstance(j, dict) else []:
                n += 1
                if (e.get("status") or {}).get("type", {}).get("state") == "pre":
                    ids.append(e["id"])
        b365 = dk = 0
        for eid in ids[:3]:
            s, j = await get(client, f"{CORE}/soccer/leagues/{lg}/events/{eid}/competitions/{eid}/odds", {"limit": 50})
            provs = {(x.get("provider") or {}).get("id") for x in (j or {}).get("items") or []} if isinstance(j, dict) else set()
            b365 += "2000" in provs
            dk += "100" in provs
        return lg, {"events_4d": n, "pre": len(ids), "checked": min(3, len(ids)), "bet365": b365, "dk": dk}


async def main():
    t0 = time.time()
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=15.0, headers=UA, follow_redirects=True) as client:
        res = dict(await asyncio.gather(*(league(client, lg, sem) for lg in SOCCER)))
    out = {"soccer": res, "seconds": round(time.time() - t0, 1)}
    (OUT / "probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("[probe] listo", out["seconds"], "s")


if __name__ == "__main__":
    asyncio.run(main())
