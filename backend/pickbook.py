"""
NURI · 누리 — pickbook.py
=========================
Sólo los MEJORES picks, y la autoevaluación semanal de esos mismos picks.

Cómo elige (usa TODO lo que tiene, pero sólo deja pasar lo que de verdad tiene valor):

  A · Momio real con valor  — una casa que opera en México (Bet365 vía ESPN; Caliente / Codere
      vía odds-api.io si hay clave) paga MÁS que la probabilidad real. La probabilidad real sale
      de la línea de mercado sin margen (Shin) o del modelo calibrado a ella. Ventaja mínima 5 %
      (7 % si la línea no existe en el mercado de referencia y la calcula el modelo).
  B · El mercado se movió   — la línea de referencia se movió ≥ 4 puntos de probabilidad a favor
      desde que abrió (dinero informado). Las casas mexicanas suelen tardar en ajustar: el pick es
      «tómala sólo si tu casa paga X o más».

  En ambos casos las estadísticas reales (forma, diferencia de goles/puntos, bajas de los líderes,
  predictor de ESPN, cara a cara, local/visita) tienen que ESTAR DE ACUERDO (en B) o al menos no
  contradecir (en A). Un candidato debe sostenerse 3 minutos (dos revisiones de la línea) antes de
  emitirse. Máximo 5 por día y uno por partido. Si nada pasa el filtro, no hay pick.

Autoevaluación: cada pick queda guardado con su momio, su probabilidad y la línea de cierre.
Cuando termina el partido NURI lo califica solo (con el marcador oficial de ESPN) y arma el
resumen por semana (lunes a domingo, hora del centro de México): aciertos, unidades, ROI,
aciertos esperados y CLV (si la línea se movió a favor después de dar el pick). Si los picks
dejan de ganarle al cierre, NURI sube su exigencia automáticamente.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np

import database as db
from espn_facts import f_num
from quant_engine import settle_wl

MX_TZ = timezone(timedelta(hours=-6))  # centro de México / Monterrey (sin horario de verano)
# Confianza de cada pick (0–100) → nivel. Lock = todas las señales de NURI coinciden.
LEVELS = (("lock", 80.0), ("alta", 65.0), ("media", 50.0), ("baja", 0.0))
STAKE_MULT = {"lock": 1.0, "alta": 0.8, "media": 0.6, "baja": 0.4}   # sobre ¼ de Kelly (tope 3 %)
LEVEL_TXT = {"lock": "LOCK", "alta": "Alta", "media": "Media", "baja": "Baja"}
PICK_MARKETS = ("1x2", "ml", "dc", "spread", "ah", "total", "btts")
MT_LABEL = {"1x2": "resultado final", "ml": "ganador", "dc": "doble oportunidad", "spread": "hándicap",
            "ah": "hándicap asiático", "total": "totales", "btts": "ambos anotan"}
BOOK_LABEL = {"Caliente.mx": "Caliente"}
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
SCALE = {"football": (1.2, 0.8), "basketball": (9.0, 8.0), "americanfootball": (9.0, 6.0),
         "hockey": (1.0, 0.8), "baseball": (1.5, 1.2)}  # (diferencial por partido, total por partido)
SEASON_KEYS = {"basketball": ("avgPoints", "avgPointsAgainst"),
               "americanfootball": ("totalPointsPerGame", "totalPointsPerGameAllowed"),
               "hockey": ("avgGoals", "avgGoalsAgainst")}


def book_name(b: str | None) -> str:
    return BOOK_LABEL.get(b or "", b or "")


def week_start(ts: float) -> datetime:
    local = datetime.fromtimestamp(ts, MX_TZ)
    return (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def week_label(monday: datetime) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day} al {sunday.day} de {MESES[sunday.month - 1]}"
    return f"{monday.day} {MESES[monday.month - 1]} al {sunday.day} {MESES[sunday.month - 1]}"


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def kelly_pct(odds: float, w: float, l: float, frac: float = 0.25, cap: float = 0.03) -> float:
    b = odds - 1.0
    if b <= 0 or w + l <= 0:
        return 0.0
    full = (b * w - l) / (b * (w + l))
    return round(max(0.0, min(frac * full, cap)), 4)


# ----------------------------------------------------------------------------- estadísticas → acuerdo


def _form_ppg(form: list, fam: str) -> float | None:
    if not form or len(form) < 3:
        return None
    pts = {"G": 3, "E": 1, "P": 0} if fam == "football" else {"G": 1, "E": 0.5, "P": 0}
    vals = [pts[g["r"]] for g in form if g.get("r") in pts]
    return sum(vals) / len(vals) if vals else None


def _for_against(d: dict, side: str, fam: str) -> tuple[float, float] | None:
    raw = (d.get("_season_raw") or {}).get(side) or {}
    if fam == "football":
        gp = (d.get("_gp") or {}).get(side)
        gf, ga = f_num(raw.get("totalGoals")), f_num(raw.get("goalsConceded"))
        if gp and gf is not None and ga is not None:
            return gf / gp, ga / gp
        return None
    k = SEASON_KEYS.get(fam)
    if not k:
        return None
    a, b = f_num(raw.get(k[0])), f_num(raw.get(k[1]))
    return (a, b) if a is not None and b is not None else None


def _split_pct(rec: str | None) -> float | None:
    if not rec:
        return None
    nums = [int(x) for x in rec.replace("–", "-").split("-") if x.strip().isdigit()]
    if len(nums) == 2 and sum(nums):
        return nums[0] / sum(nums)
    if len(nums) == 3 and sum(nums):  # G-E-P
        return (nums[0] + 0.5 * nums[1]) / sum(nums)
    return None


def stats_agreement(d: dict | None, fam: str, row: dict, ml_p: float | None) -> tuple[float, list[str]]:
    """Qué tanto las estadísticas reales respaldan este pick: −1 (en contra) … +1 (a favor)."""
    if not d:
        return 0.0, []
    sel, mt = row["selection"], row["market_type"]
    sig: list[tuple[float, float, str]] = []  # (peso, señal, frase si apoya)
    if mt in ("1x2", "ml", "spread", "ah") and sel in ("home", "away"):
        side, opp = sel, ("away" if sel == "home" else "home")
        ts, to = d.get(side) or {}, d.get(opp) or {}
        name = ts.get("name") or ""
        fs, fo = _form_ppg(ts.get("form"), fam), _form_ppg(to.get("form"), fam)
        if fs is not None and fo is not None:
            sig.append((0.25, _clip((fs - fo) / (1.5 if fam == "football" else 0.5)), "llega mejor en sus últimos 5"))
        fa_s, fa_o = _for_against(d, side, fam), _for_against(d, opp, fam)
        if fa_s and fa_o:
            diff = (fa_s[0] - fa_s[1]) - (fa_o[0] - fa_o[1])
            sig.append((0.30, _clip(diff / SCALE.get(fam, (1, 1))[0]),
                        "mejor diferencia de goles en la temporada" if fam in ("football", "hockey")
                        else "mejor diferencia de puntos en la temporada"))
        lead_s = {x["name"] for x in ts.get("leaders") or []}
        lead_o = {x["name"] for x in to.get("leaders") or []}
        out_s = [i["name"] for i in ts.get("injuries") or [] if i["code"] in ("out", "suspended") and i["name"] in lead_s]
        out_o = [i["name"] for i in to.get("injuries") or [] if i["code"] in ("out", "suspended") and i["name"] in lead_o]
        if lead_s or lead_o:
            sig.append((0.20, _clip(0.6 * (len(out_o) - len(out_s))),
                        f"el rival no tiene a {out_o[0]}" if out_o else ""))
        pred = d.get("predictor")
        if pred and ml_p is not None:
            pe = pred[side] / 100.0
            sig.append((0.25, _clip((pe - ml_p) / 0.08), f"ESPN también le da {pe:.0%}"))
        h2h = d.get("h2h")
        if h2h:
            n = h2h["home_wins"] + h2h["away_wins"] + h2h["draws"]
            if n >= 3:
                ws = h2h["home_wins"] if side == "home" else h2h["away_wins"]
                wo = h2h["away_wins"] if side == "home" else h2h["home_wins"]
                sig.append((0.10, _clip((ws - wo) / n), "domina los últimos enfrentamientos"))
        ps, po = _split_pct(ts.get("split")), _split_pct(to.get("split"))
        if ps is not None and po is not None:
            sig.append((0.10, _clip((ps - po) / 0.5), "local fuerte" if side == "home" else "buen visitante"))
        _ = name
    elif mt == "total" and sel in ("over", "under") and row.get("line") is not None:
        line = float(row["line"])
        sgn = 1.0 if sel == "over" else -1.0
        fh, fa = _for_against(d, "home", fam), _for_against(d, "away", fam)
        scale = SCALE.get(fam, (1, 1))[1]
        if fh and fa:
            proj = (fh[0] + fa[1]) / 2 + (fa[0] + fh[1]) / 2
            sig.append((0.6, _clip(sgn * (proj - line) / scale),
                        f"sus promedios dan {proj:.1f} {'goles' if fam in ('football', 'hockey') else 'puntos'}"))
        tots = [g["own"] + g["opp"] for s in ("home", "away") for g in (d.get(s) or {}).get("form") or []
                if g.get("own") is not None and g.get("opp") is not None]
        if len(tots) >= 6:
            avg = sum(tots) / len(tots)
            sig.append((0.4, _clip(sgn * (avg - line) / scale),
                        f"sus últimos partidos promedian {avg:.1f}"))
    tw = sum(w for w, _, _ in sig)
    if tw < 0.3:
        return 0.0, []
    score = sum(w * s for w, s, _ in sig) / tw
    notes = [txt for w, s, txt in sorted(sig, key=lambda x: -x[0] * x[1]) if s >= 0.35 and txt]
    return round(score, 3), notes[:2]


# ----------------------------------------------------------------------------- libro de picks


class PickBook:
    BASE = {"ev_sharp": 0.05, "ev_model": 0.07, "move": 0.04, "stats_b": 0.25}
    CONFIRM_S = int(os.getenv("NURI_CONFIRM_S") or 180)  # debe sostenerse 3 min (dos revisiones de la línea)
    MAX_PER_DAY = 8
    MAX_PROPS_DAY = 4        # props de jugadores (líneas viejas que las casas no han copiado)
    MAX_PROPS_LEAGUE_DAY = 2 # y no más de 2 por liga al día (que no se los acabe el primer deporte del día)
    MIN_LEAD_S = 20 * 60     # no emitir si faltan menos de 20 min para empezar
    HORIZON_S = 48 * 3600    # partidos de las próximas 48 h
    MAX_EV = 0.15            # más que esto casi siempre es un momio viejo o una línea distinta
    MAX_MOVE = 0.12          # movimientos más grandes = noticia ya absorbida por todas las casas
    MAX_PER_LEAGUE_DAY = 2   # no amontonar picks de una misma liga (resultados correlacionados)
    MAX_STAKE_DAY = 0.10     # nunca más del 10 % del banco en juego por día

    def __init__(self) -> None:
        self.seen: dict[str, float] = {}
        self.last_close_write: dict[str, float] = {}
        self.confirming = 0
        self.adj = 0.0
        self.adj_note = ""
        self.hist: dict | None = None   # historial de líneas (lo da la versión en la nube)
        self.mkt_adj: dict[str, float] = {}
        self.disabled: set[str] = set()
        self.level_adj: dict[str, float] = {}   # si un nivel no cumple, cuesta más llegar a él
        self.notifier = None     # notify.Notifier (avisos al celular), lo pone main.py
        self._cleanup()
        self.recompute_adjust()

    def _cleanup(self) -> None:
        """Quita picks pendientes que se emitieron con una regla vieja ya corregida (y que no han empezado)."""
        now = time.time()
        for p in db.pending_nuri_picks():
            if p["tier"] == "B" and (p.get("move") or 0) > self.MAX_MOVE and (p["start_ts"] or 0) > now:
                db.delete_nuri_pick(p["id"])
                print(f"[picks] retirado {p['id']}: la línea se movió {p['move'] * 100:.0f} pts (noticia ya absorbida)")

    # ------------------------------------------------------------------ umbrales que se ajustan solos
    def recompute_adjust(self) -> None:
        done = [p for p in db.list_nuri_picks() if p["result"] not in ("pending", "void") and p.get("close_p")]
        done.sort(key=lambda p: p["start_ts"] or 0, reverse=True)
        last = done[:25]
        if len(last) >= 10:
            clv = sum(p["close_p"] - p["p"] for p in last) / len(last)
            if clv < -0.01:
                self.adj, self.adj_note = 0.02, (f"Sus últimos {len(last)} picks no le ganaron al cierre "
                                                 f"({clv * 100:+.1f} pts): NURI subió su exigencia +2 pts.")
            elif clv < 0:
                self.adj, self.adj_note = 0.01, (f"Sus últimos {len(last)} picks quedaron a la par del cierre: "
                                                 f"NURI subió su exigencia +1 pt.")
            else:
                self.adj, self.adj_note = 0.0, (f"Sus últimos {len(last)} picks le ganan al cierre "
                                                f"({clv * 100:+.1f} pts): exigencia normal.")
        else:
            self.adj, self.adj_note = 0.0, ""
        # por tipo de mercado: si uno no le gana al cierre, se endurece y luego se apaga
        self.mkt_adj, self.disabled = {}, set()
        by_mt: dict[str, list] = {}
        for p in done:
            by_mt.setdefault(p["market_type"], []).append(p)
        notes = []
        for mt, ps in by_mt.items():
            ps = ps[:20]
            if len(ps) < 8:
                continue
            clv = sum(p["close_p"] - p["p"] for p in ps) / len(ps)
            if len(ps) >= 12 and clv < -0.015:
                self.disabled.add(mt)
                notes.append(f"apagó «{MT_LABEL.get(mt, mt)}» ({clv * 100:+.1f} pts de CLV)")
            elif clv < -0.005:
                self.mkt_adj[mt] = 0.02
                notes.append(f"exige +2 pts en «{MT_LABEL.get(mt, mt)}»")
        if notes:
            self.adj_note = (self.adj_note + " " if self.adj_note else "") + "Por mercado, NURI " + "; ".join(notes) + "."
        # por nivel de confianza: si los "lock" (o los "alta") no le ganan al cierre, se vuelven más difíciles
        self.level_adj = {}
        for lvl, _ in LEVELS[:-1]:
            ps = [p for p in done if p.get("conf") == lvl][:20]
            if len(ps) >= 10:
                clv = sum(p["close_p"] - p["p"] for p in ps) / len(ps)
                units = sum(p.get("units") or 0 for p in ps)
                if clv < -0.01 or (clv < 0 and units < 0):
                    self.level_adj[lvl] = 5.0

    def level_of(self, score: float, cap: str | None = None) -> str:
        for lvl, th in LEVELS:
            if score >= th + self.level_adj.get(lvl, 0.0):
                if cap == "alta" and lvl == "lock":
                    return "alta"
                return lvl
        return "baja"

    def thresholds(self) -> dict:
        t = dict(self.BASE)
        t["ev_sharp"] += self.adj
        t["ev_model"] += self.adj
        t["move"] += self.adj / 2
        return t

    # ------------------------------------------------------------------ elegir
    def need_ev(self, r: dict, odds: float, t: dict) -> float:
        """Ventaja mínima según el mercado: más exigente donde el error de estimación es mayor."""
        need = t["ev_sharp"] if r["confidence"] == "alta" else t["ev_model"]
        mt, sel = r["market_type"], r["selection"]
        if mt == "1x2" and sel == "draw":
            need += 0.015          # el empate es el resultado más difícil de estimar
        if mt == "dc":
            need -= 0.005          # sale exacto del 1X2 sin margen
        if mt == "btts":
            need += 0.01
        if odds >= 3.0:
            need += 0.02           # momios altos: un error chico en la probabilidad pesa mucho
        return need + self.mkt_adj.get(mt, 0.0)

    def signals(self, r: dict, book: str | None, now: float) -> dict:
        """Movimiento reciente (3 h) de la línea de referencia y del momio de la casa, desde el
        historial que guarda la versión en la nube. Sin historial todo queda en cero."""
        h = (self.hist or {}).get(r["id"]) or []
        out = {"ref_move": 0.0, "book_move": 0.0, "span_h": 0.0}
        if len(h) < 2:
            return out
        past = [x for x in h if now - x[0] >= 3 * 3600]
        base = past[-1] if past else h[0]
        last = h[-1]
        out["span_h"] = (last[0] - base[0]) / 3600
        out["ref_move"] = round(last[1] - base[1], 4)
        if book and book in (last[2] or {}) and book in (base[2] or {}):
            out["book_move"] = round(1 / last[2][book] - 1 / base[2][book], 4)  # + = la casa acortó el momio
        return out

    def candidates(self, result: dict, feed, now: float) -> list[dict]:
        t = self.thresholds()
        rows = result.get("rows_index") or {}
        out = []
        # líneas que tienen precio de referencia propio (no extrapoladas por el modelo)
        ref_lines: dict[tuple, list] = {}
        for r in rows.values():
            if r.get("ref_odds") and r["market_type"] in ("total", "spread", "ah") and r.get("line") is not None:
                ref_lines.setdefault((r["event_id"], r["market_type"]), []).append(float(r["line"]))
        for rid, r in rows.items():
            if r["status"] != "pre" or r["market_type"] not in PICK_MARKETS:
                continue
            if r["market_type"] in self.disabled:
                continue
            st = r.get("start_ts") or 0
            if not (self.MIN_LEAD_S <= st - now <= self.HORIZON_S):
                continue
            if r["confidence"] == "baja":
                continue
            best = (r.get("quotes") or [None])[0]
            if not best and (r.get("move") is None or r["move"] < t["move"]):
                continue  # ni momio real ni movimiento de línea: no hay de dónde salga valor
            p = r["p"]
            ev_raw = (feed.raw.get(r["event_id"]) if hasattr(feed, "raw") else None) or {}
            fam = r["sport"]
            d = feed.dossier.get(r["event_id"]) if hasattr(feed, "dossier") else None
            ml = rows.get(f"{r['event_id']}|{'1x2' if fam == 'football' else 'ml'}|{r['selection']}")
            ml_p = ml["p"] if ml else (p if r["market_type"] in ("1x2", "ml") else None)
            ss, notes = stats_agreement(d, fam, r, ml_p)
            sig = self.signals(r, best["book"] if best else None, now)
            cand = None
            if best:
                need = self.need_ev(r, best["odds"], t)
                stale_book = sig["ref_move"] >= 0.015 and abs(sig["book_move"]) < 0.005
                # la casa alargó nuestro momio sin que el mercado se moviera: suele saber algo (alineación,
                # lesión) que la línea de referencia aún no refleja. Esa "ventaja" es falsa.
                suspect = sig["book_move"] <= -0.02 and abs(sig["ref_move"]) < 0.01
                # Totales y hándicaps: la línea de la casa debe estar a ≤ 0.25 de una línea con precio de
                # referencia. Más lejos, la probabilidad sale de extrapolar el modelo y el error pesa más.
                line_ok = True
                if r["market_type"] in ("total", "spread", "ah") and not r.get("ref_odds"):
                    lines = ref_lines.get((r["event_id"], r["market_type"])) or []
                    line_ok = bool(lines) and r.get("line") is not None and \
                        min(abs(float(r["line"]) - L) for L in lines) <= 0.25
                if (not suspect and line_ok and need <= best["ev"] <= self.MAX_EV and 0.25 <= p <= 0.85
                        and 1.30 <= best["odds"] <= 5.0 and ss >= -0.15):
                    score = best["ev"] * (1 + 0.5 * ss) * (1.3 if stale_book else 1.0)
                    pred_t = self._pred_term(d, r, p)
                    mv_t = 10 if stale_book else (8 if sig["ref_move"] >= 0.01 else (-10 if sig["ref_move"] <= -0.01 else 0))
                    p_t = 8 if p >= 0.6 else 4 if p >= 0.5 else (-8 if p < 0.35 else 0)
                    exact = r["market_type"] not in ("total", "spread", "ah") or bool(r.get("ref_odds"))
                    hrs = (st - now) / 3600   # cerca del partido la línea de referencia es más precisa
                    conf = (50 + min(20.0, max(0.0, (best["ev"] - 0.04) * 200)) + p_t + 15 * ss + mv_t
                            + (5 if r["confidence"] == "alta" else 0) + pred_t + (3 if exact else 0)
                            + (3 if hrs <= 4 else (-5 if hrs >= 24 else 0))
                            - 500 * self.mkt_adj.get(r["market_type"], 0.0))
                    cand = {"tier": "A", "book": best["book"], "odds": best["odds"], "ev": best["ev"],
                            "score": score, "stale_book": stale_book, "conf_score": round(min(conf, 100), 1)}
            if cand is None and r["confidence"] == "alta" and r.get("move") is not None and sig["span_h"] >= 1.5:
                # Sólo con el historial propio (≥ 1.5 h): un movimiento que está pasando AHORA es el que las
                # casas mexicanas todavía no ajustan. El movimiento desde la apertura puede tener días y ya
                # estar absorbido en todas partes.
                mv = sig["ref_move"]
                pred = (d or {}).get("predictor")
                pred_ok = True
                if pred and r["selection"] in ("home", "away") and ml_p is not None:
                    pred_ok = pred[r["selection"]] / 100.0 >= ml_p - 0.02
                # Más de 12 pts casi siempre es una noticia (lesión, alineación) que TODAS las casas ya
                # ajustaron: ahí no queda ventaja. El valor está en movimientos medianos del mercado.
                if (t["move"] <= mv <= self.MAX_MOVE and r["move"] <= 0.20 and 0.35 <= p <= 0.80
                        and ss >= t["stats_b"] and pred_ok and r.get("min_odds") and r["min_odds"] <= 4.0
                        and r["market_type"] in ("1x2", "ml", "spread", "ah", "total")):
                    o = round(r["min_odds"], 2)
                    conf = (40 + min(15.0, max(0.0, (mv - 0.04) * 300)) + 15 * ss + self._pred_term(d, r, p)
                            + (5 if p >= 0.55 else 0))
                    cand = {"tier": "B", "book": None, "odds": o, "ev": r["w"] * (o - 1) - r["l"],
                            "score": 0.6 * mv * (1 + ss), "recent": sig["span_h"] >= 1.5, "mv": mv,
                            "conf_score": round(min(conf, 79.0), 1)}
            if not cand:
                continue
            cand.update({"row": r, "stats_score": ss, "notes": notes, "league_code": ev_raw.get("_league"),
                         "espn_id": ev_raw.get("_espn_id"), "ev_raw": ev_raw, "sig": sig})
            out.append(cand)
        out.sort(key=lambda c: (-c["conf_score"], c["tier"] != "A", -c["score"]))
        return out

    @staticmethod
    def _pred_term(d: dict | None, r: dict, p: float) -> float:
        """El modelo propio de ESPN (BPI / FPI / predictor) como segunda opinión."""
        pred = (d or {}).get("predictor")
        if not pred or r["selection"] not in ("home", "away") or r["market_type"] not in ("1x2", "ml"):
            return 0.0
        pp = (pred.get(r["selection"]) or 0) / 100.0
        return 5.0 if pp >= p - 0.02 else (-8.0 if pp < p - 0.08 else 0.0)

    def _reason(self, c: dict) -> str:
        notes = [n for n in c.get("notes") or [] if n][:1]
        extra = f", y además {notes[0]}" if notes else ""
        if c["tier"] == "A":
            lag = (f" El mercado ya se movió hacia este lado y {book_name(c['book'])} no ha ajustado."
                   if c.get("stale_book") else "")
            return f"{book_name(c['book'])} la paga más alto de lo que vale{extra}.{lag}"
        return (f"El dinero fuerte movió la línea hacia este lado en las últimas horas{extra}. "
                f"Si tu casa no ha ajustado, aprovecha.")

    @staticmethod
    def _prop_reason(c: dict) -> str:
        verb = "subió" if c["side"] == "over" else "bajó"
        form = f" y {c['notes'][0]}" if c.get("notes") else ""
        if c["kind"] == "move":
            return (f"DraftKings ya la {verb} a {c['now_line']:g}{form}. Si tu casa sigue en {c['line']:g}, aprovecha: "
                    f"tardan en copiar estos cambios.")
        return f"DraftKings movió fuerte el momio hacia este lado{form}. Si tu casa sigue pagando como antes, aprovecha."

    def update(self, result: dict, feed, now: float) -> None:
        cands = self.candidates(result, feed, now)
        alive = {c["row"]["id"] for c in cands}
        for k in [k for k in self.seen if k not in alive]:
            self.seen.pop(k, None)
        existing = db.list_nuri_picks(since_ts=now - 10 * 86400)
        taken_events = {p["event_id"] for p in existing}
        per_day: dict[str, int] = {}
        per_league: dict[tuple, int] = {}
        stake_day: dict[str, float] = {}
        for p in existing:
            day = datetime.fromtimestamp(p["start_ts"] or now, MX_TZ).date().isoformat()
            per_day[day] = per_day.get(day, 0) + 1
            per_league[(day, p["league"])] = per_league.get((day, p["league"]), 0) + 1
            stake_day[day] = stake_day.get(day, 0.0) + (p.get("stake_pct") or 0.0)
        confirming = 0
        for c in cands:
            r = c["row"]
            rid = r["id"]
            self.seen.setdefault(rid, now)
            if r["event_id"] in taken_events:
                continue
            if now - self.seen[rid] < self.CONFIRM_S:
                confirming += 1
                continue
            day = datetime.fromtimestamp(r["start_ts"], MX_TZ).date().isoformat()
            if per_day.get(day, 0) >= self.MAX_PER_DAY:
                continue
            lk = (day, r["league"])
            if per_league.get(lk, 0) >= self.MAX_PER_LEAGUE_DAY:
                continue
            level = self.level_of(c["conf_score"], cap="alta" if c["tier"] == "B" else None)
            stake = round(kelly_pct(c["odds"], r["w"], r["l"]) * STAKE_MULT[level], 4)
            if stake_day.get(day, 0.0) + stake > self.MAX_STAKE_DAY:
                continue
            ev_raw = c["ev_raw"]
            rec = {
                "id": rid, "created_at": now, "tier": c["tier"], "event_id": r["event_id"],
                "espn_id": c["espn_id"], "league_code": c["league_code"], "sport": r["sport"],
                "league": r["league"], "home": (ev_raw.get("home") or {}).get("name"),
                "away": (ev_raw.get("away") or {}).get("name"),
                "home_logo": (ev_raw.get("home") or {}).get("logo"), "away_logo": (ev_raw.get("away") or {}).get("logo"),
                "start_ts": r["start_ts"], "market_key": r["market_key"], "market_type": r["market_type"],
                "market_label": r["market_label"], "line": r.get("line"), "selection": r["selection"],
                "selection_label": r["selection_label"], "book": c["book"], "odds": c["odds"],
                "min_odds": round(r["min_odds"], 3) if r.get("min_odds") else None, "p": round(r["p"], 4),
                "ev": round(c["ev"], 4), "score": round(c["score"], 4), "stats_score": c["stats_score"],
                "move": r.get("move"), "stake_pct": stake, "reason": self._reason(c),
                "conf": level, "conf_score": c["conf_score"],
            }
            if db.add_nuri_pick(rec):
                taken_events.add(r["event_id"])
                per_day[day] = per_day.get(day, 0) + 1
                per_league[lk] = per_league.get(lk, 0) + 1
                stake_day[day] = stake_day.get(day, 0.0) + (rec.get("stake_pct") or 0.0)
                if self.notifier:
                    self.notifier.pick(rec, MX_TZ)
                print(f"[picks] nuevo pick {c['tier']} · {rec['league']} · {rec['home']} vs {rec['away']} · "
                      f"{rec['selection_label']} @ {rec['odds']}")
        self.confirming = confirming
        self._update_props(feed, now, existing, per_day, stake_day)
        self._track_close(result, feed, now)

    # ------------------------------------------------------------------ props de jugadores
    def _update_props(self, feed, now: float, existing: list, per_day: dict, stake_day: dict) -> None:
        if db.get_setting("fix_stake_v34") is None:   # una sola vez: los primeros props salieron sin ajuste por nivel
            for p in existing:
                if p.get("tier") == "P" and p.get("conf") and p["result"] == "pending" and p["created_at"] < 1795329600:
                    db.update_nuri_pick(p["id"], stake_pct=round((p.get("stake_pct") or 0.01) * STAKE_MULT[p["conf"]], 4))
            db.set_setting("fix_stake_v34", "1")
        src = getattr(feed, "props_src", None)
        if src is None or "prop" in self.disabled:
            return
        import props as P
        injuries = {}
        for eid, d in (getattr(feed, "dossier", None) or {}).items():
            st = d.get("_inj_status") or {}
            injuries[eid] = {**st, **{P.norm(k): v for k, v in st.items()}}
        min_p = P.MIN_P + self.mkt_adj.get("prop", 0.0)
        allc = src.candidates(feed, now, injuries, min_p=P.MIN_P - 0.05)
        # picks que salieron antes de existir los niveles: se les asigna el de hoy
        byid = {f"{c['event_id']}|prop:{c['stat']}:{c['athlete']}|{c['side']}": c for c in allc}
        for p in existing:
            if p.get("market_type") == "prop" and p["result"] == "pending" and not p.get("conf"):
                c = byid.get(p["id"])
                sc = c["conf_score"] if c else 55.0
                lvl = self.level_of(sc)
                db.update_nuri_pick(p["id"], conf=lvl, conf_score=sc,
                                    stake_pct=round((p.get("stake_pct") or 0.01) * STAKE_MULT[lvl], 4))
        cands = [c for c in allc if c["p"] >= min_p]
        taken = {p["event_id"] for p in existing if p.get("market_type") == "prop"}
        props_day: dict[str, int] = {}
        props_lg: dict[tuple, int] = {}
        for p in existing:
            if p.get("market_type") == "prop":
                day = datetime.fromtimestamp(p["start_ts"] or now, MX_TZ).date().isoformat()
                props_day[day] = props_day.get(day, 0) + 1
                props_lg[(day, p["league"])] = props_lg.get((day, p["league"]), 0) + 1
        for c in cands:
            e = c["ev_raw"]
            if c["event_id"] in taken:
                continue
            day = datetime.fromtimestamp(e["start_ts"], MX_TZ).date().isoformat()
            if props_day.get(day, 0) >= self.MAX_PROPS_DAY or per_day.get(day, 0) >= self.MAX_PER_DAY:
                continue
            if props_lg.get((day, e.get("league")), 0) >= self.MAX_PROPS_LEAGUE_DAY:
                continue
            level = self.level_of(c.get("conf_score") or 0.0)
            stake = round(kelly_pct(c["min_odds"], c["p"], 1 - c["p"]) * STAKE_MULT[level], 4)
            if stake_day.get(day, 0.0) + stake > self.MAX_STAKE_DAY:
                continue
            side_txt = "Más" if c["side"] == "over" else "Menos"
            rec = {
                "id": f"{c['event_id']}|prop:{c['stat']}:{c['athlete']}|{c['side']}", "created_at": now, "tier": "P",
                "event_id": c["event_id"], "espn_id": e.get("_espn_id"), "league_code": e.get("_league"),
                "sport": e.get("sport"), "league": e.get("league"), "home": (e.get("home") or {}).get("name"),
                "away": (e.get("away") or {}).get("name"), "home_logo": (e.get("home") or {}).get("logo"),
                "away_logo": (e.get("away") or {}).get("logo"), "start_ts": e.get("start_ts"),
                "market_key": f"prop:{c['stat']}:{c['athlete']}:{c['line']:g}", "market_type": "prop",
                "market_label": c["player"], "line": c["line"], "selection": c["side"],
                "selection_label": f"{side_txt} de {c['line']:g} {c['label']}", "book": None,
                "odds": c["min_odds"], "min_odds": c["min_odds"], "p": round(c["p"], 4), "ev": c["ev"],
                "score": round(c["score"], 4), "stats_score": 0.0,
                "move": round((c["now_line"] or 0) - (c["open_line"] or 0), 2), "stake_pct": stake,
                "reason": self._prop_reason(c), "conf": level, "conf_score": c.get("conf_score"),
            }
            if db.add_nuri_pick(rec):
                taken.add(c["event_id"])
                props_day[day] = props_day.get(day, 0) + 1
                props_lg[(day, e.get("league"))] = props_lg.get((day, e.get("league")), 0) + 1
                per_day[day] = per_day.get(day, 0) + 1
                stake_day[day] = stake_day.get(day, 0.0) + stake
                if self.notifier:
                    self.notifier.pick(rec, MX_TZ)
                print(f"[picks] nuevo prop · {rec['league']} · {c['player']} · {rec['selection_label']} (desde {c['min_odds']})")
        self._track_close_props(feed, now)

    def _track_close_props(self, feed, now: float) -> None:
        """Línea de cierre de los props: cuánto vale la línea del pick con el mercado más reciente."""
        src = getattr(feed, "props_src", None)
        if src is None:
            return
        import props as P
        for p in db.pending_nuri_picks():
            if p.get("market_type") != "prop" or (p["start_ts"] or 0) <= now:
                continue
            k = P.parse_key(p.get("market_key") or "")
            pr = (src.props.get(p["event_id"]) or {}).get(f"{k[0]}:{k[1]}") if k else None
            if pr:
                db.update_nuri_pick(p["id"], close_p=round(P.price_line(pr, k[2], p["selection"]), 4), close_ts=now)

    # ------------------------------------------------------------------ línea de cierre (CLV)
    def _track_close(self, result: dict, feed, now: float) -> None:
        rows = result.get("rows_index") or {}
        for p in db.pending_nuri_picks():
            e = feed.raw.get(p["event_id"]) if hasattr(feed, "raw") else None
            if not e or e.get("status") != "pre":
                continue
            r = rows.get(p["id"])
            if not r:
                continue
            if now - self.last_close_write.get(p["id"], 0) < 120:
                continue
            self.last_close_write[p["id"]] = now
            q = next((x for x in r.get("quotes") or [] if x["book"] == p["book"]), None)
            db.update_nuri_pick(p["id"], close_p=round(r["p"], 4), close_odds=q["odds"] if q else None, close_ts=now)

    # ------------------------------------------------------------------ calificar
    @staticmethod
    def grade(p: dict, h: int, a: int) -> tuple[str, float]:
        mt, sel, line = p["market_type"], p["selection"], p.get("line")
        if mt == "1x2":
            win = {"home": h > a, "draw": h == a, "away": h < a}[sel]
            w, l = (1.0, 0.0) if win else (0.0, 1.0)
        elif mt == "dc":
            win = {"1x": h >= a, "x2": a >= h, "12": h != a}[sel]
            w, l = (1.0, 0.0) if win else (0.0, 1.0)
        elif mt == "btts":
            yes = h > 0 and a > 0
            win = yes if sel == "yes" else not yes
            w, l = (1.0, 0.0) if win else (0.0, 1.0)
        elif mt == "ml":
            if h == a:
                w, l = 0.0, 0.0
            else:
                win = (h > a) if sel == "home" else (a > h)
                w, l = (1.0, 0.0) if win else (0.0, 1.0)
        elif mt == "total":
            tot = np.array([float(h + a)])
            w, l = settle_wl(tot, float(line)) if sel == "over" else settle_wl(-tot, -float(line))
        elif mt in ("spread", "ah"):
            dlt = np.array([float(h - a)])
            w, l = settle_wl(dlt, -float(line)) if sel == "home" else settle_wl(-dlt, float(line))
        else:
            return "void", 0.0
        o = float(p["odds"])
        units = round(w * (o - 1.0) - l, 4)
        if w >= 0.99:
            res = "won"
        elif l >= 0.99:
            res = "lost"
        elif w > 0 and l == 0:
            res = "half_won"
        elif l > 0 and w == 0:
            res = "half_lost"
        else:
            res = "push"
        return res, units

    async def settle(self, feed, now: float) -> int:
        """Califica los picks cuyos partidos ya terminaron (aunque NURI haya estado cerrado)."""
        n = 0
        fetch = getattr(feed, "fetch_result", None)
        if fetch is None:
            return 0
        for p in db.pending_nuri_picks():
            if (p["start_ts"] or now) > now - 100 * 60:
                continue
            if p.get("market_type") == "prop":
                n += await self._settle_prop(feed, p, now)
                continue
            try:
                res = await fetch(p.get("espn_id") or p["event_id"].replace("espn-", ""), p.get("league_code"))
            except Exception as exc:
                print(f"[picks] no pude revisar {p['id']}: {exc!r}")
                continue
            if not res:
                continue
            if res["state"] == "void":
                db.update_nuri_pick(p["id"], result="void", units=0.0, settled_at=now)
                n += 1
                continue
            if res["state"] != "final":
                continue
            h, a = res["reg"] if p["sport"] == "football" and res.get("reg") else (res["home"], res["away"])
            result, units = self.grade(p, int(h), int(a))
            db.update_nuri_pick(p["id"], result=result, units=units, final_home=int(h), final_away=int(a),
                                settled_at=now)
            print(f"[picks] calificado {p['id']}: {result} ({units:+.2f} u)")
            n += 1
            if self.notifier:
                wk = week_start(p["start_ts"] or now).timestamp()
                same = [x for x in db.list_nuri_picks(since_ts=wk) if week_start(x["start_ts"] or now).timestamp() == wk]
                sm = self._summary(same)
                line = f"Semana: {sm['won']:g}-{sm['lost']:g} · {sm['units']:+.2f} u"
                self.notifier.result(p, result, units, int(h), int(a), line)
        if n:
            self.recompute_adjust()
        return n

    async def _settle_prop(self, feed, p: dict, now: float) -> int:
        box = getattr(feed, "fetch_box", None)
        if box is None:
            return 0
        import props as P
        try:
            state, js = await box(p.get("espn_id") or p["event_id"].replace("espn-", ""), p.get("league_code"))
        except Exception as exc:
            print(f"[picks] no pude revisar {p['id']}: {exc!r}")
            return 0
        if state == "void":
            result, units = "void", 0.0
        elif state != "final" or not js:
            return 0
        else:
            result, units = P.grade(p, js)
        k = P.parse_key(p.get("market_key") or "")
        final = P.stat_from_box(js, p.get("league_code") or "", k[1], k[0]) if (k and js) else None
        db.update_nuri_pick(p["id"], result=result, units=units, settled_at=now,
                            final_home=int(final) if final is not None else None, final_away=None)
        print(f"[picks] calificado {p['id']}: {result} ({units:+.2f} u)")
        if self.notifier:
            wk = week_start(p["start_ts"] or now).timestamp()
            same = [x for x in db.list_nuri_picks(since_ts=wk) if week_start(x["start_ts"] or now).timestamp() == wk]
            sm = self._summary(same)
            self.notifier.result(p, result, units, int(final) if final is not None else None, None,
                                 f"Semana: {sm['won']:g}-{sm['lost']:g} · {sm['units']:+.2f} u")
        return 1

    def weekly_check(self, now: float) -> None:
        """Los lunes desde las 8:00 manda al cel el resumen de la semana que terminó (una sola vez)."""
        if not self.notifier:
            return
        local = datetime.fromtimestamp(now, MX_TZ)
        if local.weekday() != 0 or local.hour < 8:
            return
        prev = week_start(now) - timedelta(days=7)
        key = prev.date().isoformat()
        if db.get_setting("ntfy_weekly_sent") == key:
            return
        ps = [x for x in db.list_nuri_picks(since_ts=prev.timestamp())
              if week_start(x["start_ts"] or now) == prev]
        s = self._summary(ps)
        self.notifier.weekly(week_label(prev), s, self._verdict(s, self._summary(db.list_nuri_picks())))
        db.set_setting("ntfy_weekly_sent", key)

    # ------------------------------------------------------------------ lo que ve el usuario
    def view(self, feed, now: float) -> dict:
        every = db.list_nuri_picks()
        picks = [p for p in every if (p["start_ts"] or now) >= now - 70 * 86400]
        wk = week_start(now)
        wk_ts = wk.timestamp()
        current, settled, history = [], [], []
        for p in picks:
            item = {k: p[k] for k in ("id", "tier", "league", "sport", "home", "away", "home_logo", "away_logo",
                                     "start_ts", "market_label", "selection_label", "odds", "min_odds", "p", "ev",
                                     "stake_pct", "reason", "result", "units", "final_home", "final_away",
                                     "created_at", "close_p", "close_odds", "line", "market_type", "conf",
                                     "conf_score")}
            item["book"] = book_name(p["book"]) if p["book"] else None
            item["clv"] = round(p["close_p"] - p["p"], 4) if p.get("close_p") is not None else None
            e = feed.raw.get(p["event_id"]) if hasattr(feed, "raw") else None
            if e:
                item["live"] = {"status": e.get("status"), "score": e.get("score"), "clock": e.get("clock")}
            if p["result"] == "pending":
                current.append(item)
            else:
                history.append(item)
                if (p["start_ts"] or 0) >= wk_ts:
                    settled.append(item)
        current.sort(key=lambda x: x["start_ts"] or 0)
        settled.sort(key=lambda x: x["start_ts"] or 0, reverse=True)
        history.sort(key=lambda x: x["start_ts"] or 0, reverse=True)
        return {"week": {"label": week_label(wk), "start": wk_ts}, "active": current, "settled": settled,
                "history": history,
                "report": self.report(picks, now, every), "confirming": self.confirming,
                "thresholds": self.thresholds(), "adjust_note": self.adj_note}

    @staticmethod
    def _summary(ps: list[dict]) -> dict:
        done = [p for p in ps if p["result"] not in ("pending", "void")]
        won = sum(1 for p in done if p["result"] == "won") + 0.5 * sum(1 for p in done if p["result"] == "half_won")
        lost = sum(1 for p in done if p["result"] == "lost") + 0.5 * sum(1 for p in done if p["result"] == "half_lost")
        push = sum(1 for p in done if p["result"] == "push")
        units = round(sum(p["units"] or 0 for p in done), 2)
        clvs = [p["close_p"] - p["p"] for p in done if p.get("close_p") is not None]
        return {"picks": len(ps), "settled": len(done), "pending": sum(1 for p in ps if p["result"] == "pending"),
                "won": won, "lost": lost, "push": push, "units": units,
                "roi": round(units / len(done), 4) if done else None,
                "expected": round(sum(p["p"] for p in done), 2) if done else None,
                "clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
                "avg_odds": round(sum(p["odds"] for p in done) / len(done), 2) if done else None}

    def report(self, picks: list[dict], now: float, every: list[dict] | None = None) -> dict:
        weeks: dict[float, list] = {}
        for p in picks:
            weeks.setdefault(week_start(p["start_ts"] or now).timestamp(), []).append(p)
        cur = week_start(now).timestamp()
        rows = []
        for ts in sorted(weeks, reverse=True):
            s = self._summary(weeks[ts])
            s.update({"start": ts, "label": week_label(datetime.fromtimestamp(ts, MX_TZ)), "current": ts == cur})
            rows.append(s)
        this = next((r for r in rows if r["current"]), None) or {**self._summary([]), "start": cur,
                                                                   "label": week_label(week_start(now)), "current": True}
        total = self._summary(every if every is not None else picks)
        allp = every if every is not None else picks
        levels = {lvl: self._summary([p for p in allp if p.get("conf") == lvl]) for lvl, _ in LEVELS}
        return {"this_week": this, "weeks": rows[:12], "all_time": total, "verdict": self._verdict(this, total),
                "levels": levels}

    @staticmethod
    def _verdict(w: dict, total: dict) -> str:
        if not w["settled"]:
            if w["pending"]:
                return "Esta semana todavía no termina ningún partido de los picks."
            return "Esta semana todavía no hay picks calificados."
        rec = f"{w['won']:g}-{w['lost']:g}" + (f"-{w['push']}" if w["push"] else "")
        txt = (f"Semana {rec}: {w['units']:+.2f} unidades (ROI {w['roi'] * 100:+.1f} %). "
               f"Esperaba {w['expected']:.1f} aciertos y salieron {w['won']:g}.")
        if w.get("clv") is not None:
            if w["clv"] > 0.005:
                txt += f" La línea se movió a favor de sus picks después de darlos (+{w['clv'] * 100:.1f} pts): buena señal."
            elif w["clv"] < -0.005:
                txt += f" La línea se movió en contra después de darlos ({w['clv'] * 100:.1f} pts): señal de alerta."
        if total["settled"] < 30:
            txt += " Con menos de 30 picks, el resultado todavía depende mucho de la suerte."
        return txt
