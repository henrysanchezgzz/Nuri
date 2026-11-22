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
    MAX_PER_DAY = 5
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
                if (not suspect and need <= best["ev"] <= self.MAX_EV and 0.25 <= p <= 0.85
                        and 1.30 <= best["odds"] <= 5.0 and ss >= -0.15):
                    score = best["ev"] * (1 + 0.5 * ss) * (1.3 if stale_book else 1.0)
                    cand = {"tier": "A", "book": best["book"], "odds": best["odds"], "ev": best["ev"],
                            "score": score, "stale_book": stale_book}
            if cand is None and r["confidence"] == "alta" and r.get("move") is not None:
                # con historial usamos el movimiento de las últimas horas (más útil para encontrar casas
                # que no han ajustado); sin historial, el de la apertura.
                mv = sig["ref_move"] if sig["span_h"] >= 1.5 else r["move"]
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
                    cand = {"tier": "B", "book": None, "odds": o, "ev": r["w"] * (o - 1) - r["l"],
                            "score": 0.6 * mv * (1 + ss), "recent": sig["span_h"] >= 1.5, "mv": mv}
            if not cand:
                continue
            cand.update({"row": r, "stats_score": ss, "notes": notes, "league_code": ev_raw.get("_league"),
                         "espn_id": ev_raw.get("_espn_id"), "ev_raw": ev_raw, "sig": sig})
            out.append(cand)
        out.sort(key=lambda c: (c["tier"] != "A", -c["score"]))
        return out

    def _reason(self, c: dict) -> str:
        r = c["row"]
        notes = c["notes"]
        extra = (", y además " + " y ".join(notes)) if notes else ""
        if c["tier"] == "A":
            lag = (f" El mercado se movió {c['sig']['ref_move'] * 100:.1f} pts a su favor y "
                   f"{book_name(c['book'])} no ha ajustado.") if c.get("stale_book") else ""
            return (f"{book_name(c['book'])} paga {c['odds']:.2f} y lo justo es {r['fair']:.2f}: "
                    f"{c['ev']:+.1%} a tu favor{extra}.{lag}")
        when = "en las últimas horas" if c.get("recent") else "desde que abrió"
        return (f"La línea de mercado se movió {c.get('mv', r['move']) * 100:.0f} pts a su favor {when}{extra}. "
                f"Las casas mexicanas suelen tardar en ajustar.")

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
            stake = kelly_pct(c["odds"], r["w"], r["l"])
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
                "move": r.get("move"), "stake_pct": kelly_pct(c["odds"], r["w"], r["l"]),
                "reason": self._reason(c),
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
        self._track_close(result, feed, now)

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
                                     "created_at", "close_p", "close_odds", "line", "market_type")}
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
        return {"this_week": this, "weeks": rows[:12], "all_time": total, "verdict": self._verdict(this, total)}

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
