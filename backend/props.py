"""
NURI · 누리 — props.py
======================
Props de jugadores con las líneas REALES de DraftKings que publica ESPN (NBA y NFL).

De dónde sale el valor
----------------------
Cuando DraftKings mueve la línea de un jugador (por una lesión, un cambio de rol, dinero fuerte),
muchas casas mexicanas tardan en copiarla. Mientras tanto, la línea vieja tiene ventaja:

    Josh Allen · yardas por pase: abrió en 231.5 y DraftKings ya la bajó a 214.5
    → "Menos de 231.5" vale mucho más que un volado. Si tu casa todavía la tiene así, tómala.

La probabilidad de la línea vieja se calcula con la línea actual (mediana del mercado), el momio
sin comisión cuando ESPN lo trae, y la dispersión típica de cada estadística. Sólo cuenta si el
movimiento es reciente (la casa todavía no lo copia) y si conviene a un momio normal (≈ 1.87).

También califica cada prop con el boxscore oficial de ESPN cuando termina el partido.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime

import httpx

CORE = "https://sports.core.api.espn.com/v2/sports"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (NURI)"}

# deporte del feed → (deporte ESPN, liga)
LEAGUES = {"nba": ("basketball", "nba"), "nfl": ("football", "nfl")}

# tipo de DraftKings → (clave, etiqueta, dispersión σ ≈ a·línea + b, paso mínimo de movimiento)
STATS: dict[str, tuple] = {
    "Total Points": ("pts", "puntos", (0.22, 2.5), 1.5),
    "Total Rebounds": ("reb", "rebotes", (0.30, 1.2), 1.0),
    "Total Assists": ("ast", "asistencias", (0.35, 1.0), 1.0),
    "Total 3-Point Field Goals": ("3pm", "triples", (0.45, 0.7), 0.5),
    "Total Points, Rebounds, and Assists": ("pra", "puntos + rebotes + asistencias", (0.18, 4.0), 2.0),
    "Total Points and Rebounds": ("pr", "puntos + rebotes", (0.20, 3.0), 1.5),
    "Total Points and Assists": ("pa", "puntos + asistencias", (0.20, 3.0), 1.5),
    "Total Assists and Rebounds": ("ra", "rebotes + asistencias", (0.25, 2.0), 1.0),
    "Total Passing Yards (incl. overtime)": ("pass_yds", "yardas por pase", (0.22, 22.0), 6.0),
    "Total Rushing Yards (incl. overtime)": ("rush_yds", "yardas por tierra", (0.40, 12.0), 5.0),
    "Total Receiving Yards (incl. overtime)": ("rec_yds", "yardas por recepción", (0.50, 10.0), 5.0),
    "Total Receptions (incl. overtime)": ("rec", "recepciones", (0.35, 1.0), 1.0),
    "Total Rushing Plus Receiving Yards (incl. overtime)": ("rush_rec_yds", "yardas por tierra y recepción", (0.38, 12.0), 6.0),
    "Total Pass Completions (incl. overtime)": ("pass_cmp", "pases completos", (0.17, 2.5), 1.0),
    "Total Passing Attempts (incl. overtime)": ("pass_att", "pases intentados", (0.15, 3.0), 1.5),
    "Total Carries (incl. overtime)": ("carries", "acarreos", (0.25, 2.0), 1.0),
}
LABEL = {v[0]: v[1] for v in STATS.values()}
# escaleras "X o más" de DraftKings: sirven para comprobar cuál del par es el 'Más'
MILESTONES = {"pts": "Points Milestones", "reb": "Rebounds Milestones", "ast": "Assists Milestones",
              "3pm": "3-Point Field Goals Milestones", "pra": "Points + Assists + Rebounds Milestones",
              "pr": "Points + Rebounds Milestones", "pa": "Points + Assists Milestones",
              "ra": "Rebounds + Assists Milestones", "pass_yds": "Passing Yards Milestones",
              "rush_yds": "Rushing Yards Milestones", "rec_yds": "Receiving Yards Milestones",
              "rec": "Receptions Milestones", "rush_rec_yds": "Rushing + Receiving Yards Milestones",
              "pass_att": "Passing Attempts Milestones", "carries": "Rushing Attempts Milestones"}
MS_KEY = {v: k for k, v in MILESTONES.items()}

MIN_P = 0.555          # a un momio normal (≈ 1.87–1.91) tiene que dejar ≥ 5 % de ventaja
MAX_P = 0.78           # más que esto: casi seguro ya lo sabe todo el mercado (o es otra línea)
EDGE = 0.05            # ventaja mínima sobre el momio que pedimos
FRESH_S = 3 * 3600     # el movimiento tiene que ser reciente para que las casas no lo hayan copiado
MAX_START_S = 30 * 3600


def phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def phi_inv(p: float) -> float:
    """Inversa de la normal estándar (Acklam, error < 1e-6 en lo que usamos)."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    lo, hi = 0.02425, 1 - 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > hi:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(s: str | None) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _aid(item: dict) -> str | None:
    m = re.search(r"athletes/(\d+)", ((item.get("athlete") or {}).get("$ref") or ""))
    return m.group(1) if m else None


def norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", s).strip()


def parse_props(items: list[dict]) -> dict[str, dict]:
    """Agrupa las líneas Más/Menos de cada jugador. ESPN manda cada par seguido: primero el 'Más'
    y luego el 'Menos' (comprobado contra las escaleras de DraftKings)."""
    groups: dict[tuple, list] = {}
    ladder: dict[tuple, float] = {}
    for it in items:
        tname = (it.get("type") or {}).get("name") or ""
        if tname in MS_KEY:
            aid = _aid(it)
            k = _f(((it.get("current") or {}).get("target") or {}).get("value"))
            d = _f(((it.get("odds") or {}).get("decimal") or {}).get("value"))
            if aid and k is not None and d:
                ladder[(aid, MS_KEY[tname], k)] = d
            continue
        if tname not in STATS:
            continue
        aid = _aid(it)
        cur = _f(((it.get("current") or {}).get("target") or {}).get("value"))
        if not aid or cur is None:
            continue
        groups.setdefault((aid, tname, cur), []).append(it)
    out: dict[str, dict] = {}
    for (aid, tname, cur), its in groups.items():
        key, label, sig, step = STATS[tname]
        first = its[0]
        opn = _f(((first.get("open") or {}).get("target") or {}).get("value"))
        dec = [(_f(((x.get("odds") or {}).get("decimal") or {}).get("value")),
                _f(((x.get("odds") or {}).get("decimal") or {}).get("open"))) for x in its[:2]]
        p_over = p_over_open = None
        if len(dec) == 2 and all(d[0] and d[0] > 1 for d in dec):
            io, iu = 1 / dec[0][0], 1 / dec[1][0]
            p_over = io / (io + iu)
            if all(d[1] and d[1] > 1 for d in dec):
                oo, ou = 1 / dec[0][1], 1 / dec[1][1]
                p_over_open = oo / (oo + ou)
        # ¿el primero es el 'Más'? Se compara con la escalera "X o más" (Más de 10.5 ≈ 11 o más)
        ms = ladder.get((aid, key, float(math.ceil(cur))))
        if p_over is not None and ms and abs(dec[1][0] - ms) + 0.10 < abs(dec[0][0] - ms):  # sólo si es claro
            p_over = 1 - p_over
            p_over_open = (1 - p_over_open) if p_over_open is not None else None
        upd = max(_ts(x.get("lastUpdated")) for x in its)
        out[f"{key}:{aid}"] = {"athlete": aid, "stat": key, "label": label, "line": cur, "open": opn,
                               "p_over": p_over, "p_over_open": p_over_open, "updated": upd,
                               "sigma": max(sig[0] * cur + sig[1], 0.5), "step": step}
    return out


def price_line(pr: dict, line: float, side: str) -> float:
    """Probabilidad de que el jugador quede del lado `side` de `line`, con el mercado actual de
    DraftKings: la línea actual es la mediana (ajustada por el momio sin comisión si lo hay)."""
    m = pr["line"] + pr["sigma"] * phi_inv(pr["p_over"] if pr.get("p_over") else 0.5)
    over = 1 - phi((line - m) / pr["sigma"])
    return over if side == "over" else 1 - over


class PropsSource:
    """Baja las props de DraftKings (vía ESPN) y los nombres de los jugadores (plantillas)."""

    def __init__(self, state: dict | None = None):
        st = state or {}
        self.names: dict[str, list] = st.get("names") or {}        # id → [nombre, equipo_id, ts]
        self.rost_ts: dict[str, float] = st.get("rost_ts") or {}   # equipo → última descarga
        self.hist: dict[str, list] = st.get("hist") or {}          # clave → [línea, ts_del_cambio, ts_visto, ts_primera_vez]
        self.props: dict[str, dict] = {}                           # evento → {clave: prop}
        self.status = {"ok": False, "detail": "sin revisar", "events": 0, "props": 0}

    def dump(self) -> dict:
        now = time.time()
        names = {k: v for k, v in self.names.items() if now - (v[2] if len(v) > 2 else now) < 21 * 86400}
        hist = {k: v for k, v in self.hist.items() if now - v[2] < 36 * 3600}
        return {"names": names, "rost_ts": self.rost_ts, "hist": hist}

    async def _json(self, client: httpx.AsyncClient, url: str, params: dict | None = None):
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def _roster(self, client, sport: str, lg: str, team_id: str) -> None:
        js = await self._json(client, f"{SITE}/{sport}/{lg}/teams/{team_id}/roster")
        now = time.time()
        for grp in js.get("athletes") or []:
            for a in (grp.get("items") if isinstance(grp, dict) and "items" in grp else [grp]):
                if isinstance(a, dict) and a.get("id"):
                    self.names[str(a["id"])] = [a.get("displayName") or a.get("fullName") or "", team_id, now]
        self.rost_ts[team_id] = now

    async def refresh(self, feed, now: float | None = None, concurrency: int = 8) -> None:
        """Una pasada: props de los partidos de NBA y NFL de las próximas 30 h."""
        now = now or time.time()
        evs = [e for e in feed.raw.values() if e.get("_league") in LEAGUES and e["status"] == "pre"
               and 0 < e["start_ts"] - now < MAX_START_S]
        sem = asyncio.Semaphore(concurrency)
        errs = 0

        async def one(e):
            nonlocal errs
            sport, lg = LEAGUES[e["_league"]]
            eid = e["_espn_id"]
            async with sem:
                try:
                    r = await client.get(f"{CORE}/{sport}/leagues/{lg}/events/{eid}/competitions/{eid}/odds/100/propBets",
                                         params={"lang": "en", "region": "us", "limit": 1000})
                    if r.status_code == 404:
                        return                      # todavía no publican props de este partido
                    r.raise_for_status()
                    self.props[e["id"]] = parse_props(r.json().get("items") or [])
                    for tid in (e.get("_home_id"), e.get("_away_id")):
                        if tid and now - self.rost_ts.get(str(tid), 0) > 20 * 3600:
                            await self._roster(client, sport, lg, str(tid))
                except Exception as exc:  # una liga sin props no tumba la corrida
                    errs += 1
                    print(f"[props] {e['id']}: {type(exc).__name__}"[:120])

        async with httpx.AsyncClient(timeout=15.0, headers=UA, follow_redirects=True) as client:
            await asyncio.gather(*(one(e) for e in evs))
        # historial: cuándo cambió por última vez cada línea (lo vemos cada ~15 min)
        n = 0
        for eid, props in self.props.items():
            for k, pr in props.items():
                n += 1
                hk = f"{eid}|{k}"
                h = self.hist.get(hk)
                if h is None:
                    self.hist[hk] = [pr["line"], 0.0, now, now]     # primera vez: no sabemos cuándo cambió
                else:
                    if len(h) < 4:
                        h.append(h[2])
                    if pr["line"] != h[0]:
                        self.hist[hk] = [pr["line"], now, now, h[3]]
                    else:
                        h[2] = now
        self.status = {"ok": n > 0 or not evs, "detail": f"{n} líneas de jugadores en {len(self.props)} partidos",
                       "events": len(self.props), "props": n, "errors": errs}

    def name_of(self, aid: str) -> str | None:
        v = self.names.get(str(aid))
        return v[0] if v else None

    def candidates(self, feed, now: float, injuries: dict | None = None) -> list[dict]:
        """Props con línea vieja todavía valiosa (o momio que se movió fuerte en la misma línea)."""
        out = []
        for eid, props in self.props.items():
            e = feed.raw.get(eid) or {}
            if e.get("status") != "pre" or not (20 * 60 <= e.get("start_ts", 0) - now <= MAX_START_S):
                continue
            inj = (injuries or {}).get(eid) or {}
            for k, pr in props.items():
                name = self.name_of(pr["athlete"])
                if not name:
                    continue
                st = inj.get(name) or inj.get(norm(name))
                if st in ("out", "doubtful"):
                    continue
                h = self.hist.get(f"{eid}|{k}") or [pr["line"], 0.0, now, now]
                # cuándo cambió: si NURI lo vio cambiar, esa hora; si ya venía movida cuando empezamos a
                # verla, a más tardar cuando la vimos por primera vez (o la hora de ESPN, si es antes)
                changed_at = h[1] or min(pr["updated"] or now, h[3] if len(h) > 3 else now)
                fresh = now - changed_at <= FRESH_S
                cand = None
                if pr["open"] is not None and abs(pr["line"] - pr["open"]) >= pr["step"] and fresh:
                    side = "over" if pr["line"] > pr["open"] else "under"
                    p = price_line(pr, pr["open"], side)
                    if MIN_P <= p <= MAX_P:
                        cand = {"kind": "move", "line": pr["open"], "side": side, "p": p}
                if cand is None and pr.get("p_over") is not None and pr.get("p_over_open") is not None and fresh:
                    for side, pc, po in (("over", pr["p_over"], pr["p_over_open"]),
                                         ("under", 1 - pr["p_over"], 1 - pr["p_over_open"])):
                        if pc - po >= 0.06 and MIN_P <= pc <= MAX_P:
                            cand = {"kind": "price", "line": pr["line"], "side": side, "p": pc}
                if not cand:
                    continue
                p = cand["p"]
                min_odds = round((1 + EDGE) / p, 2)
                cand.update({"event_id": eid, "athlete": pr["athlete"], "player": name, "stat": pr["stat"],
                             "label": pr["label"], "now_line": pr["line"], "open_line": pr["open"],
                             "min_odds": min_odds, "ev": round(p * min_odds - 1, 4),
                             "score": p - 0.5 + (0.02 if h[1] else 0.0), "ev_raw": e})
                out.append(cand)
        out.sort(key=lambda c: -c["score"])
        return out


# ------------------------------------------------------------------------------ calificar
def _num(s) -> float | None:
    try:
        return float(str(s).replace("+", ""))
    except (TypeError, ValueError):
        return None


def stat_from_box(summary: dict, league: str, athlete: str, stat: str) -> float | None:
    """Estadística final del jugador en el boxscore oficial. None = no jugó (apuesta anulada)."""
    players = ((summary.get("boxscore") or {}).get("players")) or []
    if league == "nba":
        for team in players:
            for grp in team.get("statistics") or []:
                keys = grp.get("keys") or []
                for a in grp.get("athletes") or []:
                    if str((a.get("athlete") or {}).get("id")) != str(athlete):
                        continue
                    if a.get("didNotPlay") or not a.get("stats"):
                        return None
                    v = dict(zip(keys, a["stats"]))
                    pts = _num(v.get("points")) or 0.0
                    reb = _num(v.get("rebounds")) or 0.0
                    ast = _num(v.get("assists")) or 0.0
                    tpm = _num(str(v.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0")).split("-")[0]) or 0.0
                    return {"pts": pts, "reb": reb, "ast": ast, "3pm": tpm, "pra": pts + reb + ast, "pr": pts + reb,
                            "pa": pts + ast, "ra": reb + ast}.get(stat)
        return None
    if league == "nfl":
        seen = False
        vals = {"pass_yds": 0.0, "pass_cmp": 0.0, "pass_att": 0.0, "rush_yds": 0.0, "carries": 0.0,
                "rec_yds": 0.0, "rec": 0.0}
        for team in players:
            for cat in team.get("statistics") or []:
                keys = cat.get("keys") or []
                for a in cat.get("athletes") or []:
                    if str((a.get("athlete") or {}).get("id")) != str(athlete):
                        continue
                    seen = True
                    v = dict(zip(keys, a.get("stats") or []))
                    if cat.get("name") == "passing":
                        vals["pass_yds"] = _num(v.get("passingYards")) or 0.0
                        ca = str(v.get("completions/passingAttempts") or "0/0").split("/")
                        vals["pass_cmp"], vals["pass_att"] = _num(ca[0]) or 0.0, _num(ca[-1]) or 0.0
                    elif cat.get("name") == "rushing":
                        vals["rush_yds"] = _num(v.get("rushingYards")) or 0.0
                        vals["carries"] = _num(v.get("rushingAttempts")) or 0.0
                    elif cat.get("name") == "receiving":
                        vals["rec_yds"] = _num(v.get("receivingYards")) or 0.0
                        vals["rec"] = _num(v.get("receptions")) or 0.0
        if not seen:
            return None
        vals["rush_rec_yds"] = vals["rush_yds"] + vals["rec_yds"]
        return vals.get(stat)
    return None


def parse_key(market_key: str) -> tuple[str, str, float] | None:
    """prop:<stat>:<athlete>:<línea>"""
    m = re.match(r"prop:([a-z0-9_]+):(\d+):(-?[\d.]+)$", market_key or "")
    return (m.group(1), m.group(2), float(m.group(3))) if m else None


def grade(p: dict, summary: dict) -> tuple[str, float] | None:
    k = parse_key(p.get("market_key") or "")
    if not k:
        return "void", 0.0
    stat, aid, line = k
    v = stat_from_box(summary, p.get("league_code") or "", aid, stat)
    if v is None:
        return "void", 0.0
    o = float(p["odds"])
    if abs(v - line) < 1e-9:
        return "push", 0.0
    win = v > line if p["selection"] == "over" else v < line
    return ("won", round(o - 1.0, 4)) if win else ("lost", -1.0)
