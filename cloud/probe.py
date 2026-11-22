"""
NURI · probe.py — estructura de datos para sumar NHL y goleadores de fútbol de DraftKings.
  1) NHL: claves del boxscore de un partido terminado y del historial (gamelog) de un jugador
  2) NHL: cómo vienen los props Más/Menos de DraftKings (tiros, puntos, asistencias)
  3) Fútbol: cómo vienen 'Anytime Goalscorer' y 'To Score 2+ Goals' de DraftKings y el plantel de ESPN
Sólo corre si existe cloud/PROBE. Escribe _out/probe.json (sólo estructura y nombres de campos).
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


async def get(client, url, params=None):
    try:
        r = await client.get(url, params=params)
        return r.status_code, (r.json() if r.status_code == 200 else None)
    except Exception as exc:
        return 0, str(exc)


def strip(it: dict) -> dict:
    """Un item de prop sin URLs largas: sólo la forma."""
    out = {}
    for k, v in it.items():
        if k == "athlete":
            out[k] = (v or {}).get("$ref", "")[-60:]
        elif isinstance(v, dict):
            out[k] = {kk: (vv if not isinstance(vv, (dict, list)) else (json.dumps(vv)[:160])) for kk, vv in v.items()}
        else:
            out[k] = v if not isinstance(v, list) else json.dumps(v)[:160]
    return out


async def scoreboard(client, sp, lg, day_off):
    d = datetime.now(timezone(timedelta(hours=-6))) + timedelta(days=day_off)
    s, j = await get(client, f"{SITE}/{sp}/{lg}/scoreboard", {"dates": d.strftime("%Y%m%d"), "limit": 200})
    return (j or {}).get("events") or [] if isinstance(j, dict) else []


async def main():
    t0 = time.time()
    out: dict = {}
    async with httpx.AsyncClient(timeout=20.0, headers=UA, follow_redirects=True) as client:
        # 1) NHL boxscore y gamelog
        done = [e for e in await scoreboard(client, "hockey", "nhl", -1) if (e.get("status") or {}).get("type", {}).get("state") == "post"]
        if done:
            s, js = await get(client, f"{SITE}/hockey/nhl/summary", {"event": done[0]["id"]})
            box = ((js or {}).get("boxscore") or {}).get("players") or []
            grp = []
            for team in box[:1]:
                for g in team.get("statistics") or []:
                    a0 = (g.get("athletes") or [{}])[0]
                    grp.append({"name": g.get("name"), "keys": g.get("keys"), "labels": g.get("labels"),
                                "sample_stats": a0.get("stats"), "athlete_keys": list(a0.keys()),
                                "athlete_id": (a0.get("athlete") or {}).get("id")})
            out["nhl_box"] = grp
            aid = next((g["athlete_id"] for g in grp if g.get("athlete_id")), None)
            if aid:
                s, gl = await get(client, f"{WEB}/hockey/nhl/athletes/{aid}/gamelog")
                gl = gl or {}
                st = (gl.get("seasonTypes") or [{}])[0]
                cat = (st.get("categories") or [{}])[0]
                ev0 = (cat.get("events") or [{}])[0]
                out["nhl_gamelog"] = {"status": s, "names": gl.get("names"), "labels": gl.get("labels"),
                                      "top_keys": list(gl.keys()), "cat_keys": list(cat.keys()),
                                      "event0": ev0, "n_events": len(cat.get("events") or [])}
        # 2) NHL props O/U
        pre = [e for k in (0, 1) for e in await scoreboard(client, "hockey", "nhl", k)
               if (e.get("status") or {}).get("type", {}).get("state") == "pre"]
        if pre:
            eid = pre[0]["id"]
            s, j = await get(client, f"{CORE}/hockey/leagues/nhl/events/{eid}/competitions/{eid}/odds/100/propBets", {"limit": 1000})
            items = (j or {}).get("items") or []
            out["nhl_props_meta"] = {k: (j or {}).get(k) for k in ("count", "pageIndex", "pageSize", "pageCount")}
            want = ("Total Shots on Goal", "Total Points", "Total Assists", "Shots on Goal Milestones", "Anytime Goalscorer")
            out["nhl_props"] = {w: [strip(x) for x in items if (x.get("type") or {}).get("name") == w][:4] for w in want}
        # 3) Fútbol: goleadores de DraftKings + plantel
        for lg in ("eng.1", "esp.1", "mex.1"):
            evs = [e for k in (0, 1, 2) for e in await scoreboard(client, "soccer", lg, k)
                   if (e.get("status") or {}).get("type", {}).get("state") == "pre"]
            if not evs:
                continue
            e = evs[0]
            eid = e["id"]
            s, j = await get(client, f"{CORE}/soccer/leagues/{lg}/events/{eid}/competitions/{eid}/odds/100/propBets", {"limit": 1000})
            items = (j or {}).get("items") or []
            meta = {k: (j or {}).get(k) for k in ("count", "pageIndex", "pageSize", "pageCount")}
            want = ("Anytime Goalscorer", "To Score 2+ Goals", "First Goalscorer", "Team Total Goals")
            comp = (e.get("competitions") or [{}])[0]
            tid = ((comp.get("competitors") or [{}])[0].get("team") or {}).get("id")
            s2, ro = await get(client, f"{SITE}/soccer/{lg}/teams/{tid}/roster") if tid else (0, None)
            ath = (ro or {}).get("athletes") or []
            out[f"soccer_{lg}"] = {"meta": meta, "props": {w: [strip(x) for x in items if (x.get("type") or {}).get("name") == w][:3] for w in want},
                                   "roster_status": s2, "roster_n": len(ath),
                                   "roster0": {k: ath[0].get(k) for k in ("id", "displayName", "position")} if ath else None,
                                   "roster_keys": list(ath[0].keys())[:30] if ath else None}
    out["seconds"] = round(time.time() - t0, 1)
    (OUT / "probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print("[probe] listo", out["seconds"], "s")


if __name__ == "__main__":
    asyncio.run(main())
