"""
NURI · 누리 — espn_facts.py
===========================
Estadísticas REALES de cada partido, sacadas del resumen público de ESPN (sin llaves):

    · récord, posición en la tabla y racha
    · últimos 5 partidos de cada equipo (con marcador y rival)
    · promedios de la temporada (anota / recibe, tiros, yardas, % de tiro…)
    · bajas confirmadas (lesionados, suspendidos) con su estado
    · líderes de cada equipo (goleador, máximo anotador, QB…)
    · predictor de ESPN (NBA / NFL) y enfrentamientos directos
    · momios REALES de Bet365 que ESPN publica para el fútbol (1X2, total de goles y
      goleador en cualquier momento)

Todo se convierte a español y a datos que la interfaz muestra tal cual.
"""
from __future__ import annotations

import re
import time
import unicodedata

# ----------------------------------------------------------------------------- utilidades


def f_num(v) -> float | None:
    """Número desde '2,223', '46', '.898', '+11', 26.6 → float."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("%", "").strip()
    m = re.match(r"^[+-]?(\d+\.?\d*|\.\d+)", s)
    return float(m.group(0)) if m else None


def frac_to_decimal(v) -> float | None:
    """Momio fraccional de Bet365 ('531/1000', '4/1', 'EVS') → decimal (1.531, 5.0, 2.0)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 1.0 else None
    s = str(v).strip().upper()
    if s in ("EVS", "EVEN", "EVENS", "EV"):
        return 2.0
    if "/" in s:
        try:
            a, b = s.split("/", 1)
            a, b = float(a), float(b)
        except ValueError:
            return None
        if b <= 0 or a <= 0:
            return None
        return round(1.0 + a / b, 4)
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 1.0 else None


def norm_person(name: str) -> str:
    """'Luis Suárez' y 'Luis Suarez' → 'luis suarez'."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def _sid(x) -> str:
    return str(x) if x is not None else ""


# ----------------------------------------------------------------------------- traducciones

RESULT_ES = {"W": "G", "L": "P", "D": "E", "T": "E"}

INJ_LABEL = {"out": "Fuera", "doubtful": "Duda fuerte", "questionable": "En duda", "probable": "Probable",
             "suspended": "Suspendido", "active": "Disponible"}

BODY_ES = {
    "ankle": "tobillo", "knee": "rodilla", "hamstring": "isquiotibial", "calf": "pantorrilla", "foot": "pie",
    "back": "espalda", "hip": "cadera", "shoulder": "hombro", "groin": "ingle", "illness": "enfermedad",
    "concussion": "conmoción", "wrist": "muñeca", "hand": "mano", "thigh": "muslo", "achilles": "Aquiles",
    "elbow": "codo", "neck": "cuello", "rest": "descanso", "personal": "motivos personales", "toe": "dedo del pie",
    "heel": "talón", "oblique": "oblicuo", "quadriceps": "cuádriceps", "quad": "cuádriceps", "abdomen": "abdomen",
    "chest": "pecho", "rib": "costilla", "ribs": "costillas", "head": "cabeza", "face": "cara", "eye": "ojo",
    "finger": "dedo", "thumb": "pulgar", "shin": "espinilla", "leg": "pierna", "arm": "brazo",
    "lower body": "tren inferior", "upper body": "tren superior", "undisclosed": "no revelada",
    "not injury related": "no es lesión", "lower leg": "pierna", "forearm": "antebrazo", "pectoral": "pectoral",
    "jaw": "mandíbula", "nose": "nariz", "spine": "columna", "pelvis": "pelvis", "adductor": "aductor",
    "plantar fasciitis": "fascitis plantar", "suspension": "suspensión", "ill": "enfermedad",
    "bruise": "golpe", "surgery": "cirugía", "sprain": "esguince", "strain": "distensión", "fracture": "fractura",
    "soreness": "molestia", "tear": "desgarre", "torn": "desgarre", "tightness": "rigidez", "contusion": "contusión",
    "inflammation": "inflamación", "dislocation": "luxación", "tendinitis": "tendinitis", "spasms": "espasmos",
    "torso": "torso", "kneecap": "rótula", "acl": "ligamento cruzado", "mcl": "ligamento colateral",
}

GROUP_WORDS = [("Southeast", "Sureste"), ("Southwest", "Suroeste"), ("Northwest", "Noroeste"),
               ("Northeast", "Noreste"), ("Metropolitan", "Metropolitana"), ("Atlantic", "Atlántico"),
               ("Pacific", "Pacífico"), ("Central", "Central"), ("East", "Este"), ("West", "Oeste"),
               ("North", "Norte"), ("South", "Sur"), ("Eastern", "Este"), ("Western", "Oeste")]

# (nombre ESPN, etiqueta, 1 = más es mejor / -1 = menos es mejor, decimales)
SEASON_ROWS = {
    "basketball": [("avgPoints", "Puntos por partido", 1, 1), ("avgPointsAgainst", "Puntos que recibe", -1, 1),
                   ("fieldGoalPct", "% de tiros de campo", 1, 1), ("threePointFieldGoalPct", "% de triples", 1, 1),
                   ("avgRebounds", "Rebotes por partido", 1, 1), ("avgAssists", "Asistencias por partido", 1, 1),
                   ("avgTotalTurnovers", "Pérdidas por partido", -1, 1)],
    "americanfootball": [("totalPointsPerGame", "Puntos por partido", 1, 1),
                         ("totalPointsPerGameAllowed", "Puntos que permite", -1, 1),
                         ("yardsPerGame", "Yardas por partido", 1, 0),
                         ("yardsPerGameAllowed", "Yardas que permite", -1, 0),
                         ("passingYardsPerGame", "Yardas por pase", 1, 0),
                         ("rushingYardsPerGame", "Yardas por tierra", 1, 0),
                         ("passingYardsPerGameAllowed", "Yardas por pase que permite", -1, 0),
                         ("rushingYardsPerGameAllowed", "Yardas por tierra que permite", -1, 0)],
    "hockey": [("avgGoals", "Goles por partido", 1, 2), ("avgGoalsAgainst", "Goles que recibe", -1, 2),
               ("avgShots", "Tiros por partido", 1, 1), ("avgShotsAgainst", "Tiros que permite", -1, 1),
               ("powerPlayPct", "% en power play", 1, 1), ("penaltyKillPct", "% en penalty kill", 1, 1)],
}

LEADER_CATS = {
    "basketball": {"pointsPerGame": "Puntos", "reboundsPerGame": "Rebotes", "assistsPerGame": "Asistencias"},
    "americanfootball": {"passingYards": "Pase", "rushingYards": "Carrera", "receivingYards": "Recepción",
                         "sacks": "Capturas"},
    "hockey": {"goals": "Goles", "assists": "Asistencias", "points": "Puntos"},
    "football": {"goalsLeaders": "Goles", "assistsLeaders": "Asistencias"},
    "baseball": {},
}


def body_es(part: str | None) -> str:
    if not part:
        return ""
    return BODY_ES.get(part.strip().lower(), part.strip().lower())


def inj_code(status: str) -> str:
    s = (status or "").lower()
    if "suspen" in s:
        return "suspended"
    if "out" in s or "injured reserve" in s or s == "ir":
        return "out"
    if "doubt" in s:
        return "doubtful"
    if "quest" in s or "day" in s:
        return "questionable"
    if "prob" in s:
        return "probable"
    return "active"


def group_es(header: str) -> str:
    h = re.sub(r"^\s*\d{4}(-\d{2})?\s+", "", header or "")
    h = re.sub(r"\b(Standings|Division)\b", "", h).strip()
    h = h.replace("Conference", "Conferencia")
    for en, es in GROUP_WORDS:
        h = re.sub(rf"\b{en}\b", es, h)
    return re.sub(r"\s+", " ", h).strip()


# ----------------------------------------------------------------------------- partes del resumen


def _sides(home_id: str, away_id: str) -> dict[str, str]:
    return {_sid(home_id): "home", _sid(away_id): "away"}


def parse_form(js: dict, side_of: dict) -> dict:
    out: dict[str, list] = {"home": [], "away": []}
    for t in js.get("lastFiveGames") or []:
        tid = _sid((t.get("team") or {}).get("id"))
        side = side_of.get(tid)
        if not side:
            continue
        for e in t.get("events") or []:
            hs, as_ = f_num(e.get("homeTeamScore")), f_num(e.get("awayTeamScore"))
            is_home = _sid(e.get("homeTeamId")) == tid
            own, opp = (hs, as_) if is_home else (as_, hs)
            res = (e.get("gameResult") or "").upper()[:1]
            out[side].append({
                "r": RESULT_ES.get(res, "?"),
                "own": int(own) if own is not None else None, "opp": int(opp) if opp is not None else None,
                "vs": ((e.get("opponent") or {}).get("abbreviation") or "")[:5],
                "vs_name": (e.get("opponent") or {}).get("displayName") or "",
                "home": is_home, "date": (e.get("gameDate") or "")[:10],
                "comp": e.get("leagueAbbreviation") or "",
            })
        out[side].sort(key=lambda x: x["date"])
    return out


def parse_records(js: dict, side_of: dict) -> dict:
    rec: dict[str, dict] = {"home": {}, "away": {}}
    comps = (((js.get("header") or {}).get("competitions") or [{}])[0] or {}).get("competitors") or []
    for c in comps:
        side = side_of.get(_sid(c.get("id") or (c.get("team") or {}).get("id")))
        if not side:
            continue
        for r in c.get("record") or []:
            if r.get("type"):
                rec[side][r["type"]] = r.get("summary") or r.get("displayValue")
        if c.get("form"):
            rec[side]["form"] = c["form"]
    return rec


def parse_standings(js: dict, side_of: dict) -> dict:
    out: dict[str, dict] = {}
    for g in ((js.get("standings") or {}).get("groups") or []):
        entries = (g.get("standings") or {}).get("entries") or []
        for i, e in enumerate(entries):
            side = side_of.get(_sid(e.get("id")))
            if not side:
                continue
            st = {s.get("name"): s for s in e.get("stats") or [] if isinstance(s, dict)}

            def v(name):
                s_ = st.get(name) or {}
                return s_.get("value") if s_.get("value") is not None else f_num(s_.get("displayValue"))

            rank = v("rank") or (i + 1)
            out[side] = {
                "rank": int(rank), "of": len(entries),
                "group": group_es(g.get("header") or g.get("divisionHeader") or ""),
                "points": int(v("points")) if v("points") is not None else None,
                "gp": int(v("gamesPlayed")) if v("gamesPlayed") else None,
                "overall": (st.get("overall") or {}).get("displayValue"),
                "wins": v("wins"), "losses": v("losses"), "ties": v("ties"), "otl": v("otLosses"),
            }
    return out


def _games_played(fam: str, rec: dict, stand: dict) -> int | None:
    if stand.get("gp"):
        return int(stand["gp"])
    total = rec.get("total") or stand.get("overall")
    if total:
        nums = [int(x) for x in re.findall(r"\d+", str(total))]
        if nums:
            return sum(nums)
    w, l_, t = stand.get("wins"), stand.get("losses"), stand.get("ties")
    if w is not None and l_ is not None:
        return int(w + l_ + (t or 0) + (stand.get("otl") or 0))
    return None


def parse_season(js: dict, side_of: dict) -> dict:
    raw: dict[str, dict] = {"home": {}, "away": {}}
    for t in ((js.get("boxscore") or {}).get("teams") or []):
        side = side_of.get(_sid((t.get("team") or {}).get("id"))) or t.get("homeAway")
        if side not in raw:
            continue
        for s in t.get("statistics") or []:
            if isinstance(s, dict) and s.get("name"):
                raw[side][s["name"]] = s.get("displayValue")
    return raw


def season_rows(fam: str, raw: dict, gp: dict) -> list[dict]:
    rows = []
    if fam == "football":
        spec = []
        h_gp, a_gp = gp.get("home"), gp.get("away")
        for key, label, better in (("totalGoals", "Goles a favor por partido", 1),
                                   ("goalsConceded", "Goles en contra por partido", -1)):
            h, a = f_num(raw["home"].get(key)), f_num(raw["away"].get(key))
            if h is None or a is None:
                continue
            if h_gp and a_gp:
                spec.append((label, round(h / h_gp, 2), round(a / a_gp, 2), better))
            else:
                spec.append((label.replace(" por partido", " (temporada)"), h, a, better))
        h, a = f_num(raw["home"].get("goalDifference")), f_num(raw["away"].get("goalDifference"))
        if h is not None and a is not None:
            spec.append(("Diferencia de goles", h, a, 1))
        for label, h, a, better in spec:
            rows.append({"label": label, "home": h, "away": a,
                         "better": None if h == a else ("home" if (h > a) == (better > 0) else "away")})
        return rows
    for key, label, better, dec in SEASON_ROWS.get(fam, []):
        h, a = f_num(raw["home"].get(key)), f_num(raw["away"].get(key))
        if h is None or a is None:
            continue
        h, a = round(h, dec), round(a, dec)
        if dec == 0:
            h, a = int(h), int(a)
        rows.append({"label": label, "home": h, "away": a,
                     "better": None if h == a else ("home" if (h > a) == (better > 0) else "away")})
    return rows


def parse_injuries(js: dict, side_of: dict) -> tuple[dict, dict]:
    out: dict[str, list] = {"home": [], "away": []}
    status: dict[str, str] = {}
    for t in js.get("injuries") or []:
        side = side_of.get(_sid((t.get("team") or {}).get("id")))
        for i in t.get("injuries") or []:
            ath = i.get("athlete") or {}
            name = ath.get("displayName")
            if not name:
                continue
            raw_status = i.get("status") or (i.get("type") or {}).get("description") or ""
            code = inj_code(raw_status)
            status[name] = code if code != "suspended" else "out"
            if not side:
                continue
            det = i.get("details") or {}
            part = body_es(det.get("type"))
            if det.get("detail") and str(det.get("detail")).lower() not in ("not specified",):
                extra = body_es(det.get("detail"))
                if extra and extra not in part:
                    part = f"{part} ({extra})" if part else extra
            out[side].append({"name": name, "pos": (ath.get("position") or {}).get("abbreviation") or "",
                              "code": code, "status": INJ_LABEL.get(code, raw_status), "part": part,
                              "ret": (det.get("returnDate") or "")[:10] or None})
    order = {"out": 0, "suspended": 0, "doubtful": 1, "questionable": 2, "probable": 3, "active": 4}
    for side in out:
        out[side].sort(key=lambda x: order.get(x["code"], 5))
    return out, status


def parse_leaders(js: dict, side_of: dict, fam: str) -> dict:
    """Líderes por equipo y categoría: [{name, id, value, display, goals, apps}]."""
    raw: dict[str, dict] = {"home": {}, "away": {}}
    cats = LEADER_CATS.get(fam, {})
    for t in js.get("leaders") or []:
        side = side_of.get(_sid((t.get("team") or {}).get("id")))
        if not side:
            continue
        for c in t.get("leaders") or []:
            cname = c.get("name")
            if cname not in cats:
                continue
            lst = []
            for l_ in (c.get("leaders") or [])[:3]:
                ath = l_.get("athlete") or {}
                if not ath.get("displayName"):
                    continue
                stats_ = {s.get("name"): s.get("value") for s in (l_.get("statistics") or []) if isinstance(s, dict)}
                disp = l_.get("displayValue") or ""
                value = f_num(l_.get("value"))
                if value is None:
                    value = f_num((l_.get("mainStat") or {}).get("value"))
                if value is None:
                    m = re.search(r"([\d,]+)\s*YDS", disp)
                    value = f_num(m.group(1)) if m else f_num(disp)
                apps = f_num(stats_.get("appearances"))
                if apps is None:
                    m = re.search(r"Matches:\s*(\d+)", disp)
                    apps = float(m.group(1)) if m else None
                goals = f_num(stats_.get("totalGoals"))
                if goals is None and cname == "goalsLeaders":
                    m = re.search(r"Goals:\s*(\d+)", disp)
                    goals = float(m.group(1)) if m else value
                td = None
                m = re.search(r"(\d+)\s*TD", disp)
                if m:
                    td = int(m.group(1))
                lst.append({"name": ath["displayName"], "id": _sid(ath.get("id")),
                            "pos": (ath.get("position") or {}).get("abbreviation") or "",
                            "value": value, "display": disp, "apps": apps, "goals": goals, "td": td})
            if lst:
                raw[side][cname] = lst
    return raw


def leaders_view(fam: str, raw: dict, gp: dict) -> dict:
    out: dict[str, list] = {"home": [], "away": []}
    cats = LEADER_CATS.get(fam, {})
    for side in ("home", "away"):
        for cname, label in cats.items():
            lst = raw[side].get(cname) or []
            if not lst:
                continue
            top = lst[0]
            v = top.get("value")
            if fam == "basketball":
                txt = f"{v:.1f} por partido" if v is not None else top["display"]
            elif fam == "americanfootball":
                g = gp.get(side)
                if cname == "sacks":
                    txt = f"{v:g} en la temporada" if v is not None else top["display"]
                elif v is not None and g:
                    txt = f"{v / g:.0f} yardas por partido" + (f" · {top['td']} TD" if top.get("td") is not None else "")
                else:
                    txt = top["display"]
            elif fam == "football":
                if cname == "goalsLeaders" and top.get("goals") is not None and top.get("apps"):
                    txt = f"{int(top['goals'])} goles en {int(top['apps'])} partidos"
                elif top.get("value") is not None and top.get("apps"):
                    txt = f"{int(top['value'])} en {int(top['apps'])} partidos"
                else:
                    txt = top["display"]
            else:
                txt = f"{int(v)} en la temporada" if v is not None else top["display"]
            out[side].append({"cat": label, "name": top["name"], "val": txt})
    return out


def parse_predictor(js: dict, home_id: str, away_id: str) -> dict | None:
    p = js.get("predictor") or {}
    if not p:
        return None
    vals = {}
    for k in ("homeTeam", "awayTeam"):
        t = p.get(k) or {}
        side = "home" if _sid(t.get("id")) == _sid(home_id) else "away" if _sid(t.get("id")) == _sid(away_id) else (
            "home" if k == "homeTeam" else "away")
        v = f_num(t.get("gameProjection"))
        if v is not None:
            vals[side] = v
    if "home" in vals and "away" not in vals:
        vals["away"] = round(100 - vals["home"], 1)
    if "away" in vals and "home" not in vals:
        vals["home"] = round(100 - vals["away"], 1)
    return vals if len(vals) == 2 else None


def parse_h2h(js: dict, home_id: str, away_id: str, home_abbr: str, away_abbr: str) -> dict | None:
    series = [s for s in (js.get("seasonseries") or []) if isinstance(s, dict)]
    if not series:
        return None
    s = series[0]
    games, hw, aw, dr = [], 0, 0, 0
    for e in s.get("events") or []:
        if (e.get("status") or (e.get("statusType") or {}).get("state")) != "post":
            continue
        comp = {}
        for c in e.get("competitors") or []:
            tid = _sid((c.get("team") or {}).get("id") or c.get("id"))
            sc = c.get("score")
            if isinstance(sc, dict):
                sc = sc.get("displayValue") or sc.get("value")
            comp[tid] = {"score": f_num(sc), "home": c.get("homeAway") == "home",
                         "abbr": (c.get("team") or {}).get("abbreviation") or ""}
        h, a = comp.get(_sid(home_id)), comp.get(_sid(away_id))
        if not h or not a or h["score"] is None or a["score"] is None:
            continue
        if h["score"] > a["score"]:
            hw += 1
            w = "home"
        elif a["score"] > h["score"]:
            aw += 1
            w = "away"
        else:
            dr += 1
            w = "draw"
        loc = h if h["home"] else a
        other = a if h["home"] else h
        games.append({"date": (e.get("date") or "")[:10], "local": loc["abbr"], "visita": other["abbr"],
                      "score": f"{int(loc['score'])}-{int(other['score'])}", "winner": w})
    if not games:
        return None
    title = "Serie de la temporada" if s.get("type") == "season" else f"Últimos {len(games)} enfrentamientos"
    summary = f"{home_abbr} {hw} · {away_abbr} {aw}" + (f" · empates {dr}" if dr else "")
    return {"title": title, "summary": summary, "home_wins": hw, "away_wins": aw, "draws": dr, "games": games[:5]}


def parse_goalies(js: dict, home_id: str, away_id: str) -> dict:
    out = {}
    g = js.get("goalies") or {}
    for k, side in (("homeTeam", "home"), ("awayTeam", "away")):
        t = g.get(k) or {}
        if t.get("teamId") and _sid(t.get("teamId")) not in (_sid(home_id), _sid(away_id)):
            continue
        ath = (t.get("athletes") or [None])[0]
        if not ath:
            continue
        st = {s.get("name"): s.get("displayValue") for s in ath.get("statistics") or [] if isinstance(s, dict)}
        out[side] = {"name": ath.get("displayName"), "gaa": st.get("avgGoalsAgainst"), "sv": st.get("savePct"),
                     "gp": st.get("games")}
    return out


def parse_starters(js: dict, side_of: dict) -> dict:
    out: dict[str, list] = {"home": [], "away": []}
    for r in js.get("rosters") or []:
        side = side_of.get(_sid((r.get("team") or {}).get("id"))) or r.get("homeAway")
        if side not in out:
            continue
        out[side] = [(p.get("athlete") or {}).get("displayName") for p in r.get("roster") or []
                     if p.get("starter") and (p.get("athlete") or {}).get("displayName")]
    return out


def parse_bet365(js: dict, home_id: str, away_id: str, live: bool = False) -> dict | None:
    """Momios reales de Bet365 que ESPN publica (fútbol): 1X2, línea de goles y goleador."""
    for o in js.get("odds") or []:
        if not isinstance(o, dict):
            continue
        prov = o.get("provider") or {}
        if _sid(prov.get("id")) != "2000" and "365" not in (prov.get("name") or ""):
            continue
        bo = o.get("bettingOdds") or {}
        tm = bo.get("teamOdds") or {}
        pre = "live" if live else "preMatch"

        def g(k):
            return frac_to_decimal((tm.get(pre + k) or {}).get("value"))

        out: dict = {"ts": time.time()}
        h, d, a = g("FullTimeResultHome"), g("FullTimeResultDraw"), g("FullTimeResultAway")
        if not live and not (h and d and a):
            h = f_num(((o.get("homeTeamOdds") or {}).get("odds") or {}).get("value"))
            a = f_num(((o.get("awayTeamOdds") or {}).get("odds") or {}).get("value"))
            d = f_num((o.get("drawOdds") or {}).get("value"))
        hto = ((o.get("homeTeamOdds") or {}).get("team") or {}).get("id")
        if hto is not None and _sid(hto) == _sid(away_id):
            h, a = a, h  # ESPN invirtió local/visita en el bloque de Bet365
        if h and d and a and h > 1 and d > 1 and a > 1:
            over = 1 / h + 1 / d + 1 / a
            if 0.99 <= over <= 1.25:
                out["1x2"] = {"home": round(h, 3), "draw": round(d, 3), "away": round(a, 3)}
        dc = {"1x": g("DoubleChanceHomeOrDraw"), "x2": g("DoubleChanceDrawOrAway"), "12": g("DoubleChanceHomeOrAway")}
        if hto is not None and _sid(hto) == _sid(away_id):
            dc["1x"], dc["x2"] = dc["x2"], dc["1x"]
        if all(v and 1.0 < v < 10 for v in dc.values()):
            out["dc"] = {k: round(v, 3) for k, v in dc.items()}
        line = f_num((tm.get(pre + "OverUnderHandicap") or {}).get("value"))
        ov, un = g("GoalLineOver"), g("GoalLineUnder")
        if line and ov and un and 0.99 <= 1 / ov + 1 / un <= 1.2:
            out["total"] = {"line": line, "over": round(ov, 3), "under": round(un, 3)}
        scorers = {}
        for p in (bo.get("playerOdds") or {}).get(pre + "AnyTimeGoalScorer") or []:
            v = frac_to_decimal(p.get("value"))
            nm = p.get("player")
            if v and nm:
                scorers[norm_person(nm)] = {"name": nm, "odds": round(v, 3)}
        if scorers:
            out["scorers"] = scorers
        return out if len(out) > 1 else None
    return None


def find_scorer_odds(scorers: dict, name: str) -> float | None:
    """Empata 'Luis Suárez' con 'Luis Suarez' o 'L. Suarez'."""
    if not scorers or not name:
        return None
    n = norm_person(name)
    if n in scorers:
        return scorers[n]["odds"]
    parts = n.split()
    if not parts:
        return None
    last = parts[-1]
    cands = [v for k, v in scorers.items() if k.split() and k.split()[-1] == last
             and (k.split()[0][:1] == parts[0][:1] or len(k.split()) == 1 or len(parts) == 1)]
    if len(cands) == 1:
        return cands[0]["odds"]
    return None


# ----------------------------------------------------------------------------- dossier completo


def build_dossier(js: dict, fam: str, league: str, home: dict, away: dict, home_id: str, away_id: str,
                  live: bool = False) -> dict:
    """Junta todo lo real del partido. Las claves con '_' son para el modelo (props), el resto
    se muestra en la interfaz."""
    side_of = _sides(home_id, away_id)
    form = parse_form(js, side_of)
    rec = parse_records(js, side_of)
    stand = parse_standings(js, side_of)
    gp = {s: _games_played(fam, rec.get(s, {}), stand.get(s, {})) for s in ("home", "away")}
    season_raw = parse_season(js, side_of) if not live else {"home": {}, "away": {}}
    lead_raw = parse_leaders(js, side_of, fam)
    if fam == "football":
        for s in ("home", "away"):  # sin tabla (liguilla / playoffs): usa las apariciones del líder
            if not gp.get(s):
                apps = [p.get("apps") or 0 for p in (lead_raw[s].get("goalsLeaders") or [])]
                gp[s] = int(max(apps)) if apps and max(apps) > 0 else None
    injuries, inj_status = parse_injuries(js, side_of)
    pred = parse_predictor(js, home_id, away_id)
    h2h = parse_h2h(js, home_id, away_id, home.get("short") or "LOC", away.get("short") or "VIS")
    goalies = parse_goalies(js, home_id, away_id) if fam == "hockey" else {}
    starters = parse_starters(js, side_of) if fam == "football" else {"home": [], "away": []}

    teams = {}
    for s, t in (("home", home), ("away", away)):
        r = rec.get(s, {})
        st = stand.get(s, {})
        f = form.get(s, [])
        streak = season_raw.get(s, {}).get("streak")
        last10 = season_raw.get(s, {}).get("Last Ten Games")
        teams[s] = {
            "name": t.get("name"), "short": t.get("short"), "logo": t.get("logo"),
            "record": r.get("total") or st.get("overall"),
            "split": r.get("home") if s == "home" else r.get("road"),
            "points": st.get("points") if st.get("points") is not None else (f_num(r.get("points")) if r.get("points") else None),
            "rank": st.get("rank"), "of": st.get("of"), "group": league if fam == "football" else (st.get("group") or ""),
            "form": f[-5:], "streak": streak, "last10": last10, "gp": gp.get(s),
            "injuries": injuries.get(s, [])[:8], "leaders": [],
            "goalie": goalies.get(s),
            "lineup": starters.get(s) or [],
        }
    lv = leaders_view(fam, lead_raw, gp)
    for s in ("home", "away"):
        teams[s]["leaders"] = lv[s]
    d = {
        "updated": time.time(), "sport": fam, "league": league,
        "home": teams["home"], "away": teams["away"],
        "season": season_rows(fam, season_raw, gp) if not live else [],
        "predictor": pred, "h2h": h2h,
        "_gp": gp, "_season_raw": season_raw, "_leaders_raw": lead_raw, "_inj_status": inj_status,
        "_starters": starters,
    }
    d["bullets"] = make_bullets(d, fam)
    return d


def merge_live(old: dict | None, new: dict) -> dict:
    """En vivo ESPN ya no manda los promedios de temporada: conservamos los del pre-partido."""
    if not old:
        return new
    keep = dict(new)
    for k in ("season", "_season_raw", "predictor", "h2h"):
        if not keep.get(k) or (k == "_season_raw" and not any(keep[k].values())):
            keep[k] = old.get(k)
    for s in ("home", "away"):
        t = dict(keep[s])
        for k in ("form", "record", "rank", "points", "leaders", "streak", "last10", "group", "of"):
            if not t.get(k) and old.get(s, {}).get(k):
                t[k] = old[s][k]
        keep[s] = t
    keep["bullets"] = old.get("bullets") or keep.get("bullets")
    return keep


# ----------------------------------------------------------------------------- frases clave


def _streak(form: list) -> tuple[str, int]:
    if not form:
        return "", 0
    last = form[-1]["r"]
    n = 0
    for g in reversed(form):
        if g["r"] != last:
            break
        n += 1
    return last, n


def make_bullets(d: dict, fam: str) -> list[str]:
    """Hasta 5 datos reales, neutrales y cortos, que explican el partido."""
    scored: list[tuple[float, str]] = []
    h, a = d["home"], d["away"]
    word = {"G": ("victoria", "victorias"), "P": ("derrota", "derrotas"), "E": ("empate", "empates")}
    for t in (h, a):
        f = t.get("form") or []
        if len(f) >= 3:
            r, n = _streak(f)
            wins = sum(1 for x in f if x["r"] == "G")
            draws = sum(1 for x in f if x["r"] == "E")
            losses = sum(1 for x in f if x["r"] == "P")
            if n >= 3 and r in word:
                scored.append((3.0 + n * 0.2, f"{t['name']} llega con {n} {word[r][1]} seguidas"))
            elif fam == "football":
                scored.append((1.5, f"{t['name']}: {wins}G {draws}E {losses}P en sus últimos {len(f)}"))
            else:
                scored.append((1.4, f"{t['name']}: {wins}-{losses} en sus últimos {len(f)}"))
        if t.get("last10") and fam == "basketball":
            scored.append((1.2, f"{t['name']}: {t['last10']} en sus últimos 10"))
    # bajas clave: un líder del equipo fuera pesa mucho
    for t in (h, a):
        leaders = {x["name"] for x in t.get("leaders") or []}
        outs = [i for i in t.get("injuries") or [] if i["code"] in ("out", "suspended", "doubtful")]
        key = [i for i in outs if i["name"] in leaders]
        if key:
            i = key[0]
            scored.append((4.5, f"{t['name']} sin {i['name']} ({i['status'].lower()}{', ' + i['part'] if i['part'] else ''})"))
        elif outs:
            names = ", ".join(i["name"] for i in outs[:4])
            more = f" y {len(outs) - 4} más" if len(outs) > 4 else ""
            scored.append((2.6, f"Bajas de {t['name']}: {names}{more}"))
    # tabla
    if fam == "football" and h.get("rank") and a.get("rank"):
        pts_h = f" ({h['points']} pts)" if h.get("points") is not None else ""
        pts_a = f" ({a['points']} pts)" if a.get("points") is not None else ""
        scored.append((2.2, f"Tabla: {h['name']} {h['rank']}.º{pts_h} · {a['name']} {a['rank']}.º{pts_a}"))
    elif h.get("record") and a.get("record"):
        scored.append((1.0, f"Récord: {h['name']} {h['record']} · {a['name']} {a['record']}"))
    # ataque contra defensa
    rows = {r["label"]: r for r in d.get("season") or []}
    if fam == "football":
        gf, gc = rows.get("Goles a favor por partido"), rows.get("Goles en contra por partido")
        if gf and gc:
            scored.append((2.0, f"{h['name']} anota {gf['home']} y recibe {gc['home']} por partido; "
                                f"{a['name']} anota {gf['away']} y recibe {gc['away']}"))
    elif fam in ("basketball", "americanfootball"):
        key_for = "Puntos por partido"
        key_vs = "Puntos que recibe" if fam == "basketball" else "Puntos que permite"
        pf, pa = rows.get(key_for), rows.get(key_vs)
        if pf and pa:
            scored.append((2.0, f"{h['name']} anota {pf['home']} y permite {pa['home']}; "
                                f"{a['name']} anota {pf['away']} y permite {pa['away']}"))
    elif fam == "hockey":
        gf, gc = rows.get("Goles por partido"), rows.get("Goles que recibe")
        if gf and gc:
            scored.append((2.0, f"{h['name']} {gf['home']} goles a favor y {gc['home']} en contra por partido; "
                                f"{a['name']} {gf['away']} y {gc['away']}"))
        gh, ga = h.get("goalie"), a.get("goalie")
        if gh and ga and gh.get("sv") and ga.get("sv"):
            scored.append((1.3, f"Porteros: {gh['name']} ({gh['sv']} de efectividad) vs {ga['name']} ({ga['sv']})"))
    # predictor ESPN
    p = d.get("predictor")
    if p:
        fav = h if p["home"] >= p["away"] else a
        scored.append((2.4, f"El predictor de ESPN le da {max(p['home'], p['away']):.0f} % a {fav['name']}"))
    # cara a cara
    hh = d.get("h2h")
    if hh and (hh["home_wins"] + hh["away_wins"] + hh["draws"]) >= 2:
        scored.append((1.6, f"{hh['title']}: {hh['summary']}"))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _, txt in scored:
        if txt not in seen:
            out.append(txt)
            seen.add(txt)
        if len(out) >= 5:
            break
    return out


def public(d: dict | None) -> dict | None:
    """Versión para la interfaz (sin las claves internas del modelo)."""
    if not d:
        return None
    return {k: v for k, v in d.items() if not k.startswith("_")}
