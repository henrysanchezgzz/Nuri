"""
NURI · 누리 — quant_engine.py
=============================

Motor cuantitativo de 7 capas. Todo lo que aquí ocurre se queda en el backend;
el frontend sólo recibe: qué apostar, en qué casa, cuánto y por qué.

  Capa 1  De-vigging sharp con el modelo de Shin (parámetro z resuelto con brentq).
  Capa 2  Monte Carlo vectorizado de 25,000 iteraciones:
            · Dixon-Coles bivariado (fútbol), Poisson (NHL), Binomial Negativa (MLB)
            · Gaussiano bivariado por pace / net rating (NBA, NFL)
  Capa 3  Actualización bayesiana in-play (Gamma-Poisson para goles/córners/tarjetas,
          Normal-Normal para pace, regla de Bayes explícita para noticias de lesiones).
  Capa 4  Cópula gaussiana jugador↔equipo para Player Props + redistribución de Usage.
  Capa 5  Detector de líneas dormidas (stale lines) y steam moves sharp→casas MX.
  Capa 6  Evaluación de todos los mercados (+EV ≥ umbral) y surebets (Σ 1/cuota < 1).
  Capa 7  Kelly fraccionado 0.25x con tope 6 % y auditoría CLV.
"""
from __future__ import annotations

import json
import math
import time
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import optimize, stats

# --------------------------------------------------------------------------------------
# Configuración
# --------------------------------------------------------------------------------------

REF_BOOK = "DraftKings (ref.)"        # línea de mercado pública que trae ESPN (referencia, no para apostar)
OPEN_BOOK = "DraftKings (apertura)"   # la misma línea al abrir: sirve para medir el movimiento
SHARP_BOOKS = ("Pinnacle", "Betfair Exchange", REF_BOOK)
MX_BOOKS = ("Caliente.mx", "Codere", "Stake", "Bet365", "Betway", "Novibet", "Betano", "Winpot", "Strendus",
            "Playdoit", "RushBet", "BetMexico", "Betmaster", "MexBoss")
MANUAL_BOOKS = ("Caliente.mx", "Codere", "Stake", "Bet365", "Betway", "Novibet", "BetVIP")
SLOW_BOOKS = ("Bet365",)   # casas cuyo momio llega con minutos de retraso (no sirven para "línea dormida")


@dataclass
class EngineConfig:
    mc_iterations: int = 25_000
    ev_threshold: float = 0.045
    kelly_fraction: float = 0.25
    kelly_cap: float = 0.06
    max_credible_ev: float = 0.25      # EV mayor a esto casi siempre es un error de mapeo de datos
    max_model_ev: float = 0.12         # tope cuando la probabilidad viene sólo del modelo (sin sharp)
    min_stake: int = 10                # mínimo típico de apuesta en casas MX (MXN)
    steam_threshold: float = 0.02      # movimiento sharp mínimo (probabilidad absoluta)
    steam_window_s: float = 90.0       # ventana para detectar el movimiento
    min_prob: float = 0.03             # no evaluar selecciones casi imposibles
    max_prob: float = 0.97


# --------------------------------------------------------------------------------------
# Capa 1 · Shin
# --------------------------------------------------------------------------------------

def shin_devig(odds: list[float] | np.ndarray) -> tuple[np.ndarray, float]:
    """Devuelve (probabilidades reales, z de Shin) para un mercado completo.

    π_i = 1/o_i,  B = Σ π_i
    p_i(z) = ( sqrt(z² + 4(1−z)·π_i²/B) − z ) / ( 2(1−z) )
    Se resuelve z ∈ [0, 0.5) tal que Σ p_i(z) = 1. El modelo de Shin asume una fracción z
    de dinero informado; corrige el sesgo favorito-underdog mejor que normalizar.
    """
    o = np.asarray(odds, dtype=float)
    if o.ndim != 1 or o.size < 2 or not np.all(np.isfinite(o)) or np.any(o <= 1.0):
        raise ValueError("Cuotas inválidas para de-vig")
    pi = 1.0 / o
    booksum = float(pi.sum())
    if booksum <= 1.0 + 1e-12:
        # Sin margen (o mercado con sobre-oferta): normalización simple.
        return pi / booksum, 0.0

    def shin_probs(z: float) -> np.ndarray:
        return (np.sqrt(z * z + 4.0 * (1.0 - z) * pi * pi / booksum) - z) / (2.0 * (1.0 - z))

    def residual(z: float) -> float:
        return float(shin_probs(z).sum() - 1.0)

    hi = 0.5
    if residual(hi) > 0:  # margen absurdo: caer a normalización
        return pi / booksum, 0.0
    z = optimize.brentq(residual, 0.0, hi, xtol=1e-12, maxiter=200)
    p = shin_probs(z)
    return p / p.sum(), float(z)


def apply_margin(probs: np.ndarray | list[float], margin: float) -> np.ndarray:
    """Inverso aproximado: agrega margen con el método de potencia (p_i^c, c<1),
    que reproduce el sesgo favorito-underdog típico. Devuelve cuotas decimales."""
    p = np.clip(np.asarray(probs, dtype=float), 1e-6, 1.0)
    p = p / p.sum()
    target = 1.0 + max(margin, 0.0)
    if margin <= 0:
        return 1.0 / p
    f = lambda c: float(np.sum(p ** c) - target)
    c = optimize.brentq(f, 0.05, 1.0)
    return 1.0 / (p ** c)


# --------------------------------------------------------------------------------------
# Utilidades de liquidación (win / push / lose) para MC
# --------------------------------------------------------------------------------------

def _is_quarter(t: float) -> bool:
    q = t * 4.0
    return abs(q - round(q)) < 1e-9 and int(round(q)) % 2 == 1


def settle_wl(values: np.ndarray, threshold: float) -> tuple[float, float]:
    """Para la apuesta 'values > threshold' devuelve (W, L): peso esperado de ganar y perder.
    Push = 1 − W − L. Las líneas de cuarto (x.25 / x.75) se dividen en dos medias apuestas."""
    if _is_quarter(threshold):
        w1, l1 = settle_wl(values, threshold - 0.25)
        w2, l2 = settle_wl(values, threshold + 0.25)
        return (w1 + w2) / 2.0, (l1 + l2) / 2.0
    w = float(np.mean(values > threshold + 1e-9))
    l = float(np.mean(values < threshold - 1e-9))
    return w, l


def ev_of(odds: float, w: float, l: float) -> float:
    return w * (odds - 1.0) - l


def fair_odds_of(w: float, l: float) -> float:
    if w <= 1e-9:
        return 1000.0
    return 1.0 + l / w


# --------------------------------------------------------------------------------------
# Capa 2 · Dixon-Coles + Poisson + Binomial Negativa
# --------------------------------------------------------------------------------------

MAX_GOALS = 10


def _clamp_rho(lam: float, mu: float, rho: float) -> float:
    lo = max(-1.0 / max(lam, 1e-9), -1.0 / max(mu, 1e-9))
    hi = min(1.0 / max(lam * mu, 1e-9), 1.0)
    return float(min(max(rho, lo + 1e-6), hi - 1e-6))


def dc_score_matrix(lam: float, mu: float, rho: float = -0.06, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Matriz P(i goles locales, j goles visitantes) con la corrección τ de Dixon-Coles."""
    lam = max(lam, 1e-6)
    mu = max(mu, 1e-6)
    g = np.arange(max_goals + 1)
    m = np.outer(stats.poisson.pmf(g, lam), stats.poisson.pmf(g, mu))
    r = _clamp_rho(lam, mu, rho)
    m[0, 0] *= 1.0 - lam * mu * r
    m[0, 1] *= 1.0 + lam * r
    m[1, 0] *= 1.0 + mu * r
    m[1, 1] *= 1.0 - r
    m = np.clip(m, 0.0, None)
    return m / m.sum()


def nb_score_matrix(lam: float, mu: float, dispersion: float = 4.0, max_runs: int = 25) -> np.ndarray:
    """Matriz para béisbol: carreras ~ Binomial Negativa (Gamma-Poisson) independientes."""
    g = np.arange(max_runs + 1)
    r = dispersion
    ph = stats.nbinom.pmf(g, r, r / (r + max(lam, 1e-6)))
    pa = stats.nbinom.pmf(g, r, r / (r + max(mu, 1e-6)))
    m = np.outer(ph, pa)
    return m / m.sum()


def _matrix_outcomes(m: np.ndarray, ch: int, ca: int, line: float | None) -> tuple[float, float, float, float | None]:
    n = m.shape[0]
    i = np.arange(n)[:, None] + ch
    j = np.arange(n)[None, :] + ca
    ph = float(m[i > j].sum())
    pd = float(m[i == j].sum())
    pa = float(m[i < j].sum())
    po = None
    if line is not None:
        po = float(m[(i + j) > line].sum())
    return ph, pd, pa, po


def fit_two_rates(
    matrix_fn,
    targets: dict[str, float],
    ch: int,
    ca: int,
    total_line: float | None,
    guess: tuple[float, float],
    tie_split: float | None = None,
) -> tuple[float, float]:
    """Ajusta (λ, μ) restantes para que el modelo reproduzca las probabilidades sharp.

    targets admite: home/draw/away (3 vías) o home/away (2 vías con empate repartido
    tie_split hacia local) y over (para total_line).
    """
    def loss(x):
        lam, mu = math.exp(x[0]), math.exp(x[1])
        m = matrix_fn(lam, mu)
        ph, pd, pa, po = _matrix_outcomes(m, ch, ca, total_line)
        err = 0.0
        if "draw" in targets:
            err += (ph - targets["home"]) ** 2 + (pd - targets["draw"]) ** 2 + (pa - targets["away"]) ** 2
        elif "home" in targets:
            split = 0.5 if tie_split is None else tie_split
            err += (ph + pd * split - targets["home"]) ** 2 * 2.0
        if po is not None and "over" in targets:
            err += (po - targets["over"]) ** 2
        return err

    x0 = np.log(np.clip(np.asarray(guess, dtype=float), 0.02, 12.0))
    res = optimize.minimize(loss, x0, method="L-BFGS-B", bounds=[(math.log(0.005), math.log(15.0))] * 2)
    lam, mu = float(math.exp(res.x[0])), float(math.exp(res.x[1]))
    return lam, mu


def sample_matrix(m: np.ndarray, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    flat = m.ravel()
    idx = rng.choice(flat.size, size=n, p=flat / flat.sum())
    k = m.shape[1]
    return idx // k, idx % k


# --------------------------------------------------------------------------------------
# Capa 3 · Bayes
# --------------------------------------------------------------------------------------

def gamma_poisson_posterior(prior_rate: float, prior_strength: float, observed: float, exposure: float) -> tuple[float, float]:
    """Prior Gamma(α = r0·k, β = k) sobre la tasa por partido; observación de 'observed'
    eventos en 'exposure' fracciones de partido. Devuelve (α_post, β_post)."""
    alpha = prior_rate * prior_strength + max(observed, 0.0)
    beta = prior_strength + max(exposure, 0.0)
    return alpha, beta


def gamma_poisson_predictive(alpha: float, beta: float, remaining: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """Predictiva Binomial Negativa del conteo restante."""
    if remaining <= 1e-6:
        return np.zeros(n, dtype=int)
    p = beta / (beta + remaining)
    return rng.negative_binomial(max(alpha, 1e-6), p, size=n)


def normal_shrinkage(prior_mean: float, prior_weight: float, observed_rate: float, data_weight: float) -> float:
    """Media posterior Normal-Normal (pesos en 'fracciones de partido')."""
    if data_weight <= 1e-9:
        return prior_mean
    return (prior_weight * prior_mean + data_weight * observed_rate) / (prior_weight + data_weight)


def bayes_posterior(prior: float, likelihood_if_true: float, likelihood_if_false: float) -> float:
    """P(A|B) = P(B|A)·P(A) / P(B)."""
    num = likelihood_if_true * prior
    den = num + likelihood_if_false * (1.0 - prior)
    return num / den if den > 0 else prior


# Probabilidad de que un jugador NO juegue según el reporte de lesiones (regla de Bayes).
# Prior de baja 8 %; verosimilitud de cada reporte dado que juega / no juega.
INJURY_LIKELIHOOD = {
    "active": (0.97, 0.10),
    "probable": (0.08, 0.10),
    "questionable": (0.12, 0.45),
    "doubtful": (0.02, 0.30),
    "out": (0.0001, 0.15),
}


def p_player_out(status: str) -> float:
    if status not in INJURY_LIKELIHOOD:
        return 0.0
    l_plays, l_out = INJURY_LIKELIHOOD[status]
    # A = "no juega"
    return bayes_posterior(0.08, l_out, l_plays)


RED_CARD_ATTACK = 0.68   # el equipo con 10 genera ~32 % menos
RED_CARD_OPP = 1.25      # el rival genera ~25 % más


# --------------------------------------------------------------------------------------
# Capa 4 · Cópula gaussiana
# --------------------------------------------------------------------------------------

STAT_CORR = {"pts": 0.45, "reb": 0.15, "ast": 0.30, "threes": 0.30, "pass_yds": 0.50, "rush_yds": 0.35,
             "rec_yds": 0.40, "sog": 0.25, "shots_ot": 0.35, "k": -0.30, "goals": 0.55}

STAT_LABEL = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "threes": "Triples",
              "pass_yds": "Yardas por pase", "rush_yds": "Yardas por tierra", "rec_yds": "Yardas por recepción",
              "sog": "Tiros a gol", "shots_ot": "Tiros a puerta", "k": "Ponches", "goals": "Goles"}


def usage_shifts(players: list[dict], team: str) -> tuple[dict[str, float], float, list[str]]:
    """Redistribuye el Usage de bajas (ponderado por P(baja) bayesiana) entre los compañeros.
    Devuelve (shift por jugador, multiplicador del total del equipo, nombres de bajas)."""
    roster = [p for p in players if p.get("team") == team]
    out_mass, missing = 0.0, []
    for p in roster:
        q = p_player_out(p.get("status", "active"))
        if q > 0.5:
            missing.append(p["name"])
        out_mass += q * float(p.get("usage", 0.2))
    shifts: dict[str, float] = {}
    active = [p for p in roster if p_player_out(p.get("status", "active")) < 0.5]
    # El uso que deja la baja se reparte entre los ~8 jugadores de rotación (que suman 100 % del uso);
    # sólo ~35 % se convierte en producción extra para cada titular (el resto lo absorben suplentes).
    out_mass = min(out_mass, 0.6)
    for p in active:
        shifts[p["name"]] = 0.35 * out_mass / max(1.0 - out_mass, 0.4)
    team_mult = 1.0 - 0.12 * out_mass
    return shifts, team_mult, missing


def team_normal_scores(team_remaining: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Transforma el total simulado del equipo a puntajes normales (rango → Φ⁻¹)."""
    n = team_remaining.size
    jitter = np.asarray(team_remaining, dtype=float) + rng.uniform(-1e-3, 1e-3, size=n)
    u_team = (stats.rankdata(jitter) - 0.5) / n
    return stats.norm.ppf(u_team)


def copula_player_samples(
    z_team: np.ndarray,
    mean_remaining: float,
    dist: str,
    sd_remaining: float,
    rho: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Muestras del stat restante del jugador, correlacionadas (cópula gaussiana) con el
    total restante de su equipo, expresado como puntajes normales z_team."""
    n = z_team.size
    z_p = rho * z_team + math.sqrt(max(1.0 - rho * rho, 1e-9)) * rng.standard_normal(n)
    u_p = np.clip(stats.norm.cdf(z_p), 1e-9, 1 - 1e-9)
    mean_remaining = max(mean_remaining, 1e-6)
    if dist == "poisson":
        # Φ⁻¹ discreta vía tabla de CDF + searchsorted (≈100x más rápido que scipy.ppf)
        kmax = int(mean_remaining + 12 * math.sqrt(mean_remaining) + 12)
        cdf = stats.poisson.cdf(np.arange(kmax + 1), mean_remaining)
        return np.searchsorted(cdf, u_p, side="left").astype(float)
    x = stats.norm.ppf(u_p, loc=mean_remaining, scale=max(sd_remaining, 1e-6))
    return np.clip(np.round(x), 0, None)


# --------------------------------------------------------------------------------------
# Capa 5 · Líneas dormidas y steam moves
# --------------------------------------------------------------------------------------

class LineTracker:
    def __init__(self, cfg: EngineConfig, horizon_s: float = 600.0):
        self.cfg = cfg
        self.horizon = horizon_s
        self.sharp: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=400))
        self.book: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=400))

    def record_sharp(self, key: tuple, ts: float, prob: float) -> None:
        h = self.sharp[key]
        if not h or abs(h[-1][1] - prob) > 1e-6 or ts - h[-1][0] > 30:
            h.append((ts, prob))
        while h and ts - h[0][0] > self.horizon:
            h.popleft()

    def record_book(self, key: tuple, ts: float, odds: float) -> None:
        h = self.book[key]
        if not h or abs(h[-1][1] - odds) > 1e-9 or ts - h[-1][0] > 30:
            h.append((ts, odds))
        while h and ts - h[0][0] > self.horizon:
            h.popleft()

    def trend(self, key: tuple) -> tuple[float | None, str]:
        h = self.book.get(key)
        if not h or len(h) < 2:
            return None, "flat"
        last = h[-1][1]
        for ts, o in reversed(list(h)[:-1]):
            if abs(o - last) > 1e-9:
                return o, ("up" if last > o else "down")
        return None, "flat"

    @staticmethod
    def _value_at(h: deque, ts: float) -> float | None:
        val = None
        for t, v in h:
            if t <= ts:
                val = v
            else:
                break
        return val

    def steam(self, key: tuple, now: float) -> tuple[float, float] | None:
        """(movimiento de probabilidad, ts del movimiento) si el sharp se movió ≥ umbral."""
        h = self.sharp.get(key)
        if not h or len(h) < 2:
            return None
        start = self._value_at(h, now - self.cfg.steam_window_s)
        if start is None:
            start = h[0][1]
        cur = h[-1][1]
        move = cur - start
        if abs(move) < self.cfg.steam_threshold:
            return None
        half = start + move / 2.0
        move_ts = h[-1][0]
        for t, v in h:
            if t >= now - self.cfg.steam_window_s and ((move > 0 and v >= half) or (move < 0 and v <= half)):
                move_ts = t
                break
        return move, move_ts

    def stale(self, sharp_key: tuple, book_key: tuple, now: float) -> dict | None:
        """La casa MX no ha seguido un movimiento sharp a favor de esta selección."""
        st = self.steam(sharp_key, now)
        if not st:
            return None
        move, move_ts = st
        if move <= 0:  # sólo nos interesa cuando la selección se volvió MÁS probable
            return None
        bh = self.book.get(book_key)
        if not bh:
            return None
        sh = self.sharp[sharp_key]
        ref_ts = move_ts - 1.0
        p_ref = self._value_at(sh, ref_ts) or sh[0][1]
        o_ref = self._value_at(bh, ref_ts) or bh[0][1]
        o_now = bh[-1][1]
        p_now = sh[-1][1]
        expected_cut = 1.0 - (p_ref / max(p_now, 1e-9))           # recorte esperado de la cuota
        actual_cut = 1.0 - (o_now / max(o_ref, 1e-9))
        if now - move_ts < 4.0:
            return None  # la casa aún no ha tenido ni un tick para reaccionar
        if expected_cut > 0.015 and actual_cut < 0.5 * expected_cut:
            return {"lag_s": int(max(now - move_ts, 0)), "move_pct": round(move * 100, 1)}
        return None


# --------------------------------------------------------------------------------------
# Capa 6 · Surebets
# --------------------------------------------------------------------------------------

def find_surebet(best: dict[str, tuple[str, float]]) -> dict | None:
    """best: selección → (casa, cuota). Devuelve el arbitraje si Σ 1/o < 1."""
    if len(best) < 2:
        return None
    inv = {s: 1.0 / o for s, (_, o) in best.items()}
    total = sum(inv.values())
    if total >= 1.0:
        return None
    books = {b for b, _ in best.values()}
    if len(books) < 2:  # misma casa en todas las patas: casi siempre error de datos
        return None
    weights = {s: v / total for s, v in inv.items()}
    return {"inv_sum": total, "profit_pct": 1.0 / total - 1.0, "weights": weights}


# --------------------------------------------------------------------------------------
# Capa 7 · Kelly + CLV
# --------------------------------------------------------------------------------------

def kelly_stake(bankroll: float, available: float, odds: float, w: float, l: float, cfg: EngineConfig) -> tuple[float, int]:
    """f* = 0.25 · (b·W − L) / (b·(W+L)); con W+L = 1 se reduce a 0.25·(b·p − q)/b.
    Tope 6 % del bankroll y del dinero disponible. Redondeo a pesos."""
    b = odds - 1.0
    if b <= 0 or (w + l) <= 0:
        return 0.0, 0
    f_full = (b * w - l) / (b * (w + l))
    if f_full <= 0:
        return 0.0, 0
    f = min(cfg.kelly_fraction * f_full, cfg.kelly_cap)
    stake = min(bankroll * f, max(available, 0.0))
    return float(f), int(round(stake))


def clv_of(odds_taken: float, closing_fair_odds: float) -> float:
    return odds_taken / closing_fair_odds - 1.0


# --------------------------------------------------------------------------------------
# Etiquetas
# --------------------------------------------------------------------------------------

TOTAL_NOUN = {"football": "goles", "basketball": "puntos", "americanfootball": "puntos",
              "hockey": "goles", "baseball": "carreras"}


def _fmt_line(x: float) -> str:
    s = f"{x:+.2f}".rstrip("0").rstrip(".")
    return s


def market_label(ev: dict, mk: dict) -> str:
    t = mk["type"]
    noun = TOTAL_NOUN.get(ev["sport"], "puntos")
    return {
        "1x2": "Resultado final (1X2)",
        "dc": "Doble oportunidad",
        "ml": "Ganador (incluye prórroga)" if ev["sport"] != "football" else "Ganador",
        "total": f"Total de {noun}",
        "spread": "Hándicap",
        "ah": "Hándicap asiático",
        "btts": "Ambos anotan",
        "total_1h": "Goles 1er tiempo",
        "corners": "Total de córners",
        "cards": "Total de tarjetas",
        "prop": (f"{mk.get('player', '')} · Goleador (en cualquier momento)" if mk.get("stat") == "goals"
                 else f"{mk.get('player', '')} · {STAT_LABEL.get(mk.get('stat', ''), mk.get('stat', ''))}"),
    }.get(t, mk.get("label", t))


def selection_label(ev: dict, mk: dict, sel: str) -> str:
    t, line = mk["type"], mk.get("line")
    home, away = ev["home"]["name"], ev["away"]["name"]
    if t in ("1x2", "ml"):
        return {"home": f"Gana {home}", "away": f"Gana {away}", "draw": "Empate"}.get(sel, sel)
    if t == "dc":
        return {"1x": f"{home} o empate", "x2": f"Empate o {away}", "12": f"{home} o {away}"}.get(sel, sel)
    if t == "prop" and mk.get("stat") == "goals" and line == 0.5:
        return f"Anota {mk.get('player', '')}" if sel == "over" else f"No anota {mk.get('player', '')}"
    if t in ("total", "total_1h", "corners", "cards", "prop"):
        return f"{'Más' if sel == 'over' else 'Menos'} de {line:g}"
    if t in ("spread", "ah"):
        return f"{home} {_fmt_line(line)}" if sel == "home" else f"{away} {_fmt_line(-line)}"
    if t == "btts":
        return "Sí" if sel == "yes" else "No"
    return sel


# --------------------------------------------------------------------------------------
# Motor principal
# --------------------------------------------------------------------------------------

SPORT_SD = {"basketball": (12.5, 18.0), "americanfootball": (13.5, 13.5)}
LEAGUE_DEFAULTS = {"goals": (1.45, 1.15), "corners": 10.0, "cards": 4.4}


class NuriEngine:
    def __init__(self, cfg: EngineConfig | None = None):
        self.cfg = cfg or EngineConfig()
        self.tracker = LineTracker(self.cfg)
        self.fair_cache: dict[tuple, dict] = {}
        self.prematch_rates: dict[str, tuple[float, float]] = {}
        self.alerts: deque = deque(maxlen=40)
        self._alert_keys: dict[tuple, float] = {}

    # ------------------------------------------------------------------ helpers
    def _rng(self, event_id: str, now: float) -> np.random.Generator:
        # Números aleatorios comunes por evento y ventana de 30 s → picks estables entre ticks.
        seed = zlib.crc32(event_id.encode()) ^ int(now // 30)
        return np.random.default_rng(seed)

    @staticmethod
    def _complete(mk: dict, book: str) -> dict[str, float] | None:
        sels = mk["selections"]
        out = {}
        for s, books in sels.items():
            o = books.get(book)
            if not o or o <= 1.0:
                return None
            out[s] = float(o)
        return out if len(out) >= 2 else None

    def _sharp_probs(self, mk: dict) -> tuple[dict[str, float] | None, list[str], float | None]:
        """Shin sobre cada casa sharp con mercado completo y promedio de probabilidades."""
        acc: dict[str, list[float]] = defaultdict(list)
        used, zs = [], []
        for sb in SHARP_BOOKS:
            q = self._complete(mk, sb)
            if not q:
                continue
            sels = list(q.keys())
            try:
                p, z = shin_devig([q[s] for s in sels])
            except ValueError:
                continue
            for s, pv in zip(sels, p):
                acc[s].append(float(pv))
            used.append(sb)
            zs.append(z)
        if not used or len(acc) != len(mk["selections"]):
            return None, [], None
        probs = {s: float(np.mean(v)) for s, v in acc.items()}
        tot = sum(probs.values())
        return {s: v / tot for s, v in probs.items()}, used, float(np.mean(zs))

    def _consensus(self, mk: dict) -> dict[str, float] | None:
        """Probabilidad de consenso de las casas MX (Shin por casa, promedio). Se usa para
        anclar el modelo cuando no existe línea sharp y evitar falsos +EV por error de modelo."""
        acc: dict[str, list[float]] = defaultdict(list)
        for b in MX_BOOKS:
            q = self._complete(mk, b)
            if not q:
                continue
            sels = list(q)
            try:
                p, _ = shin_devig([q[s] for s in sels])
            except ValueError:
                continue
            for s, pv in zip(sels, p):
                acc[s].append(float(pv))
        if len(acc) != len(mk["selections"]) or min(len(v) for v in acc.values()) < 2:
            return None
        probs = {s: float(np.mean(v)) for s, v in acc.items()}
        tot = sum(probs.values())
        return {s: v / tot for s, v in probs.items()}

    # ------------------------------------------------------------------ modelos por deporte
    def _football(self, ev, sharp, rng, n):
        sc = ev.get("score") or {"home": 0, "away": 0}
        ch, ca = int(sc["home"]), int(sc["away"])
        frac = float(ev.get("elapsed_frac", 0.0)) if ev["status"] == "live" else 0.0
        rem = max(1.0 - frac, 1e-3)
        live = ev.get("live", {})
        prior = ev.get("model_prior", {})
        info = {"method": "", "ch": ch, "ca": ca}

        main_total = None
        for key, sp in sharp.items():
            mk = ev["markets"][key]
            if mk["type"] == "total" and sp:
                if main_total is None or abs(sp["over"] - 0.5) < abs(main_total[1]["over"] - 0.5):
                    main_total = (mk["line"], sp)
        p1x2 = next((sp for k, sp in sharp.items() if ev["markets"][k]["type"] == "1x2" and sp), None)

        if p1x2:
            guess = tuple(np.array(prior.get("goals", LEAGUE_DEFAULTS["goals"])) * rem)
            targets = dict(p1x2)
            if main_total:
                targets["over"] = main_total[1]["over"]
            lam, mu = fit_two_rates(lambda a, b: dc_score_matrix(a, b, -0.06), targets, ch, ca,
                                    main_total[0] if main_total else None, guess)
            info["method"] = "Dixon-Coles calibrado a la línea de mercado"
            if ev["status"] == "pre":
                self.prematch_rates[ev["id"]] = (lam, mu)
        else:
            # Capa 3: sin línea sharp → prior pre-partido + xG observado (Gamma-Poisson)
            base = self.prematch_rates.get(ev["id"]) or tuple(prior.get("goals", LEAGUE_DEFAULTS["goals"]))
            xg = live.get("xg", {"home": 0.0, "away": 0.0})
            ah, bh = gamma_poisson_posterior(base[0], 1.2, xg.get("home", 0.0), frac)
            aa, ba = gamma_poisson_posterior(base[1], 1.2, xg.get("away", 0.0), frac)
            lam, mu = ah / bh * rem, aa / ba * rem
            reds = live.get("red", {"home": 0, "away": 0})
            lam *= RED_CARD_ATTACK ** reds.get("home", 0) * RED_CARD_OPP ** reds.get("away", 0)
            mu *= RED_CARD_ATTACK ** reds.get("away", 0) * RED_CARD_OPP ** reds.get("home", 0)
            info["method"] = "Bayes in-play (xG + tarjetas)"

        m = dc_score_matrix(lam, mu, -0.06)
        hg, ag = sample_matrix(m, n, rng)
        sims = {"h": hg + ch, "a": ag + ca, "h_rem": hg, "a_rem": ag}
        info.update({"lam": lam, "mu": mu, "proj_total": ch + ca + lam + mu})

        # Primer tiempo
        minute = float(ev.get("minute", 0) or 0)
        if minute < 45:
            rem1 = 45.0 - minute
            rem2 = 45.0 if minute < 45 else max(90.0 - minute, 0)
            weighted = rem1 * (0.45 / 45.0) + rem2 * (0.55 / 45.0)
            share = rem1 * (0.45 / 45.0) / max(weighted, 1e-9)
            m1 = dc_score_matrix(lam * share, mu * share, -0.06)
            h1, a1 = sample_matrix(m1, n, rng)
            sims["t1h"] = h1 + a1 + ch + ca

        # Córners y tarjetas: predictiva Gamma-Poisson
        for key, default in (("corners", LEAGUE_DEFAULTS["corners"]), ("cards", LEAGUE_DEFAULTS["cards"])):
            cur = live.get(key, {"home": 0, "away": 0})
            obs = float(cur.get("home", 0) + cur.get("away", 0))
            a, b = gamma_poisson_posterior(float(prior.get(key, default)), 1.5, obs, frac)
            sims[key] = obs + gamma_poisson_predictive(a, b, rem, n, rng)
            info[f"{key}_now"] = obs
            info[f"{key}_proj"] = obs + a / b * rem
        return sims, info

    def _gaussian(self, ev, sharp, rng, n):
        sd_m, sd_t = SPORT_SD.get(ev["sport"], (12.5, 18.0))
        sc = ev.get("score") or {"home": 0, "away": 0}
        ch, ca = float(sc["home"]), float(sc["away"])
        frac = float(ev.get("elapsed_frac", 0.0)) if ev["status"] == "live" else 0.0
        rem = max(1.0 - frac, 1e-3)
        sdm, sdt = sd_m * math.sqrt(rem), sd_t * math.sqrt(rem)
        cur_d, cur_t = ch - ca, ch + ca
        prior = ev.get("model_prior", {})
        info = {}

        m_rem = t_rem = None
        best_spread = None
        for key, sp in sharp.items():
            mk = ev["markets"][key]
            if mk["type"] == "spread" and sp:
                if best_spread is None or abs(sp["home"] - 0.5) < abs(best_spread[1]["home"] - 0.5):
                    best_spread = (mk["line"], sp)
            if mk["type"] == "total" and sp:
                p = min(max(sp["over"], 0.01), 0.99)
                cand = mk["line"] - cur_t + sdt * stats.norm.ppf(p)
                if t_rem is None or abs(sp["over"] - 0.5) < 0.2:
                    t_rem = cand
        if best_spread:
            L, sp = best_spread
            m_rem = sdm * stats.norm.ppf(min(max(sp["home"], 0.01), 0.99)) - L - cur_d
        else:
            pml = next((sp for k, sp in sharp.items() if ev["markets"][k]["type"] == "ml" and sp), None)
            if pml:
                m_rem = sdm * stats.norm.ppf(min(max(pml["home"], 0.01), 0.99)) - cur_d

        method = "Gaussiano calibrado a la línea de mercado"
        if m_rem is None or t_rem is None:
            # Capa 3: pace / net rating con contracción Normal-Normal
            exp_margin = float(prior.get("margin", 0.0))
            exp_total = float(prior.get("total", 224.0 if ev["sport"] == "basketball" else 44.0))
            if frac > 0.05:
                exp_total = normal_shrinkage(exp_total, 0.35, cur_t / frac, frac)
                exp_margin = normal_shrinkage(exp_margin, 0.6, cur_d / frac, frac)
            if m_rem is None:
                m_rem = exp_margin * rem
            if t_rem is None:
                t_rem = exp_total * rem
            method = "Pace + Net Rating bayesiano"

        z1 = rng.standard_normal(n)
        z2 = rng.standard_normal(n)
        margin = m_rem + sdm * z1
        total = t_rem + sdt * z2
        h_rem = np.clip((total + margin) / 2.0, 0, None)
        a_rem = np.clip((total - margin) / 2.0, 0, None)
        h = np.round(ch + h_rem)
        a = np.round(ca + a_rem)
        tie = h == a
        home_win = (h > a) | (tie & (rng.random(n) < 0.5 + np.clip(m_rem, -10, 10) * 0.01))
        info.update({"method": method, "proj_margin": cur_d + m_rem, "proj_total": cur_t + t_rem,
                     "team_prior": prior})
        return {"h": h, "a": a, "h_rem": h_rem, "a_rem": a_rem, "home_win": home_win}, info

    def _count_sport(self, ev, sharp, rng, n):
        """NHL (Poisson) y MLB (Binomial Negativa)."""
        is_mlb = ev["sport"] == "baseball"
        sc = ev.get("score") or {"home": 0, "away": 0}
        ch, ca = int(sc["home"]), int(sc["away"])
        frac = float(ev.get("elapsed_frac", 0.0)) if ev["status"] == "live" else 0.0
        rem = max(1.0 - frac, 1e-3)
        prior = ev.get("model_prior", {})
        base = tuple(prior.get("goals", (4.6, 4.3) if is_mlb else (3.2, 2.9)))
        mfn = (lambda a, b: nb_score_matrix(a, b)) if is_mlb else (lambda a, b: dc_score_matrix(a, b, 0.0, 14))

        main_total = None
        for key, sp in sharp.items():
            mk = ev["markets"][key]
            if mk["type"] == "total" and sp:
                if main_total is None or abs(sp["over"] - 0.5) < abs(main_total[1]["over"] - 0.5):
                    main_total = (mk["line"], sp)
        pml = next((sp for k, sp in sharp.items() if ev["markets"][k]["type"] == "ml" and sp), None)
        if pml:
            targets = {"home": pml["home"], "away": pml["away"]}
            if main_total:
                targets["over"] = main_total[1]["over"]
            lam, mu = fit_two_rates(mfn, targets, ch, ca, main_total[0] if main_total else None,
                                    (base[0] * rem, base[1] * rem), tie_split=base[0] / sum(base))
            method = "Poisson calibrado a la línea de mercado" if not is_mlb else "Binomial Negativa calibrada"
        else:
            lam, mu = base[0] * rem, base[1] * rem
            method = "Prior de liga"
        m = mfn(lam, mu)
        hg, ag = sample_matrix(m, n, rng)
        h, a = hg + ch, ag + ca
        tie = h == a
        p_home_ot = 0.52 if is_mlb else lam / max(lam + mu, 1e-9)
        home_win = (h > a) | (tie & (rng.random(n) < p_home_ot))
        return {"h": h, "a": a, "h_rem": hg, "a_rem": ag, "home_win": home_win}, {
            "method": method, "lam": lam, "mu": mu, "proj_total": ch + ca + lam + mu}

    # ------------------------------------------------------------------ W/L por mercado
    def _model_wl(self, ev, mk, sel, sims, rng, n, ctx) -> tuple[float, float] | None:
        t, line = mk["type"], mk.get("line")
        h, a = sims["h"], sims["a"]
        if t == "1x2":
            v = {"home": h > a, "draw": h == a, "away": h < a}[sel]
            p = float(np.mean(v))
            return p, 1 - p
        if t == "dc":
            v = {"1x": h >= a, "x2": a >= h, "12": h != a}[sel]
            p = float(np.mean(v))
            return p, 1 - p
        if t == "ml":
            hw = sims.get("home_win")
            if hw is None:
                hw = (h > a) | ((h == a) & (rng.random(n) < 0.5))
            p = float(np.mean(hw if sel == "home" else ~hw))
            return p, 1 - p
        if t == "total":
            tot = h + a
            return settle_wl(tot, line) if sel == "over" else settle_wl(-tot, -line)
        if t in ("spread", "ah"):
            d = h - a
            return settle_wl(d, -line) if sel == "home" else settle_wl(-d, line)
        if t == "btts":
            v = (h > 0) & (a > 0)
            p = float(np.mean(v if sel == "yes" else ~v))
            return p, 1 - p
        if t in ("total_1h", "corners", "cards"):
            arr = sims.get({"total_1h": "t1h"}.get(t, t))
            if arr is None:
                return None
            return settle_wl(arr, line) if sel == "over" else settle_wl(-arr, -line)
        if t == "prop":
            arr = self._prop_samples(ev, mk, sims, rng, ctx)
            if arr is None:
                return None
            return settle_wl(arr, line) if sel == "over" else settle_wl(-arr, -line)
        return None

    def _prop_samples(self, ev, mk, sims, rng, ctx):
        cache = ctx.setdefault("_props", {})
        key = (mk.get("player"), mk.get("stat"))
        if key in cache:
            return cache[key]
        player = next((p for p in ev.get("players", []) if p["name"] == mk.get("player")), None)
        if not player or mk.get("stat") not in player.get("stats", {}):
            cache[key] = None
            return None
        st = player["stats"][mk["stat"]]
        frac = float(ev.get("elapsed_frac", 0.0)) if ev["status"] == "live" else 0.0
        rem = max(1.0 - frac, 1e-3)
        shifts, team_mult, missing = ctx.setdefault("_usage", {}).get(player["team"]) or (None, None, None)
        if shifts is None:
            shifts, team_mult, missing = usage_shifts(ev.get("players", []), player["team"])
            ctx["_usage"][player["team"]] = (shifts, team_mult, missing)
        shift = shifts.get(player["name"], 0.0)
        team_rem = sims["h_rem"] if player["team"] == "home" else sims["a_rem"]
        pace_ratio = 1.0
        prior_team = ctx.get("team_prior", {}).get("home_pts" if player["team"] == "home" else "away_pts")
        if prior_team:
            pace_ratio = float(np.clip(np.mean(team_rem) / max(prior_team * rem, 1e-6), 0.8, 1.25))
        mean_rem = float(st["mean"]) * rem * (1.0 + shift) * pace_ratio
        sd_rem = float(st.get("sd") or math.sqrt(max(float(st["mean"]), 1.0))) * math.sqrt(rem)
        rho = STAT_CORR.get(mk["stat"], 0.25)
        zcache = ctx.setdefault("_zteam", {})
        if player["team"] not in zcache:
            zcache[player["team"]] = team_normal_scores(team_rem, rng)
        arr = float(st.get("current", 0.0)) + copula_player_samples(
            zcache[player["team"]], mean_rem, st.get("dist", "poisson"), sd_rem, rho, rng)
        cache[key] = arr
        ctx.setdefault("_prop_info", {})[key] = {"mean": float(st.get("current", 0.0)) + mean_rem,
                                                  "shift": shift, "missing": missing}
        return arr

    # ------------------------------------------------------------------ explicación
    @staticmethod
    def _reason(ev, mk, sel, book, odds, p, source, stale, info, ctx) -> str:
        implied = 1.0 / odds
        if stale:
            return (f"La línea de referencia se movió {stale['move_pct']:+.1f} pts hace {stale['lag_s']} s y "
                    f"{book} sigue pagando {odds:.2f}; la probabilidad real ya es {p:.1%}.")
        t = mk["type"]
        if source.startswith("Shin"):
            return (f"La línea sharp sin margen (Shin) da {p:.1%}; {book} paga {odds:.2f}, "
                    f"como si sólo fuera {implied:.1%}.")
        if t in ("corners", "cards"):
            noun = "córners" if t == "corners" else "tarjetas"
            now = info.get(f"{t}_now", 0)
            proj = info.get(f"{t}_proj", 0)
            when = f"Van {now:.0f} {noun} y" if ev["status"] == "live" else "El"
            return (f"{when} el ritmo proyecta {proj:.1f} {noun}; {book} paga como si la probabilidad "
                    f"fuera {implied:.0%} y el modelo da {p:.0%}.")
        if t == "prop":
            pi = ctx.get("_prop_info", {}).get((mk.get("player"), mk.get("stat")), {})
            missing = pi.get("missing") or []
            stat = STAT_LABEL.get(mk.get("stat"), "").lower()
            if missing and pi.get("shift", 0) > 0.02:
                return (f"Sin {missing[0]}, {mk['player']} absorbe más uso: el modelo proyecta "
                        f"{pi.get('mean', 0):.1f} {stat} contra la línea de {mk['line']:g}.")
            return (f"La cópula jugador-equipo proyecta {pi.get('mean', 0):.1f} {stat} "
                    f"(línea {mk['line']:g}); {book} lo paga como {implied:.0%}.")
        return (f"25,000 simulaciones ({info.get('method', 'modelo')}) dan {p:.1%}; "
                f"{book} lo paga como {implied:.1%}.")

    # ------------------------------------------------------------------ análisis
    def analyze(self, events: list[dict], bankroll: float, available: float,
                taken_ids: set[str] | None = None, now: float | None = None) -> dict:
        now = now or time.time()
        taken_ids = taken_ids or set()
        cfg = self.cfg
        n = cfg.mc_iterations
        picks, surebets, events_view, board = [], [], [], []
        rows_index: dict[str, dict] = {}  # TODAS las filas (para el registro de picks y el cierre)
        new_cache: dict[tuple, dict] = {}
        prev_cache = getattr(self, "_event_cache", {})
        next_event_cache: dict[str, tuple] = {}

        for ev in events:
            if ev.get("status") == "final" or not ev.get("markets"):
                events_view.append(self._event_view(ev, {}, None, 0))
                continue
            sig = self._signature(ev)
            cached = prev_cache.get(ev["id"])
            if ev["status"] == "pre" and cached and cached[0] == sig:
                # Pre-partido sin cambios de línea: reutilizamos el cálculo (ahorra CPU).
                _, c_picks, c_sure, c_probs, c_info, c_rows, c_fair = cached
                result = (c_picks, c_sure, c_probs, c_info, c_rows)
                new_cache.update(c_fair)
                next_event_cache[ev["id"]] = cached
            else:
                fair_local: dict[tuple, dict] = {}
                try:
                    result = self._analyze_event(ev, bankroll, available, taken_ids, now, n, fair_local)
                except Exception as exc:  # un evento malo no debe tumbar el tick
                    print(f"[engine] evento {ev.get('id')} falló: {exc!r}")
                    events_view.append(self._event_view(ev, {}, None, 0))
                    continue
                new_cache.update(fair_local)
                next_event_cache[ev["id"]] = (sig, *result, fair_local)
            ev_picks, ev_sure, model_probs, info, rows = result
            for p_ in ev_picks:  # el monto Kelly depende del saldo actual
                p_["taken"] = p_["id"] in taken_ids
            picks.extend(ev_picks)
            surebets.extend(ev_sure)
            for r_ in rows:
                rows_index[r_["id"]] = r_
            sel_rows = self._select_rows(ev, rows)
            board.extend(sel_rows)
            events_view.append(self._event_view(ev, model_probs, info, len(ev_picks) + len(sel_rows)))

        self.fair_cache = new_cache
        self._event_cache = next_event_cache
        picks.sort(key=lambda p: (-(p["stale"] is not None), -p["ev"]))
        surebets.sort(key=lambda s: -s["profit_pct"])
        board.sort(key=lambda r: (r["status"] != "live", (r["move"] or 0) < 0.03, -r["p"]))
        top = self._top_picks(board, now)
        return {"picks": picks, "surebets": surebets, "events": events_view, "board": board, "top": top,
                "alerts": list(self.alerts), "rows_index": rows_index}

    # ------------------------------------------------------------------ los mejores picks del día
    SECTIONS = (
        ("real", "Momio real con valor", 5),
        ("valor", "Valor: el mercado se movió", 4),
        ("props", "Props de jugadores", 5),
        ("lineas", "Hándicaps y totales", 3),
        ("seguras", "Las más seguras", 3),
        ("vivo", "En vivo ahora", 3),
    )

    def _top_picks(self, board: list[dict], now: float) -> list[dict]:
        """Pocas opciones claras por sección, sin repetir partido dentro de cada sección."""
        def window_ok(r):
            if r["status"] == "live":
                return True
            hours = ((r.get("start_ts") or now) - now) / 3600
            return -0.1 <= hours <= 36

        cands = [r for r in board if window_ok(r) and r.get("min_odds") and r["min_odds"] >= 1.15
                 and r["confidence"] != "baja"]

        def prop_ok(r):
            if r["market_type"] != "prop" or r["status"] != "pre":
                return False
            if r.get("stat") == "goals":  # goleador: con momio real que no regale, o ≥ 50 % sin momio
                if r.get("quotes"):
                    return r["p"] >= 0.40 and (r.get("best_ev") or -1) >= -0.04
                return r["p"] >= 0.50
            return r["p"] >= 0.58

        pools = {
            "real": sorted([r for r in cands if r.get("real_value") and 0.20 <= r["p"] <= 0.90],
                           key=lambda r: -(r.get("best_ev") or 0)),
            "valor": sorted([r for r in cands if r["status"] == "pre" and (r.get("move") or 0) >= 0.03
                             and 0.25 <= r["p"] <= 0.85], key=lambda r: -(r["move"] or 0)),
            "props": sorted([r for r in cands if prop_ok(r)],
                            key=lambda r: (not r.get("real_value"), -(r.get("best_ev") or 0) if r.get("quotes") else 0,
                                           -r["p"])),
            "lineas": sorted([r for r in cands if r["market_type"] in ("spread", "ah", "total") and r["status"] == "pre"
                              and r["confidence"] == "alta" and 0.52 <= r["p"] <= 0.75],
                             key=lambda r: -(r["p"] + 2 * max(r.get("move") or 0, 0))),
            "seguras": sorted([r for r in cands if r["status"] == "pre" and 0.70 <= r["p"] <= 0.86
                               and r["market_type"] in ("1x2", "ml", "spread", "ah", "total", "btts")],
                              key=lambda r: (r["confidence"] != "alta", -r["p"])),
            "vivo": sorted([r for r in cands if r["status"] == "live" and 0.55 <= r["p"] <= 0.88],
                           key=lambda r: -r["p"]),
        }
        tags = {"real": "Momio real", "valor": "Línea movida", "props": "Prop", "lineas": "Hándicap / total",
                "seguras": "La más segura", "vivo": "En vivo"}
        out, used = [], set()
        for key, title, n in self.SECTIONS:
            seen = set()
            count = 0
            for r in pools[key]:
                if r["event_id"] in seen or r["id"] in used:
                    continue
                seen.add(r["event_id"])
                used.add(r["id"])
                count += 1
                out.append({**r, "section": key, "section_title": title, "rank": count, "tag": tags[key]})
                if count >= n:
                    break
        return out

    @staticmethod
    def _signature(ev: dict) -> str:
        mk = {k: {s: sorted(b.items()) for s, b in m["selections"].items()} for k, m in ev["markets"].items()}
        return json.dumps([ev.get("status"), ev.get("score"), round(float(ev.get("elapsed_frac") or 0), 3), mk],
                          sort_keys=True, default=str)

    def _open_probs(self, mk: dict) -> dict[str, float] | None:
        q = self._complete(mk, OPEN_BOOK)
        if not q:
            return None
        sels = list(q)
        try:
            p, _ = shin_devig([q[s] for s in sels])
        except ValueError:
            return None
        return {s: float(v) for s, v in zip(sels, p)}

    def _analyze_event(self, ev, bankroll, available, taken_ids, now, n, cache):
        cfg = self.cfg
        rng = self._rng(ev["id"], now)
        markets = ev["markets"]

        sharp: dict[str, dict | None] = {}
        sharp_used: dict[str, list[str]] = {}
        for key, mk in markets.items():
            sp, used, _z = self._sharp_probs(mk)
            sharp[key] = sp
            sharp_used[key] = used
            if sp:
                for sel, p in sp.items():
                    self.tracker.record_sharp((ev["id"], key, sel), now, p)

        if "dc" in markets and sharp.get("1x2"):
            s1 = sharp["1x2"]
            if all(k in s1 for k in ("home", "draw", "away")):
                sharp["dc"] = {"1x": s1["home"] + s1["draw"], "x2": s1["draw"] + s1["away"],
                               "12": s1["home"] + s1["away"]}
                sharp_used["dc"] = list(sharp_used.get("1x2") or [])

        sport = ev["sport"]
        if sport == "football":
            sims, info = self._football(ev, sharp, rng, n)
        elif sport in ("basketball", "americanfootball"):
            sims, info = self._gaussian(ev, sharp, rng, n)
        else:
            sims, info = self._count_sport(ev, sharp, rng, n)
        ctx = {"team_prior": info.get("team_prior", {}) if isinstance(info.get("team_prior"), dict) else {}}

        ev_picks, ev_sure = [], []
        model_probs: dict[str, dict[str, float]] = {}
        rows: list[dict] = []
        has_line = any(sharp.values()) or bool(ev.get("model_prior", {}).get("from_line"))

        frac_live = float(ev.get("elapsed_frac", 0.0)) if ev["status"] == "live" else 0.0
        for key, mk in markets.items():
            sp = sharp.get(key)
            op = self._open_probs(mk) if sp else None
            cons = None if sp else self._consensus(mk)
            w_model = 0.35 if mk["type"] == "prop" else 0.40 + 0.30 * frac_live
            best_by_sel: dict[str, tuple[str, float]] = {}
            for sel, books in mk["selections"].items():
                # Probabilidad real: Shin si hay sharp; si no, Monte Carlo.
                mwl = self._model_wl(ev, mk, sel, sims, rng, n, ctx)
                if sp:
                    w, l = sp[sel], 1.0 - sp[sel]
                    # Líneas enteras / de cuarto: el sharp cotiza condicionado a no-push;
                    # la probabilidad de push la aporta el Monte Carlo.
                    if mwl and mk.get("line") is not None and float(mk["line"]) % 1 == 0:
                        push = max(1.0 - mwl[0] - mwl[1], 0.0)
                        w, l = w * (1.0 - push), l * (1.0 - push)
                    source = "Shin · " + " + ".join(sharp_used[key])
                elif mwl:
                    w, l = mwl
                    source = "Monte Carlo 25k · " + info.get("method", "modelo")
                    if cons and sel in cons:
                        decided = w + l
                        w = w_model * w + (1 - w_model) * cons[sel] * decided
                        l = decided - w
                        source += " + consenso MX"
                else:
                    continue
                if mk["type"] in ("1x2", "ml"):
                    model_probs.setdefault(mk["type"], {})[sel] = round(w / max(w + l, 1e-9), 4)
                cache[(ev["id"], key, sel)] = {"w": w, "l": l, "source": source, "status": ev["status"],
                                               "fair": fair_odds_of(w, l)}
                row = self._board_row(ev, mk, key, sel, w, l, source, sp, op, has_line, info, ctx)
                if row:
                    rows.append(row)

                mx_quotes = [(b, float(o)) for b, o in books.items() if b in MX_BOOKS and o and o > 1.0]
                for b, o in mx_quotes:
                    self.tracker.record_book((ev["id"], key, sel, b), now, o)
                if not mx_quotes:
                    continue
                b_best, o_best = max(mx_quotes, key=lambda x: x[1])
                best_by_sel[sel] = (b_best, o_best)

                if not (cfg.min_prob <= w <= cfg.max_prob):
                    continue
                if not has_line:
                    continue  # sin ninguna línea de mercado el modelo no está calibrado: no recomendamos
                ev_val = ev_of(o_best, w, l)
                cap = cfg.max_credible_ev if sp else cfg.max_model_ev
                if ev_val < cfg.ev_threshold or ev_val > cap:
                    continue
                # Bet365 llega vía ESPN cada pocos minutos: no lo seguimos en tiempo real, así que no
                # podemos afirmar que "se quedó dormido" (sería una falsa alarma).
                stale = (self.tracker.stale((ev["id"], key, sel), (ev["id"], key, sel, b_best), now)
                         if sp and b_best not in SLOW_BOOKS else None)
                prev, trend = self.tracker.trend((ev["id"], key, sel, b_best))
                frac, stake = kelly_stake(bankroll, available, o_best, w, l, cfg)
                pid = f"{ev['id']}|{key}|{sel}|{b_best}"
                pick = {
                    "id": pid,
                    "event_id": ev["id"], "sport": sport, "league": ev["league"],
                    "league_logo": ev.get("league_logo"), "home": ev["home"], "away": ev["away"],
                    "status": ev["status"], "clock": ev.get("clock"), "start_ts": ev.get("start_ts"),
                    "score": ev.get("score"),
                    "market_key": key, "market_type": mk["type"], "market_label": market_label(ev, mk),
                    "selection": sel, "selection_label": selection_label(ev, mk, sel), "line": mk.get("line"),
                    "book": b_best, "odds": round(o_best, 3), "odds_prev": prev, "trend": trend,
                    "others": sorted([{"book": b, "odds": o} for b, o in mx_quotes if b != b_best],
                                     key=lambda x: -x["odds"])[:4],
                    "p_real": round(w / max(w + l, 1e-9), 4), "w": round(w, 4), "l": round(l, 4),
                    "fair_odds": round(fair_odds_of(w, l), 3), "ev": round(ev_val, 4),
                    "source": source, "kelly_fraction": round(frac, 4), "stake": stake,
                    "below_min": 0 < stake < cfg.min_stake,
                    "stale": stale,
                    "confidence": "alta" if sp else ("media" if mk["type"] not in ("prop",) else "media-baja"),
                    "reason": self._reason(ev, mk, sel, b_best, o_best, w / max(w + l, 1e-9), source, stale, info, ctx),
                    "taken": pid in taken_ids,
                }
                ev_picks.append(pick)
                if stale:
                    self._alert(("stale", pid), now, {
                        "type": "stale", "ts": now, "event_id": ev["id"],
                        "text": f"{b_best} no ha ajustado {pick['selection_label']} ({ev['home']['name']} vs "
                                f"{ev['away']['name']}) · {stale['lag_s']} s de retraso"})

            # Steam moves (alerta informativa)
            if sp:
                for sel in sp:
                    st = self.tracker.steam((ev["id"], key, sel), now)
                    if st and st[0] > 0:
                        self._alert(("steam", ev["id"], key, sel), now, {
                            "type": "steam", "ts": now, "event_id": ev["id"],
                            "text": f"Steam sharp: {selection_label(ev, mk, sel)} "
                                    f"({ev['home']['name']} vs {ev['away']['name']}) {st[0] * 100:+.1f} pts"})

            if len(best_by_sel) == len(mk["selections"]):
                sb = find_surebet(best_by_sel)
                if sb and sb["profit_pct"] <= cfg.max_credible_ev:
                    total = min(max(available, 0.0), bankroll * 0.25)
                    legs = []
                    for sel, (b, o) in best_by_sel.items():
                        wgt = sb["weights"][sel]
                        legs.append({"selection": sel, "selection_label": selection_label(ev, mk, sel),
                                     "book": b, "odds": round(o, 3), "weight": round(wgt, 5),
                                     "stake": int(round(total * wgt))})
                    sid = f"sure|{ev['id']}|{key}"
                    ev_sure.append({
                        "id": sid, "event_id": ev["id"], "sport": sport, "league": ev["league"],
                        "league_logo": ev.get("league_logo"), "home": ev["home"], "away": ev["away"],
                        "status": ev["status"], "clock": ev.get("clock"), "start_ts": ev.get("start_ts"),
                        "market_key": key, "market_label": market_label(ev, mk),
                        "legs": legs, "inv_sum": round(sb["inv_sum"], 5),
                        "profit_pct": round(sb["profit_pct"], 4), "suggested_total": int(round(total)),
                        "profit_mxn": round(total * sb["profit_pct"], 2),
                        "taken": any(f"{sid}|{l['selection']}" in taken_ids for l in legs),
                        "reason": (f"Sumando 1/cuota de las {len(legs)} patas da {sb['inv_sum']:.3f} < 1: "
                                   f"cualquier resultado deja {sb['profit_pct']:.2%} de ganancia."),
                    })
        return ev_picks, ev_sure, model_probs, info, rows  # todas; la selección se hace en analyze()

    # ------------------------------------------------------------------ tablero de probabilidades
    def _board_row(self, ev, mk, key, sel, w, l, source, sp, op, has_line, info, ctx=None) -> dict | None:
        cfg = self.cfg
        if w <= 1e-4 or l <= 1e-6:
            return None  # resultado ya decidido (p. ej. un "Más de 0.5" que ya se cumplió)
        p = w / max(w + l, 1e-9)
        p_open = op.get(sel) if op else None
        move = (p - p_open) if p_open is not None else None
        min_odds = 1.0 + (l + cfg.ev_threshold) / w if w > 1e-6 else None
        if ev["status"] == "live":
            conf = "media" if has_line else "baja"
        elif sp:
            conf = "alta"
        else:
            conf = "media" if has_line else "baja"
        if mk["type"] == "prop":
            pi = (ctx or {}).get("_prop_info", {}).get((mk.get("player"), mk.get("stat")), {})
            stat = STAT_LABEL.get(mk.get("stat"), "").lower()
            avg = mk.get("avg") or 0.0
            extra = ""
            if pi.get("missing") and pi.get("shift", 0) > 0.02:
                extra = f" y sin {pi['missing'][0]} tiene más balón"
            if mk.get("stat") == "goals":
                se = mk.get("season") or {}
                reason = (f"Lleva {se.get('goals', 0)} goles en {se.get('apps', 0)} partidos. Con los goles que la "
                          f"línea espera de su equipo hoy, el modelo le da {p:.0%} de anotar "
                          f"(justo sería {fair_odds_of(w, l):.2f}). Tómala si paga {min_odds:.2f} o más.")
                source = "Tasa de gol real (temporada) + goles esperados del equipo · cópula 25k"
            else:
                fmt = ".0f" if avg >= 20 else ".1f"
                opp = ""
                if mk.get("opp_allowed"):
                    opp = f"; la defensa rival permite {mk['opp_allowed']:.0f} por partido"
                reason = (f"Promedia {avg:{fmt}} {stat} esta temporada{opp}. Con el ritmo esperado del partido{extra} "
                          f"el modelo proyecta {pi.get('mean', avg):{fmt}}. Tómala si tu casa tiene la línea en "
                          f"{mk['line']:g} y paga {min_odds:.2f} o más.")
                source = "Proyección NURI (promedio real + rival + ritmo + bajas) · cópula 25k"
            conf = "media"
        elif ev["status"] == "live":
            sc = ev.get("score") or {"home": 0, "away": 0}
            reason = (f"Van {sc['home']}–{sc['away']} ({ev.get('clock') or 'en vivo'}): 25,000 simulaciones "
                      f"calibradas a la línea previa dan {p:.0%}. Vale la pena desde {min_odds:.2f}.")
        elif move is not None and move >= 0.03:
            reason = (f"La línea se movió a favor: abrió en {p_open:.0%} y ya va en {p:.0%}. Las casas "
                      f"mexicanas suelen tardar en ajustar: revisa si todavía pagan {min_odds:.2f} o más.")
        elif sp:
            reason = (f"Probabilidad real {p:.0%} (línea de mercado sin margen, modelo de Shin). "
                      f"Vale la pena desde {min_odds:.2f}.")
        elif has_line:
            reason = f"El modelo calibrado a la línea de mercado da {p:.0%}. Vale la pena desde {min_odds:.2f}."
        else:
            reason = f"Estimación del modelo sin línea de mercado ({p:.0%}): úsala con cautela."
        books = mk["selections"].get(sel, {})
        # Momios REALES de casas que operan en México (Bet365 vía ESPN; Caliente/Codere vía odds-api.io)
        quotes = sorted(({"book": b, "odds": round(float(o), 3), "ev": round(w * (float(o) - 1.0) - l, 4)}
                         for b, o in books.items() if b in MX_BOOKS and o and float(o) > 1.0),
                        key=lambda q: -q["odds"])
        best = quotes[0] if quotes else None
        cap = cfg.max_credible_ev if sp else cfg.max_model_ev
        real_value = bool(best and has_line and cfg.ev_threshold <= best["ev"] <= cap)
        if best:
            fair = fair_odds_of(w, l)
            if real_value:
                reason = (f"{best['book']} paga {best['odds']:.2f} y lo justo es {fair:.2f} (gana {p:.0%} de las veces): "
                          f"te deja {best['ev']:+.1%} a tu favor. ") + reason
            elif best["ev"] > cap:
                reason = (f"{best['book']} paga {best['odds']:.2f}, demasiado alto contra lo justo ({fair:.2f}): "
                          f"revisa que sea la misma línea antes de apostar. ") + reason
            else:
                reason = (f"{best['book']} paga {best['odds']:.2f}; necesitas {min_odds:.2f} o más para que convenga. ") + reason
        return {
            "id": f"{ev['id']}|{key}|{sel}", "event_id": ev["id"], "sport": ev["sport"], "league": ev["league"],
            "status": ev["status"], "start_ts": ev.get("start_ts"),
            "ref_odds": books.get(REF_BOOK), "open_odds": books.get(OPEN_BOOK),
            "quotes": quotes, "best_book": best["book"] if best else None,
            "best_odds": best["odds"] if best else None, "best_ev": best["ev"] if best else None,
            "real_value": real_value, "stat": mk.get("stat"), "player": mk.get("player"),
            "proj": mk.get("proj"),
            "market_key": key, "market_type": mk["type"], "market_label": market_label(ev, mk),
            "selection": sel, "selection_label": selection_label(ev, mk, sel), "line": mk.get("line"),
            "p": round(p, 4), "w": round(w, 4), "l": round(l, 4), "fair": round(fair_odds_of(w, l), 3),
            "min_odds": round(min_odds, 3) if min_odds else None,
            "p_open": round(p_open, 4) if p_open is not None else None,
            "move": round(move, 4) if move is not None else None,
            "source": source, "confidence": conf, "reason": reason,
        }

    @staticmethod
    def _select_rows(ev, rows: list[dict]) -> list[dict]:
        """Del tablero completo deja lo útil: el lado favorito de cada mercado y todo lo que se movió."""
        out, by_market = [], {}
        far = ev["status"] == "pre" and (ev.get("start_ts") or 0) - time.time() > 48 * 3600
        for r in rows:
            if not (0.05 <= r["p"] <= 0.97):
                continue
            if far and r["market_type"] not in ("1x2", "ml", "total"):
                continue
            by_market.setdefault(r["market_key"], []).append(r)
        for key, rs in by_market.items():
            best = max(rs, key=lambda r: r["p"])
            three_way = rs[0]["market_type"] == "1x2"
            scorer = rs[0].get("stat") == "goals"
            if scorer:
                # goleador: sólo «Anota», y sólo si hay momio real o una probabilidad decente
                out += [r for r in rs if r["selection"] == "over" and (r.get("quotes") or r["p"] >= 0.45)]
                continue
            if best["p"] >= (0.40 if three_way else 0.50):
                out.append(best)
            out += [r for r in rs if r is not best and ((r["move"] or 0) >= 0.03 or r.get("real_value"))]
        out.sort(key=lambda r: (not r.get("real_value"), -(r["move"] or 0), -r["p"]))
        return out[:10]

    def _alert(self, key, now, payload):
        last = self._alert_keys.get(key)
        if last and now - last < 120:
            return
        self._alert_keys[key] = now
        self.alerts.appendleft(payload)

    def _event_view(self, ev, model_probs, info, n_picks):
        main = None
        for t in ("1x2", "ml"):
            mk = next((m for m in ev.get("markets", {}).values() if m["type"] == t), None)
            if mk:
                sels = []
                for sel, books in mk["selections"].items():
                    mx = [(b, o) for b, o in books.items() if b in MX_BOOKS and o]
                    best = max(mx, key=lambda x: x[1]) if mx else (None, None)
                    _, trend = self.tracker.trend((ev["id"], mk["key"], sel, best[0])) if best[0] else (None, "flat")
                    pm = model_probs.get(t, {}).get(sel)
                    sels.append({"selection": sel, "label": {"home": "1", "draw": "X", "away": "2"}[sel],
                                 "book": best[0], "odds": best[1], "trend": trend,
                                 "p_model": pm, "fair": round(1 / pm, 2) if pm else None})
                main = {"type": t, "selections": sels}
                break
        return {
            "id": ev["id"], "sport": ev["sport"], "league": ev["league"], "league_logo": ev.get("league_logo"),
            "home": ev["home"], "away": ev["away"], "status": ev["status"], "clock": ev.get("clock"),
            "start_ts": ev.get("start_ts"), "score": ev.get("score"), "stats": ev.get("stats", []),
            "timeline": ev.get("timeline", [])[-12:], "main": main, "picks_count": n_picks,
            "model": {"method": (info or {}).get("method"),
                      "proj_total": round((info or {}).get("proj_total", 0) or 0, 2) or None},
            "source": ev.get("source"), "facts": ev.get("facts"), "book_urls": ev.get("book_urls"),
        }

    # ------------------------------------------------------------------ manual / CLV
    def validate(self, event_id: str, market_key: str, selection: str, odds: float,
                 bankroll: float, available: float) -> dict:
        fair = self.fair_cache.get((event_id, market_key, selection))
        if not fair:
            return {"ok": False, "message": "Ese mercado ya no está activo."}
        w, l = fair["w"], fair["l"]
        ev_val = ev_of(odds, w, l)
        frac, stake = kelly_stake(bankroll, available, odds, w, l, self.cfg)
        if ev_val > self.cfg.max_credible_ev:
            verdict = ("¿Seguro que es ese momio? Una ventaja tan grande casi siempre es un error al escribir "
                       "o una línea distinta (otro hándicap o total). Revísalo antes de apostar.")
        elif ev_val >= self.cfg.ev_threshold:
            verdict = "Valor real: tómala."
        elif ev_val > 0:
            verdict = "Ventaja pequeña, por debajo del umbral de 4.5 %."
        else:
            verdict = "Sin valor a esa cuota."
        return {"ok": True, "ev": round(ev_val, 4), "fair_odds": round(fair["fair"], 3),
                "p_real": round(w / max(w + l, 1e-9), 4), "stake": stake if ev_val > 0 else 0,
                "kelly_fraction": round(frac, 4), "verdict": verdict, "source": fair["source"]}

    def closing_fair(self, event_id: str, market_key: str, selection: str) -> float | None:
        fair = self.fair_cache.get((event_id, market_key, selection))
        return fair["fair"] if fair else None
