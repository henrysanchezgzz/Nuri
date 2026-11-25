"""
NURI · model.py — EL ANÁLISIS PROPIO de NURI (independiente del casino)
=======================================================================
Calcula la probabilidad de cada resultado con estadísticas, sin mirar el momio:
  · Fútbol: Poisson con ataque/defensa de la temporada y de los últimos 5 (goles a favor y en contra),
    ventaja de local y bajas de los goleadores del equipo.
  · NBA / NFL / NHL: margen y total proyectados con puntos a favor/en contra (temporada + últimos 5),
    ventaja de local, bajas de figuras; se mezcla con el modelo de ESPN (que tampoco es el casino).
Devuelve {selección: probabilidad} por mercado. El momio sólo se usa después, para ver si paga bien.
"""
from __future__ import annotations

import math

HOME_ADV = {"football": 0.20, "basketball": 2.5, "americanfootball": 1.8, "hockey": 0.15}   # goles / puntos
SD_MARGIN = {"basketball": 12.0, "americanfootball": 13.5}
SD_TOTAL = {"basketball": 18.0, "americanfootball": 13.0}
LEAGUE_AVG = {"football": 1.35, "hockey": 3.0}   # goles por equipo por partido (respaldo)


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def _form_fa(t: dict) -> tuple[float, float, int] | None:
    f = [x for x in (t.get("form") or []) if x.get("own") is not None and x.get("opp") is not None]
    if len(f) < 3:
        return None
    return sum(x["own"] for x in f) / len(f), sum(x["opp"] for x in f) / len(f), len(f)


def _blend_fa(season, form) -> tuple[float, float] | None:
    if season and form:
        w = 0.35 if form[2] >= 5 else 0.25            # los últimos 5 pesan 35 %
        return (1 - w) * season[0] + w * form[0], (1 - w) * season[1] + w * form[1]
    if season:
        return season[0], season[1]
    if form:
        return form[0], form[1]
    return None


def _key_out(d: dict, side: str) -> int:
    t = d.get(side) or {}
    lead = {x.get("name") for x in t.get("leaders") or []}
    return sum(1 for i in t.get("injuries") or [] if i.get("code") in ("out", "suspended") and i.get("name") in lead)


def team_probs(d: dict, fam: str, for_against) -> dict | None:
    """{"ml": {home, draw?, away}, "total": fn(line)->p_over, "spread": fn(line, side)->p, "btts": p_yes, "src": n}"""
    if not d:
        return None
    h_s, a_s = for_against(d, "home", fam), for_against(d, "away", fam)
    h = _blend_fa(h_s, _form_fa(d.get("home") or {}))
    a = _blend_fa(a_s, _form_fa(d.get("away") or {}))
    if not h or not a:
        return None
    out: dict = {"src": (2 if h_s and a_s else 1)}
    if fam in ("football", "hockey"):
        avg = LEAGUE_AVG[fam]
        lam_h = max(0.15, (h[0] + a[1]) / 2 + HOME_ADV[fam] / 2) * (1 - 0.06 * _key_out(d, "home"))
        lam_a = max(0.15, (a[0] + h[1]) / 2 - HOME_ADV[fam] / 2) * (1 - 0.06 * _key_out(d, "away"))
        lam_h, lam_a = 0.85 * lam_h + 0.15 * avg, 0.85 * lam_a + 0.15 * avg      # un poco hacia la media
        grid = [[pois(i, lam_h) * pois(j, lam_a) for j in range(11)] for i in range(11)]
        ph = sum(grid[i][j] for i in range(11) for j in range(11) if i > j)
        pd = sum(grid[i][i] for i in range(11))
        pa = 1 - ph - pd
        if fam == "hockey":     # en NHL no hay empate: el empate se reparte (tiempo extra)
            ph, pa, pd = ph + pd * lam_h / (lam_h + lam_a), pa + pd * lam_a / (lam_h + lam_a), 0.0
        out["ml"] = {"home": ph, "draw": pd, "away": pa} if fam == "football" else {"home": ph, "away": pa}
        out["lam"] = (lam_h, lam_a)

        def total(line):
            return sum(grid[i][j] for i in range(11) for j in range(11) if i + j > line)

        def spread(line, side):   # hándicap del lado `side`, sin medios goles (se ignora el push)
            s = 1 if side == "home" else -1
            return sum(grid[i][j] for i in range(11) for j in range(11) if s * (i - j) + line > 0)
        out["total"], out["spread"] = total, spread
        out["btts"] = (1 - math.exp(-lam_h)) * (1 - math.exp(-lam_a))
    else:
        sdm, sdt = SD_MARGIN.get(fam, 12.0), SD_TOTAL.get(fam, 15.0)
        ph_pts = (h[0] + a[1]) / 2 + HOME_ADV.get(fam, 2) / 2 - 2.5 * _key_out(d, "home")
        pa_pts = (a[0] + h[1]) / 2 - HOME_ADV.get(fam, 2) / 2 - 2.5 * _key_out(d, "away")
        margin, tot = ph_pts - pa_pts, ph_pts + pa_pts
        p_home = phi(margin / sdm)
        pred = d.get("predictor") or {}
        if pred.get("home") is not None:          # el modelo de ESPN entra al 40 %
            p_home = 0.6 * p_home + 0.4 * pred["home"] / 100
            margin = sdm * _inv_phi(p_home)
        out["ml"] = {"home": p_home, "away": 1 - p_home}
        out["proj"] = (margin, tot)
        out["total"] = lambda line: 1 - phi((line - tot) / sdt)
        out["spread"] = lambda line, side: phi(((margin if side == "home" else -margin) + line) / sdm)
    return out


def _inv_phi(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def prob_for(tp: dict, mt: str, sel: str, line) -> float | None:
    """La probabilidad del modelo para una fila del motor."""
    if not tp:
        return None
    ml = tp.get("ml") or {}
    if mt in ("1x2", "ml"):
        return ml.get(sel)
    if mt == "dc":
        m = {"1x": ("home", "draw"), "x2": ("draw", "away"), "12": ("home", "away")}.get(str(sel).lower())
        return sum(ml.get(x, 0) for x in m) if m else None
    if mt == "total" and line is not None and sel in ("over", "under"):
        po = tp["total"](float(line))
        return po if sel == "over" else 1 - po
    if mt in ("spread", "ah") and line is not None and sel in ("home", "away"):
        return tp["spread"](float(line), sel)
    if mt == "btts" and "btts" in tp:
        return tp["btts"] if sel in ("yes", "si") else 1 - tp["btts"]
    return None


def prop_prob(vals: list, line: float, side: str, poisson: bool) -> float | None:
    """Probabilidad propia de un jugador: promedio ponderado (los últimos 5 pesan doble) y su variación real."""
    if len(vals) < 6:
        return None
    w = [2.0 if i < 5 else 1.0 for i in range(len(vals))]
    mean = sum(v * x for v, x in zip(vals, w)) / sum(w)
    if poisson:
        k = math.floor(line)
        over = 1 - sum(pois(i, max(mean, 0.05)) for i in range(k + 1))
    else:
        m0 = sum(vals) / len(vals)
        sd = max((sum((v - m0) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5, 0.15 * max(mean, 1), 0.8)
        over = 1 - phi((line - mean) / sd)
    return over if side == "over" else 1 - over
