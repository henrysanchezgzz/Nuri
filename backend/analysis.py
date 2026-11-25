"""
NURI · 누리 — analysis.py
=========================
El análisis que se ve en cada pick: quién juega, por qué conviene y los números reales que lo
respaldan (ESPN, DraftKings y la casa). Si un dato no existe, no se inventa: simplemente no sale.

Salida (para la app):
    {"kind": "player"|"team", "player": {...}, "teams": {"home": {...}, "away": {...}},
     "pick_side": "home"|"away"|"draw"|"over"|"under"|"yes"|None,
     "probs": {"fair", "book", "book_name", "odds", "fair_odds", "edge"},
     "series": {"label", "line", "vals", "side"},          # props: últimos partidos contra la línea
     "tiles": [[etiqueta, valor], ...],                      # 3 o 4 números clave
     "rows": [{"label", "home", "away", "better"}],          # equipos: comparación de temporada
     "pred": {"home", "draw", "away"},                       # predictor de ESPN (%)
     "points": [["pos"|"neg"|"info", texto], ...]}           # el análisis, en frases cortas
"""
from __future__ import annotations

import math

POS_ES = {
    "football": {"G": "Portero", "GK": "Portero", "D": "Defensa", "DF": "Defensa", "M": "Medio", "MF": "Medio",
                 "F": "Delantero", "FW": "Delantero", "AM": "Medio ofensivo", "DM": "Medio defensivo"},
    "basketball": {"PG": "Base", "SG": "Escolta", "SF": "Alero", "PF": "Ala-pívot", "C": "Pívot", "G": "Base",
                   "F": "Alero", "G-F": "Escolta-alero", "F-C": "Ala-pívot"},
    "americanfootball": {"QB": "Quarterback", "RB": "Corredor", "WR": "Receptor", "TE": "Ala cerrada",
                         "FB": "Fullback", "K": "Pateador"},
    "hockey": {"C": "Centro", "LW": "Ala izquierda", "RW": "Ala derecha", "D": "Defensa", "G": "Portero",
               "F": "Delantero"},
}
STAT_ES = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "3pm": "Triples", "pra": "Pts + reb + ast",
           "pr": "Pts + reb", "pa": "Pts + ast", "ra": "Reb + ast", "pass_yds": "Yardas por pase",
           "rush_yds": "Yardas por tierra", "rec_yds": "Yardas por recepción", "rec": "Recepciones",
           "rush_rec_yds": "Yardas tierra + recepción", "pass_cmp": "Pases completos", "pass_att": "Pases intentados",
           "carries": "Acarreos", "sog": "Tiros a gol", "hpts": "Puntos", "hast": "Asistencias"}


def initials(name: str) -> str:
    parts = [x for x in (name or "").replace(".", " ").split() if x[:1].isalpha()]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def pct(x: float | None) -> str:
    return f"{int(x * 100 + 0.5)}%" if x is not None else "—"     # redondeo normal (igual que la app)


def _team(e: dict, d: dict, side: str) -> dict:
    t = dict(e.get(side) or {})
    dt = (d or {}).get(side) or {}
    form = [{"r": f.get("r"), "own": f.get("own"), "opp": f.get("opp"), "vs": f.get("vs")}
            for f in (dt.get("form") or [])[-5:] if f.get("r") in ("G", "E", "P")]
    return {"name": t.get("name"), "short": t.get("short"), "logo": t.get("logo"), "color": t.get("color"),
            "alt": t.get("alt"), "form": form, "record": dt.get("record"), "rank": dt.get("rank"), "of": dt.get("of"),
            "inj": [i.get("name") for i in (dt.get("injuries") or [])[:4] if isinstance(i, dict) and i.get("name")]}


def _side_of_team(e: dict, team_id) -> str | None:
    if team_id is None:
        return None
    if str(team_id) == str(e.get("_home_id")):
        return "home"
    if str(team_id) == str(e.get("_away_id")):
        return "away"
    return None


def _probs(p: dict, book_label: str | None) -> dict:
    fair = p.get("p")
    odds = p.get("odds") if (p.get("book") or p.get("tier") == "S") else None
    book = (1 / odds) if odds and odds > 1 else None
    return {"fair": round(fair, 4) if fair else None, "book": round(book, 4) if book else None,
            "book_name": book_label, "odds": odds, "min_odds": p.get("min_odds"),
            "fair_odds": round(1 / fair, 2) if fair else None,
            "edge": round(fair * odds - 1, 4) if (fair and odds) else None}


def _goals_form(t: dict) -> tuple[int, float] | None:
    f = [x for x in t.get("form") or [] if x.get("own") is not None]
    if len(f) < 3:
        return None
    return sum(1 for x in f if x["own"] > 0), sum(x["own"] for x in f) / len(f)


def build(p: dict, feed, hist: dict | None = None, book_label: str | None = None) -> dict | None:
    e = (getattr(feed, "raw", {}) or {}).get(p.get("event_id")) or {}
    if not e:
        return None
    d = (getattr(feed, "dossier", {}) or {}).get(p["event_id"]) or {}
    an: dict = {"teams": {"home": _team(e, d, "home"), "away": _team(e, d, "away")},
                "probs": _probs(p, book_label), "points": [], "tiles": [], "pick_side": None}
    mt = p.get("market_type")
    try:
        if mt == "scorer":
            _scorer(an, p, e, d, feed)
        elif mt == "prop":
            _prop(an, p, e, d, feed)
        else:
            _team_pick(an, p, e, d, hist or {})
    except Exception as exc:   # el análisis nunca tumba la app
        an["points"].append(["info", f"Análisis incompleto ({type(exc).__name__})."])
    _value_points(an, p)
    return an


def _value_points(an: dict, p: dict) -> None:
    pr = an["probs"]
    if p.get("tier") == "S" and pr.get("fair"):
        an["points"].insert(0, ["pos", f"NURI le da {pct(pr['fair'])} de que pase: el mercado ya lo ve favorito y las "
                                       f"estadísticas lo confirman."])
        if pr.get("min_odds"):
            an["points"].append(["info", f"Tómala si tu casa paga {pr['min_odds']:.2f} o más."])
        return
    if pr.get("fair") and pr.get("book") and pr.get("odds"):
        an["points"].insert(0, ["pos", f"{pr['book_name']} lo paga a {pr['odds']:.2f}, como si pasara {pct(pr['book'])} "
                                       f"de las veces. NURI calcula {pct(pr['fair'])}: lo justo sería "
                                       f"{pr['fair_odds']:.2f}."])
    if pr.get("min_odds"):
        an["points"].append(["info", f"Conviene mientras paguen {pr['min_odds']:.2f} o más. Si ya bajó, no la tomes."])


# ----------------------------------------------------------------------------- goleador
def _scorer(an: dict, p: dict, e: dict, d: dict, feed) -> None:
    import scorers as S
    name = p.get("market_label") or ""
    an["kind"] = "player"
    src = getattr(feed, "props_src", None)
    aid = src.find(name, (e.get("_home_id"), e.get("_away_id"))) if src else None
    info = src.info_of(aid) if (src and aid) else {}
    side = _side_of_team(e, info.get("team_id"))
    lead = None
    for s in ("home", "away"):
        for l_ in ((d.get("_leaders_raw") or {}).get(s, {}) or {}).get("goalsLeaders") or []:
            if S.norm(l_.get("name")) == S.norm(name):
                lead, side = l_, side or s
    starters = d.get("_starters") or {}
    t = an["teams"].get(side) if side else {}
    an["player"] = {"name": name, "init": initials(name), "side": side, "team": (t or {}).get("name"),
                    "pos": POS_ES["football"].get((info.get("pos") or (lead or {}).get("pos") or "").upper(), ""),
                    "num": info.get("num") or "", "color": (t or {}).get("color"), "alt": (t or {}).get("alt"),
                    "logo": (t or {}).get("logo")}
    an["pick_side"] = side
    b = (getattr(feed, "b365", {}) or {}).get(p["event_id"])
    lam = S.total_lambda(e, b)
    dk = None
    dkn = S.dk_names(feed, p["event_id"])
    if dkn:
        dk = S._match(dkn, name)
    if lead and lead.get("goals") is not None and lead.get("apps"):
        g, a = int(lead["goals"]), int(lead["apps"])
        an["tiles"].append(["Goles", f"{g} en {a}"])
        an["points"].append(["pos" if g / max(a, 1) >= 0.35 else "info",
                             f"Lleva {g} goles en {a} partidos esta temporada ({g / max(a, 1):.2f} por partido)."])
    if lam:
        an["tiles"].append(["Goles esperados", f"{lam:.1f}"])
        an["points"].append(["info", f"El mercado espera {lam:.1f} goles en el partido."])
    if dk:
        more = p["odds"] / dk - 1
        an["tiles"].append(["DraftKings", f"{dk:.2f}"])
        if more > 0.05:
            an["points"].append(["pos", f"DraftKings lo paga a {dk:.2f}: aquí te dan {round(more * 100)}% más."])
        elif more < -0.05:
            an["points"].append(["neg", f"DraftKings lo paga a {dk:.2f}, más que aquí."])
    if side and t:
        gf = _goals_form(t)
        if gf:
            k, avg = gf
            an["points"].append(["pos" if avg >= 1.5 else "info",
                                 f"{t['name']} anotó en {k} de sus últimos {len(t['form'])} y promedia {avg:.1f} goles."])
        opp = an["teams"]["away" if side == "home" else "home"]
        conc = [x["opp"] for x in opp.get("form") or [] if x.get("opp") is not None]
        if len(conc) >= 3:
            ca = sum(conc) / len(conc)
            an["points"].append(["pos" if ca >= 1.5 else ("neg" if ca <= 0.8 else "info"),
                                 f"{opp['name']} recibe {ca:.1f} goles por partido en sus últimos {len(conc)}."])
    if side and starters.get(side):
        on = any(S.norm(x) == S.norm(name) for x in starters[side])
        an["points"].append(["pos", "Titular confirmado en la alineación."] if on else
                            ["neg", "No aparece en el once titular. Si no juega, la apuesta se anula; si entra de cambio, "
                                    "tiene menos minutos para anotar."])
    an["tiles"] = an["tiles"][:4]


# ----------------------------------------------------------------------------- props de jugador
def _prop(an: dict, p: dict, e: dict, d: dict, feed) -> None:
    import props as P
    an["kind"] = "player"
    k = P.parse_key(p.get("market_key") or "")
    src = getattr(feed, "props_src", None)
    if not k or not src:
        return
    stat, aid, line = k
    info = src.info_of(aid)
    side = _side_of_team(e, info.get("team_id"))
    t = an["teams"].get(side) if side else {}
    fam = e.get("sport") or ""
    an["player"] = {"name": p.get("market_label") or info.get("name"), "init": initials(p.get("market_label") or ""),
                    "side": side, "team": (t or {}).get("name"),
                    "pos": POS_ES.get(fam, {}).get((info.get("pos") or "").upper(), info.get("pos") or ""),
                    "num": info.get("num") or "", "color": (t or {}).get("color"), "alt": (t or {}).get("alt"),
                    "logo": (t or {}).get("logo")}
    an["pick_side"] = side
    games = ((src.gl.get(aid) or {}).get("games")) or []
    vals = P.gl_values(games, stat)
    over = p.get("selection") == "over"
    if vals:
        last = vals[:10][::-1]                      # del más viejo al más reciente
        an["series"] = {"label": STAT_ES.get(stat, stat), "line": line, "vals": last, "side": p.get("selection")}
        hits = sum(1 for x in vals[:10] if (x > line if over else x < line))
        n10 = len(vals[:10])
        avg = sum(vals) / len(vals)
        avg5 = sum(vals[:5]) / min(5, len(vals))
        an["tiles"] += [["Temporada", f"{avg:.1f}"], ["Últimos 5", f"{avg5:.1f}"], ["Le gana a la línea", f"{hits} de {n10}"]]
        good = hits / n10 >= 0.6
        an["points"].append(["pos" if good else ("neg" if hits / n10 <= 0.4 else "info"),
                             f"{'Pasó' if over else 'Se quedó abajo de'} {line:g} en {hits} de sus últimos {n10} partidos."])
        side_ok = (avg5 > line) if over else (avg5 < line)
        an["points"].append(["pos" if side_ok else "neg", f"Promedia {avg5:.1f} en sus últimos 5 (la línea es {line:g})."])
    ut = P.usage_trend(games, stat) if games else None
    if ut is not None and abs(ut) >= 0.08:
        what = {"basketball": "minutos", "hockey": "tiempo en hielo"}.get(fam, "participación")
        an["points"].append(["pos" if (ut > 0) == over else "neg",
                             f"Sus {what} {'subieron' if ut > 0 else 'bajaron'} {abs(round(ut * 100))}% en los últimos 4 partidos."])
    if side:
        opp = an["teams"]["away" if side == "home" else "home"]
        if opp.get("name"):
            an["points"].append(["info", f"Juega {'en casa' if side == 'home' else 'de visita'} contra {opp['name']}."])
    inj = (t or {}).get("inj") or []
    if inj:
        an["points"].append(["info", f"Bajas de su equipo: {', '.join(inj[:3])}."])
    an["tiles"] = an["tiles"][:4]


# ----------------------------------------------------------------------------- equipos
def _team_pick(an: dict, p: dict, e: dict, d: dict, hist: dict) -> None:
    an["kind"] = "team"
    sel = p.get("selection")
    an["pick_side"] = sel
    mt = p.get("market_type")
    h, a = an["teams"]["home"], an["teams"]["away"]
    an["rows"] = (d.get("season") or [])[:4]
    pred = d.get("predictor") or {}
    if pred.get("home") is not None:
        an["pred"] = {"home": pred.get("home"), "draw": pred.get("draw"), "away": pred.get("away")}
        if sel in ("home", "away") and mt in ("1x2", "ml"):
            pp = (pred.get(sel) or 0) / 100
            if pp >= (p.get("p") or 0) - 0.02:
                an["points"].append(["pos", f"El modelo de ESPN también lo ve: le da {round(pp * 100)}%."])
            elif pp < (p.get("p") or 0) - 0.08:
                an["points"].append(["neg", f"El modelo de ESPN es más frío: le da {round(pp * 100)}%."])
    for t in (h, a):
        f = t.get("form") or []
        if len(f) >= 3:
            w = sum(1 for x in f if x["r"] == "G")
            dr = sum(1 for x in f if x["r"] == "E")
            lo = len(f) - w - dr
            parts = [x for x in (f"ganó {w}" if w else "", f"empató {dr}" if dr else "", f"perdió {lo}" if lo else "") if x]
            txt = f"{t['name']} " + (", ".join(parts[:-1]) + " y " + parts[-1] if len(parts) > 1 else parts[0]) \
                + f" de sus últimos {len(f)}."
            mine = (sel == "home" and t is h) or (sel == "away" and t is a)
            an["points"].append(["pos" if mine and w >= 3 else ("neg" if mine and w <= 1 else "info"), txt])
    if mt in ("total", "btts"):
        tot = []
        for t in (h, a):
            f = [x for x in t.get("form") or [] if x.get("own") is not None and x.get("opp") is not None]
            if len(f) >= 3:
                tot.append(sum(x["own"] + x["opp"] for x in f) / len(f))
        if tot:
            avg = sum(tot) / len(tot)
            line = p.get("line")
            if mt == "total" and line is not None:
                ok = (avg > line) if sel == "over" else (avg < line)
                unit = {"football": "goles", "hockey": "goles", "baseball": "carreras"}.get(e.get("sport"), "puntos")
                an["points"].append(["pos" if ok else "neg",
                                     f"Sus últimos partidos promedian {avg:.1f} {unit} en total (la línea es {line:g})."])
                an["tiles"].append(["Promedio total", f"{avg:.1f}"])
    series = (hist or {}).get(p["id"]) or []
    if len(series) >= 2:
        p0, p1 = series[0][1], series[-1][1]
        if abs(p1 - p0) >= 0.02:
            an["points"].append(["pos" if p1 > p0 else "neg",
                                 f"Desde que NURI lo sigue, la probabilidad justa pasó de {pct(p0)} a {pct(p1)}."])
    for t in (h, a):
        if t.get("inj"):
            an["points"].append(["info", f"Bajas de {t['name']}: {', '.join(t['inj'][:3])}."])
    for b in (d.get("bullets") or [])[:2]:
        an["points"].append(["info", b])
    an["tiles"] = an["tiles"][:4]
