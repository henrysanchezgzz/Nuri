"""
NURI · 누리 — mx_feeds.py
=========================

Fuentes de datos normalizadas a un mismo formato de evento:

  · OddsApiSource      The Odds API v4 (Pinnacle / Betfair como sharp + Betway, la única casa
                       con operación en México que hoy publica The Odds API).
  · ApiSportsSource    API-Football (API-Sports): marcador, minuto, logos oficiales y estadísticas.
  · LogoResolver       TheSportsDB (llave pública "3") para escudos oficiales de equipos y ligas.
  · MXScraper          Lector asíncrono (httpx o Playwright headless, rotación de User-Agent) para
                       los feeds JSON públicos de Caliente.mx, Codere y Stake.
  · DemoFeed           Motor de streaming realista Zero-Config: simula partidos, reloj, xG,
                       córners, tarjetas, lesiones y cómo cada casa mexicana sigue al sharp con
                       retraso. Se activa solo si no hay ODDS_API_KEY.

Formato de evento (lo que consume quant_engine):
{
  id, sport, league, league_logo, home{name,short,logo}, away{...}, start_ts, status(pre|live|final),
  clock, minute, elapsed_frac, score{home,away}, stats[{label,home,away,fmt}], live{xg,corners,cards,red},
  timeline[{t,type,team,text}], markets{key:{key,type,line,player?,stat?,selections{sel:{book:odds}}}},
  players[{name,team,usage,status,stats{stat:{mean,sd,current,dist}}}], model_prior{...}, source
}
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import time
import unicodedata
import zlib
from collections import deque
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import httpx
import numpy as np
from scipy import stats

from quant_engine import MX_BOOKS, SHARP_BOOKS, apply_margin, dc_score_matrix

# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
]

_STOP = {"fc", "cf", "club", "de", "the", "sc", "ac", "afc", "cd", "ud", "sd", "deportivo", "real"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_name(s: str) -> str:
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p not in _STOP]
    return " ".join(parts) or s.strip()


def name_score(a: str, b: str) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def short_name(name: str) -> str:
    words = [w for w in re.split(r"\s+", strip_accents(name)) if w]
    if len(words) == 1:
        return words[0][:3].upper()
    if len(words[-1]) >= 3 and len(words) <= 3:
        return words[-1][:3].upper()
    return "".join(w[0] for w in words[:3]).upper()


def _seeded(name: str, salt: str = "") -> np.random.Generator:
    return np.random.default_rng(zlib.crc32((name + salt).encode()))


def market_key(mtype: str, line: float | None = None, player: str | None = None, stat: str | None = None) -> str:
    if mtype == "prop":
        return f"prop:{player}:{stat}:{line:g}"
    if line is None:
        return mtype
    return f"{mtype}:{line:g}"


# --------------------------------------------------------------------------------------
# Logos oficiales (TheSportsDB)
# --------------------------------------------------------------------------------------

TSDB_SPORT = {"football": "Soccer", "basketball": "Basketball", "americanfootball": "American Football",
              "hockey": "Ice Hockey", "baseball": "Baseball"}
TSDB_LEAGUE_IDS = {"Liga MX": 4350, "Premier League": 4328, "LaLiga": 4335, "Serie A": 4332,
                   "Bundesliga": 4331, "Champions League": 4480, "NBA": 4387, "NFL": 4391, "NHL": 4380,
                   "MLB": 4424}
TSDB_SEARCH_ALIAS = {"América": "Club America", "Tigres UANL": "Tigres", "Pumas UNAM": "Pumas",
                     "Atlético Madrid": "Atletico Madrid", "León": "Leon", "Athletic Bilbao": "Athletic Bilbao",
                     "Manchester United": "Man United", "Inter": "Inter Milan"}


class LogoResolver:
    def __init__(self, db, api_key: str = "3"):
        self.db = db
        self.key = api_key or "3"
        self.mem: dict[str, str | None] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending: set[str] = set()

    def _cache_key(self, kind: str, name: str, sport: str) -> str:
        return f"{kind}:{sport}:{name}"

    def _lookup(self, ck: str, item: tuple) -> str | None:
        if ck in self.mem:
            return self.mem[ck]
        cached = self.db.get_logo(ck)
        if cached is not None:
            url, ts = cached
            if url or time.time() - ts < 86400:
                self.mem[ck] = url or None
                return self.mem[ck]
        if ck not in self.pending:
            self.pending.add(ck)
            self.queue.put_nowait(item)
        return None

    def team(self, name: str, sport: str) -> str | None:
        ck = self._cache_key("team", name, sport)
        return self._lookup(ck, ("team", name, sport, ck))

    def league(self, name: str) -> str | None:
        if name not in TSDB_LEAGUE_IDS:
            return None
        ck = self._cache_key("league", name, "")
        return self._lookup(ck, ("league", name, "", ck))

    def put(self, ck: str, url: str | None) -> None:
        self.mem[ck] = url
        self.db.set_logo(ck, url or "")

    async def run(self) -> None:
        base = f"https://www.thesportsdb.com/api/v1/json/{self.key}"
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": USER_AGENTS[0]}) as client:
            while True:
                kind, name, sport, ck = await self.queue.get()
                url = None
                try:
                    if kind == "league":
                        r = await client.get(f"{base}/lookupleague.php", params={"id": TSDB_LEAGUE_IDS[name]})
                        lg = (r.json() or {}).get("leagues") or []
                        if lg:
                            url = lg[0].get("strBadge") or lg[0].get("strLogo")
                    else:
                        q = strip_accents(TSDB_SEARCH_ALIAS.get(name, name))
                        r = await client.get(f"{base}/searchteams.php", params={"t": q})
                        teams = (r.json() or {}).get("teams") or []
                        want = TSDB_SPORT.get(sport)
                        cands = [t for t in teams if not want or t.get("strSport") == want] or teams
                        if cands:
                            best = max(cands, key=lambda t: name_score(t.get("strTeam", ""), name))
                            url = best.get("strBadge") or best.get("strTeamBadge")
                except Exception:
                    url = None
                self.put(ck, url)
                self.pending.discard(ck)
                await asyncio.sleep(0.5)


# --------------------------------------------------------------------------------------
# Casas de apuestas (perfil para el simulador y metadatos)
# --------------------------------------------------------------------------------------

ALL_MARKETS = {"1x2", "ml", "total", "spread", "ah", "btts", "total_1h", "corners", "cards", "prop"}

BOOK_PROFILES: dict[str, dict] = {
    "Pinnacle": {"sharp": True, "margin": 0.025, "lag": (0, 0), "noise": 0.0, "decimals": 3,
                 "markets": {"1x2", "ml", "total", "spread", "ah"}, "susp": 8},
    "Betfair Exchange": {"sharp": True, "margin": 0.012, "lag": (0, 0), "noise": 0.0, "decimals": 2,
                         "markets": {"1x2", "ml", "total"}, "main_only": True, "susp": 8},
    "Caliente.mx": {"margin": 0.068, "lag": (20, 45), "noise": 0.07, "decimals": 2, "markets": ALL_MARKETS},
    "Codere": {"margin": 0.062, "lag": (15, 40), "noise": 0.062, "decimals": 2, "markets": ALL_MARKETS - {"cards"}},
    "Stake": {"margin": 0.045, "lag": (5, 15), "noise": 0.04, "decimals": 2, "markets": ALL_MARKETS},
    "Bet365": {"margin": 0.055, "lag": (5, 12), "noise": 0.032, "decimals": 2, "markets": ALL_MARKETS},
    "Betway": {"margin": 0.058, "lag": (10, 25), "noise": 0.05, "decimals": 2, "markets": ALL_MARKETS - {"total_1h"}},
    "Novibet": {"margin": 0.060, "lag": (12, 35), "noise": 0.056, "decimals": 2, "markets": ALL_MARKETS - {"btts"}},
}
LIVE_PROP_BOOKS = {"Bet365", "Stake"}  # casas que mantienen props abiertos in-play
SOFT_MARKET_NOISE = {"prop": 1.25, "corners": 1.2, "cards": 1.2, "total_1h": 1.1, "btts": 1.1}

# --------------------------------------------------------------------------------------
# DEMO · datos de ligas y equipos (nombres de jugadores ficticios)
# --------------------------------------------------------------------------------------

FOOTBALL_LEAGUES = {
    "Liga MX": {"goals": 2.8, "corners": 9.6, "cards": 4.9,
                "teams": ["América", "Guadalajara", "Monterrey", "Tigres UANL", "Cruz Azul", "Pumas UNAM",
                          "Toluca", "Santos Laguna", "Pachuca", "León", "Atlas", "Tijuana"]},
    "Premier League": {"goals": 2.9, "corners": 10.3, "cards": 3.9,
                       "teams": ["Arsenal", "Manchester City", "Liverpool", "Chelsea", "Tottenham", "Newcastle",
                                 "Aston Villa", "Manchester United", "Brighton", "West Ham"]},
    "LaLiga": {"goals": 2.6, "corners": 9.4, "cards": 5.0,
               "teams": ["Real Madrid", "Barcelona", "Atlético Madrid", "Athletic Bilbao", "Real Sociedad",
                         "Villarreal", "Real Betis", "Sevilla", "Valencia", "Girona"]},
    "Serie A": {"goals": 2.7, "corners": 9.8, "cards": 4.6,
                "teams": ["Inter", "Milan", "Juventus", "Napoli", "Roma", "Lazio", "Atalanta", "Fiorentina"]},
}
NBA_TEAMS = ["Los Angeles Lakers", "Boston Celtics", "Golden State Warriors", "Denver Nuggets", "New York Knicks",
             "Oklahoma City Thunder", "Milwaukee Bucks", "Dallas Mavericks", "Phoenix Suns", "Miami Heat",
             "Minnesota Timberwolves", "Cleveland Cavaliers"]
NFL_TEAMS = ["Kansas City Chiefs", "Philadelphia Eagles", "Dallas Cowboys", "San Francisco 49ers", "Buffalo Bills",
             "Baltimore Ravens", "Detroit Lions", "Green Bay Packers", "Houston Texans", "Miami Dolphins"]
NHL_TEAMS = ["Edmonton Oilers", "Toronto Maple Leafs", "New York Rangers", "Florida Panthers",
             "Colorado Avalanche", "Vegas Golden Knights", "Dallas Stars", "Boston Bruins"]

FIRST = ["M.", "J.", "D.", "L.", "T.", "R.", "S.", "A.", "C.", "K.", "B.", "N.", "E.", "G."]
LAST = ["Ortega", "Whitaker", "Kovac", "Brandt", "Okafor", "Varela", "Haynes", "Petrov", "Moreau", "Lindqvist",
        "Adeyemi", "Castillo", "Navarro", "Sterling", "Duarte", "Holloway", "Ibarra", "Fontaine", "Reyes", "Kessler"]


def fake_player(team: str, idx: int) -> str:
    g = _seeded(team, f"p{idx}")
    return f"{FIRST[int(g.integers(len(FIRST)))]} {LAST[int(g.integers(len(LAST)))]}"


# --------------------------------------------------------------------------------------
# DEMO · simuladores por deporte
# --------------------------------------------------------------------------------------

class BaseSim:
    sport = ""
    total_game_s = 3600.0

    def __init__(self, eid: str, league: str, home: str, away: str, start_ts: float, speed: float,
                 rng: np.random.Generator, meta: dict):
        self.id, self.league, self.home, self.away = eid, league, home, away
        self.start_ts, self.speed, self.rng, self.meta = start_ts, speed, rng, meta
        self.status = "pre"
        self.game_s = 0.0
        self.score = {"home": 0, "away": 0}
        self.timeline: list[dict] = []
        self.shock = {"home": 1.0, "away": 1.0}
        self.last_score_real = -1e9
        self.final_real: float | None = None
        self._ff = False
        self._now = time.time()
        self.players: list[dict] = []

    # --- reloj
    def clock(self) -> str:
        return ""

    def elapsed_frac(self) -> float:
        return min(self.game_s / self.total_game_s, 1.0)

    def advance(self, now: float, dt_real: float) -> None:
        self._now = now
        if self.status == "final":
            return
        self._maybe_shock(now, dt_real)
        if now < self.start_ts:
            self.status = "pre"
            return
        self.status = "live"
        target = (now - self.start_ts) * self.speed
        while self.game_s < target and self.status != "final":
            step = min(30.0, target - self.game_s)
            self._sim(step)
            self.game_s += step
            if self.game_s >= self.total_game_s:
                self._finish(now)

    def fast_forward(self, now: float) -> None:
        self._ff = True
        self.advance(now, 0.0)
        self._ff = False

    def _finish(self, now: float) -> None:
        if self._overtime_needed():
            self._resolve_tie()
        self.status = "final"
        self.final_real = now
        self.timeline.append({"t": "Final", "type": "end", "team": None,
                              "text": f"Final {self.score['home']}–{self.score['away']}"})

    def _overtime_needed(self) -> bool:
        return False

    def _resolve_tie(self) -> None:
        pass

    def _scored(self, team: str, pts: int, text: str) -> None:
        self.score[team] += pts
        if not self._ff:
            self.last_score_real = self._now
        self.timeline.append({"t": self.clock(), "type": "score", "team": team, "text": text})

    def _maybe_shock(self, now: float, dt_real: float) -> None:
        if dt_real <= 0 or self.status == "final":
            return
        horizon = self.start_ts - now
        rate = 1 / 900 if (horizon < 3 * 3600 or self.status == "live") else 1 / 5400
        if self.rng.random() < rate * dt_real:
            self._shock()

    def _shock(self) -> None:
        team = "home" if self.rng.random() < 0.5 else "away"
        up = self.rng.random() < 0.5
        mult = self.rng.uniform(1.08, 1.14) if up else self.rng.uniform(0.86, 0.92)
        self.shock[team] *= mult
        name = self.home if team == "home" else self.away
        txt = (f"Noticia: {name} recupera a un titular" if up else f"Noticia: baja de último minuto en {name}")
        self.timeline.append({"t": self.clock() or "Previa", "type": "news", "team": team, "text": txt})

    # --- interfaz
    def _sim(self, dt_s: float) -> None:
        raise NotImplementedError

    def truth(self) -> dict[str, dict]:
        raise NotImplementedError

    def stats_list(self) -> list[dict]:
        return []

    def live_inputs(self) -> dict:
        return {}

    def model_prior(self) -> dict:
        return {}


class FootballSim(BaseSim):
    sport = "football"
    HT = 300.0
    STOP = 180.0
    total_game_s = 2700 + 300 + 2700 + 180

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        gh, ga = _seeded(self.home, "att"), _seeded(self.away, "att")
        att_h, def_h = float(np.clip(gh.normal(1, 0.16), 0.7, 1.4)), float(np.clip(gh.normal(1, 0.12), 0.75, 1.3))
        att_a, def_a = float(np.clip(ga.normal(1, 0.16), 0.7, 1.4)), float(np.clip(ga.normal(1, 0.12), 0.75, 1.3))
        g = self.meta["goals"]
        self.base = {"home": g / 2 * 1.12 * att_h * def_a, "away": g / 2 * 0.89 * att_a * def_h}
        self.att = {"home": att_h, "away": att_a}
        cr = self.meta["corners"] / 2
        self.corner_rate = {t: cr * (0.8 + 0.2 * self.att[t]) * self.rng.uniform(0.85, 1.2) for t in ("home", "away")}
        self.card_rate = {t: self.meta["cards"] / 2 * self.rng.uniform(0.8, 1.25) for t in ("home", "away")}
        z = lambda: {"home": 0, "away": 0}
        self.xg = {"home": 0.0, "away": 0.0}
        self.shots, self.sot, self.dang, self.corners, self.yellow, self.red = z(), z(), z(), z(), z(), z()
        self.poss = 0.5 + 0.08 * (att_h - att_a)
        lam, mu = self.base["home"], self.base["away"]
        diff = lam - mu
        self.ah_line = -0.5 if diff > 0.25 else (0.5 if diff < -0.25 else 0.0)
        if abs(diff) > 0.85:
            self.ah_line = -1.5 if diff > 0 else 1.5
        exp_c = sum(self.corner_rate.values())
        self.corner_line = math.floor(exp_c) + 0.5
        self.card_line = math.floor(sum(self.card_rate.values())) + 0.5
        self.players = []
        for team, name in (("home", self.home), ("away", self.away)):
            mean = round(float(np.clip(1.1 * self.att[team], 0.6, 1.8)), 2)
            self.players.append({"name": fake_player(name, 0), "team": team, "usage": 0.3, "status": "active",
                                 "true_mean": mean, "proj": round(mean * float(self.rng.normal(1, 0.05)), 2),
                                 "current": 0, "line": math.floor(mean) + 0.5})

    def minute(self) -> float:
        gs = self.game_s
        if gs < 2700:
            return gs / 60
        if gs < 2700 + self.HT:
            return 45.0
        return min(45 + (gs - 2700 - self.HT) / 60, 93.0)

    def is_ht(self) -> bool:
        return 2700 <= self.game_s < 2700 + self.HT

    def clock(self) -> str:
        if self.status == "pre":
            return ""
        if self.status == "final":
            return "Final"
        if self.is_ht():
            return "MT"
        m = self.minute()
        if m > 90:
            return f"90+{int(m - 90) + 1}'"
        return f"{int(m) + 1}'"

    def rate(self, team: str) -> float:
        opp = "away" if team == "home" else "home"
        return (self.base[team] * self.shock[team] * (0.68 ** self.red[team]) * (1.25 ** self.red[opp]))

    def rem_minutes(self) -> float:
        return max(93.0 - self.minute(), 0.0) if self.status != "final" else 0.0

    def _sim(self, dt_s: float) -> None:
        if self.is_ht():
            return
        dm = dt_s / 60.0
        r = self.rng
        for team, name in (("home", self.home), ("away", self.away)):
            lam_pm = self.rate(team) / 90.0
            shot_pm = 12.5 / 90.0 * (self.rate(team) / 1.35) ** 0.6
            mean_xg = float(np.clip(lam_pm / shot_pm, 0.03, 0.45))
            for _ in range(r.poisson(shot_pm * dm)):
                k = 4.0
                x = float(r.beta(mean_xg * k, (1 - mean_xg) * k))
                self.xg[team] += x
                self.shots[team] += 1
                on_target = False
                if r.random() < x:
                    on_target = True
                    scorer = self.players[0 if team == "home" else 1]
                    who = scorer["name"] if r.random() < 0.35 else name
                    self._scored(team, 1, f"Gol de {who}")
                elif r.random() < 0.3:
                    on_target = True
                if on_target:
                    self.sot[team] += 1
                    p = self.players[0 if team == "home" else 1]
                    if r.random() < 0.3:
                        p["current"] += 1
            self.dang[team] += int(r.poisson((40 + 25 * self.att[team]) / 90.0 * dm))
            c = int(r.poisson(self.corner_rate[team] / 90.0 * dm))
            self.corners[team] += c
            if r.random() < self.card_rate[team] / 90.0 * dm:
                self.yellow[team] += 1
                self.timeline.append({"t": self.clock(), "type": "yellow", "team": team, "text": f"Amarilla · {name}"})
            if r.random() < 0.10 / 90.0 * dm and self.red[team] < 2:
                self.red[team] += 1
                if not self._ff:
                    self.last_score_real = self._now
                self.timeline.append({"t": self.clock(), "type": "red", "team": team, "text": f"Roja · {name}"})
        self.poss = float(np.clip(self.poss + r.normal(0, 0.004 * dm), 0.3, 0.7))

    def truth(self) -> dict[str, dict]:
        if self.status == "final":
            return {}
        remf = self.rem_minutes() / 93.0
        lam, mu = self.rate("home") * remf, self.rate("away") * remf
        ch, ca = self.score["home"], self.score["away"]
        m = dc_score_matrix(lam, mu, -0.06)
        n = m.shape[0]
        i = np.arange(n)[:, None] + ch
        j = np.arange(n)[None, :] + ca
        out: dict[str, dict] = {}
        out["1x2"] = {"type": "1x2", "line": None, "main": True,
                      "p": {"home": float(m[i > j].sum()), "draw": float(m[i == j].sum()), "away": float(m[i < j].sum())}}
        tot_lines = [1.5, 2.5, 3.5]
        exp_tot = ch + ca + lam + mu
        main_t = min(tot_lines, key=lambda L: abs(L - exp_tot))
        for L in tot_lines:
            po = float(m[(i + j) > L].sum())
            out[market_key("total", L)] = {"type": "total", "line": L, "main": L == main_t, "p": {"over": po, "under": 1 - po}}
        L = self.ah_line
        d = i - j
        win = float(m[d + L > 0].sum())
        push = float(m[d + L == 0].sum())
        if push < 1e-9:
            out[market_key("ah", L)] = {"type": "ah", "line": L, "main": True, "p": {"home": win, "away": 1 - win}}
        else:
            # línea 0.0 (draw no bet): probabilidades condicionales sin empate
            out[market_key("ah", L)] = {"type": "ah", "line": L, "main": True,
                                        "p": {"home": win / (1 - push), "away": 1 - win / (1 - push)}}
        pb = float(m[(i > 0) & (j > 0)].sum())
        out["btts"] = {"type": "btts", "line": None, "p": {"yes": pb, "no": 1 - pb}}
        mi = self.minute()
        if mi < 44 and not self.is_ht() and self.status != "final":
            share = (45 - mi) * 0.01 / max((45 - mi) * 0.01 + 48 * 0.55 / 45, 1e-9)
            m1 = dc_score_matrix(lam * share, mu * share, -0.06)
            n1 = m1.shape[0]
            s1 = np.arange(n1)[:, None] + np.arange(n1)[None, :] + ch + ca
            for L1 in (0.5, 1.5):
                p1 = float(m1[s1 > L1].sum())
                out[market_key("total_1h", L1)] = {"type": "total_1h", "line": L1, "p": {"over": p1, "under": 1 - p1}}
        for key, rates, cur, L in (("corners", self.corner_rate, self.corners, self.corner_line),
                                   ("cards", self.card_rate, self.yellow, self.card_line)):
            rem_mean = sum(rates.values()) * remf
            now_c = cur["home"] + cur["away"]
            po = float(1 - stats.poisson.cdf(L - now_c, rem_mean)) if L - now_c >= 0 else 1.0
            out[market_key(key, L)] = {"type": key, "line": L, "p": {"over": po, "under": 1 - po}}
        for p in self.players:
            mean_rem = p["true_mean"] * remf * self.shock[p["team"]] ** 0.5
            po = float(1 - stats.poisson.cdf(p["line"] - p["current"], mean_rem)) if p["line"] - p["current"] >= 0 else 1.0
            out[market_key("prop", p["line"], p["name"], "shots_ot")] = {
                "type": "prop", "line": p["line"], "player": p["name"], "stat": "shots_ot",
                "p": {"over": po, "under": 1 - po}}
        return out

    def stats_list(self) -> list[dict]:
        pos_h = round(self.poss * 100)
        return [
            {"label": "xG", "home": round(self.xg["home"], 2), "away": round(self.xg["away"], 2), "fmt": "dec"},
            {"label": "Tiros a puerta", "home": self.sot["home"], "away": self.sot["away"]},
            {"label": "Tiros totales", "home": self.shots["home"], "away": self.shots["away"]},
            {"label": "Ataques peligrosos", "home": self.dang["home"], "away": self.dang["away"]},
            {"label": "Posesión", "home": pos_h, "away": 100 - pos_h, "fmt": "pct"},
            {"label": "Córners", "home": self.corners["home"], "away": self.corners["away"]},
            {"label": "Amarillas", "home": self.yellow["home"], "away": self.yellow["away"]},
            {"label": "Rojas", "home": self.red["home"], "away": self.red["away"]},
        ]

    def live_inputs(self) -> dict:
        return {"xg": dict(self.xg), "corners": dict(self.corners), "cards": dict(self.yellow), "red": dict(self.red)}

    def model_prior(self) -> dict:
        # Proyección pre-partido "externa": conoce el perfil de cada equipo con error de ~6 %.
        g = self.meta["goals"]
        j = _seeded(self.id, "prior")
        return {"goals": (g / 2 * 1.12, g / 2 * 0.89),
                "corners": sum(self.corner_rate.values()) * float(j.normal(1, 0.06)),
                "cards": sum(self.card_rate.values()) * float(j.normal(1, 0.06))}

    def players_view(self) -> list[dict]:
        return [{"name": p["name"], "team": p["team"], "usage": p["usage"], "status": p["status"],
                 "stats": {"shots_ot": {"mean": p["proj"], "current": p["current"], "dist": "poisson"}}}
                for p in self.players]


class GaussSim(BaseSim):
    """Base para NBA y NFL: puntos con incrementos gaussianos o eventos discretos."""
    SD_MARGIN = 12.5
    SD_TOTAL = 18.0
    periods = 4

    def clock(self) -> str:
        if self.status == "pre":
            return ""
        if self.status == "final":
            return "Final"
        per_len = self.total_game_s / self.periods
        q = min(int(self.game_s // per_len) + 1, self.periods)
        left = per_len * q - self.game_s
        return f"{self.PREFIX}{q} {int(left // 60):02d}:{int(left % 60):02d}"

    PREFIX = "Q"

    def _overtime_needed(self) -> bool:
        return self.score["home"] == self.score["away"]

    def _resolve_tie(self) -> None:
        team = "home" if self.rng.random() < 0.5 + (self.exp["home"] - self.exp["away"]) * 0.01 else "away"
        self._scored(team, 3 if self.sport == "americanfootball" else 4, "Prórroga")

    def truth_common(self) -> dict[str, dict]:
        if self.status == "final":
            return {}
        rem = max(1 - self.elapsed_frac(), 1e-3)
        mm = (self.exp["home"] * self.shock["home"] - self.exp["away"] * self.shock["away"]) * rem
        tt = (self.exp["home"] * self.shock["home"] + self.exp["away"] * self.shock["away"]) * rem
        sdm, sdt = self.SD_MARGIN * math.sqrt(rem), self.SD_TOTAL * math.sqrt(rem)
        cd = self.score["home"] - self.score["away"]
        ct = self.score["home"] + self.score["away"]
        ph = float(stats.norm.sf(0, loc=cd + mm, scale=sdm))
        out = {"ml": {"type": "ml", "line": None, "main": True, "p": {"home": ph, "away": 1 - ph}}}
        L = self.spread_line
        pc = float(stats.norm.sf(-L, loc=cd + mm, scale=sdm))
        out[market_key("spread", L)] = {"type": "spread", "line": L, "main": True, "p": {"home": pc, "away": 1 - pc}}
        for T in self.total_lines:
            po = float(stats.norm.sf(T, loc=ct + tt, scale=sdt))
            out[market_key("total", T)] = {"type": "total", "line": T, "main": T == self.total_lines[0],
                                           "p": {"over": po, "under": 1 - po}}
        return out

    def props_truth(self, rem: float) -> dict[str, dict]:
        out = {}
        for p in self.players:
            if p["status"] == "out":
                continue
            for st, d in p["stats"].items():
                mean_rem = d["true_mean"] * rem * (1 + p.get("true_shift", 0.0))
                need = d["line"] - d["current"]
                if d["dist"] == "normal":
                    po = float(stats.norm.sf(need, loc=mean_rem, scale=max(d["sd"] * math.sqrt(rem), 1e-6)))
                else:
                    po = float(1 - stats.poisson.cdf(need, mean_rem)) if need >= 0 else 1.0
                out[market_key("prop", d["line"], p["name"], st)] = {
                    "type": "prop", "line": d["line"], "player": p["name"], "stat": st,
                    "p": {"over": po, "under": 1 - po}}
        return out

    def _shock(self) -> None:
        # En NBA/NFL la noticia más común es la baja de un jugador relevante.
        if self.status == "pre" and self.rng.random() < 0.6:
            cands = [p for p in self.players if p["status"] in ("active", "questionable")]
            if cands:
                p = cands[int(self.rng.integers(len(cands)))]
                if p["status"] == "active" and self.rng.random() < 0.5:
                    p["status"] = "questionable"
                    self.timeline.append({"t": "Previa", "type": "news", "team": p["team"],
                                          "text": f"Reporte: {p['name']} en duda"})
                    return
                p["status"] = "out"
                team = p["team"]
                mates = [q for q in self.players if q["team"] == team and q["status"] != "out"]
                others = sum(q["usage"] for q in mates) or 1.0
                for q in mates:
                    q["true_shift"] = q.get("true_shift", 0.0) + 0.6 * p["usage"] / others
                self.shock[team] *= 1 - 0.12 * p["usage"]
                self.timeline.append({"t": "Previa", "type": "injury", "team": team,
                                      "text": f"Baja confirmada: {p['name']} no juega"})
                return
        super()._shock()

    def players_view(self) -> list[dict]:
        out = []
        for p in self.players:
            out.append({"name": p["name"], "team": p["team"], "usage": p["usage"], "status": p["status"],
                        "stats": {st: {"mean": d["proj"], "sd": d.get("sd"), "current": d["current"], "dist": d["dist"]}
                                  for st, d in p["stats"].items()}})
        return out

    def model_prior(self) -> dict:
        g = _seeded(self.id, "proj")
        return {"margin": self.exp["home"] - self.exp["away"] + float(g.normal(0, 1.5)),
                "total": self.exp["home"] + self.exp["away"] + float(g.normal(0, 3)),
                "home_pts": self.exp["home"], "away_pts": self.exp["away"]}


class BasketballSim(GaussSim):
    sport = "basketball"
    total_game_s = 2880.0
    SD_MARGIN, SD_TOTAL = 12.5, 18.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        gh, ga = _seeded(self.home, "nba"), _seeded(self.away, "nba")
        pace = (float(gh.normal(99, 2.5)) + float(ga.normal(99, 2.5))) / 2
        o_h, d_h = float(gh.normal(115, 3)), float(gh.normal(115, 3))
        o_a, d_a = float(ga.normal(115, 3)), float(ga.normal(115, 3))
        self.exp = {"home": pace * (o_h + d_a) / 200 + 1.6, "away": pace * (o_a + d_h) / 200 - 1.6}
        self.pts_f = {"home": 0.0, "away": 0.0}
        self.reb, self.ast, self.tpm = {"home": 0, "away": 0}, {"home": 0, "away": 0}, {"home": 0, "away": 0}
        diff = self.exp["home"] - self.exp["away"]
        self.spread_line = -(math.floor(abs(diff)) + 0.5) if diff >= 0 else math.floor(abs(diff)) + 0.5
        self.total_lines = [math.floor(sum(self.exp.values())) + 0.5]
        self.players = []
        templates = [(0.31, 27.5, 7.0, 6.5, 3.0), (0.25, 21.0, 5.0, 4.5, 2.3), (0.19, 14.0, 9.5, 2.5, 1.0)]
        for team, name in (("home", self.home), ("away", self.away)):
            for idx, (usage, pts, reb, ast, thr) in enumerate(templates):
                jit = _seeded(name, f"st{idx}")
                stats_d = {}
                for st, mean, dist, sd in (("pts", pts, "normal", 6.5), ("reb", reb, "poisson", None),
                                           ("ast", ast, "poisson", None), ("threes", thr, "poisson", None)):
                    tm = round(mean * float(jit.normal(1, 0.08)), 2)
                    line = (math.floor(tm) + 0.5)
                    stats_d[st] = {"true_mean": tm, "proj": round(tm * float(jit.normal(1, 0.03)), 2), "sd": sd,
                                   "dist": dist, "current": 0, "line": line}
                self.players.append({"name": fake_player(name, idx), "team": team, "usage": usage,
                                     "status": "active", "stats": stats_d})

    def _sim(self, dt_s: float) -> None:
        r = self.rng
        frac = dt_s / self.total_game_s
        for team in ("home", "away"):
            mean = self.exp[team] * self.shock[team] * frac
            inc = max(r.normal(mean, 9.0 * math.sqrt(frac)), 0.0)
            before = int(round(self.pts_f[team]))
            self.pts_f[team] += inc
            after = int(round(self.pts_f[team]))
            if after > before:
                self.score[team] += after - before
                if not self._ff:
                    pass  # en NBA no se suspenden mercados por cada canasta
            self.reb[team] += int(r.poisson(44 * frac))
            self.ast[team] += int(r.poisson(26 * frac))
            self.tpm[team] += int(r.poisson(13 * frac))
            mates = [p for p in self.players if p["team"] == team and p["status"] != "out"]
            for p in mates:
                sh = 1 + p.get("true_shift", 0.0)
                d = p["stats"]["pts"]
                d["current"] += int(round(inc * d["true_mean"] * sh / self.exp[team] * float(r.lognormal(0, 0.35))))
                for st in ("reb", "ast", "threes"):
                    p["stats"][st]["current"] += int(r.poisson(p["stats"][st]["true_mean"] * sh * frac))

    def truth(self) -> dict[str, dict]:
        out = self.truth_common()
        if out:
            out.update(self.props_truth(max(1 - self.elapsed_frac(), 1e-3)))
        return out

    def stats_list(self) -> list[dict]:
        frac = max(self.elapsed_frac(), 1e-3)
        pace = lambda t: round(self.score[t] / frac / 1.15, 1) if self.status != "pre" else 0
        return [
            {"label": "Ritmo proyectado (pos/48)", "home": pace("home"), "away": pace("away"), "fmt": "dec"},
            {"label": "Rebotes", "home": self.reb["home"], "away": self.reb["away"]},
            {"label": "Asistencias", "home": self.ast["home"], "away": self.ast["away"]},
            {"label": "Triples", "home": self.tpm["home"], "away": self.tpm["away"]},
        ]

    def live_inputs(self) -> dict:
        return {"pace": self.stats_list()[0]}


class NFLSim(GaussSim):
    sport = "americanfootball"
    total_game_s = 3600.0
    SD_MARGIN, SD_TOTAL = 13.5, 13.5

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        gh, ga = _seeded(self.home, "nfl"), _seeded(self.away, "nfl")
        self.exp = {"home": float(np.clip(gh.normal(23.5, 3), 16, 31)) + 1.2,
                    "away": float(np.clip(ga.normal(22.5, 3), 15, 30)) - 1.2}
        diff = self.exp["home"] - self.exp["away"]
        self.spread_line = -(math.floor(abs(diff)) + 0.5) if diff >= 0 else math.floor(abs(diff)) + 0.5
        self.total_lines = [math.floor(sum(self.exp.values())) + 0.5]
        self.yds = {"home": 0, "away": 0}
        self.fd = {"home": 0, "away": 0}
        self.to = {"home": 0, "away": 0}
        self.players = []
        for team, name in (("home", self.home), ("away", self.away)):
            for idx, (st, mean, sd, usage) in enumerate((("pass_yds", 248.0, 58.0, 0.45), ("rush_yds", 72.0, 27.0, 0.3),
                                                         ("rec_yds", 70.0, 29.0, 0.25))):
                jit = _seeded(name, f"nfl{idx}")
                tm = round(mean * float(jit.normal(1, 0.1)), 1)
                self.players.append({"name": fake_player(name, 10 + idx), "team": team, "usage": usage, "status": "active",
                                     "stats": {st: {"true_mean": tm, "proj": round(tm * float(jit.normal(1, 0.03)), 1),
                                                    "sd": sd, "dist": "normal", "current": 0,
                                                    "line": math.floor(tm) + 0.5}}})

    def _sim(self, dt_s: float) -> None:
        r = self.rng
        frac = dt_s / self.total_game_s
        for team, name in (("home", self.home), ("away", self.away)):
            rate = self.exp[team] * self.shock[team] / 4.9
            for _ in range(r.poisson(rate * frac)):
                if r.random() < 0.58:
                    self._scored(team, 7, f"Touchdown · {name}")
                else:
                    self._scored(team, 3, f"Gol de campo · {name}")
            self.yds[team] += int(max(r.normal(340 * frac, 40 * math.sqrt(frac)), 0))
            self.fd[team] += int(r.poisson(20 * frac))
            self.to[team] += int(r.poisson(1.3 * frac))
            for p in self.players:
                if p["team"] == team and p["status"] != "out":
                    for st, d in p["stats"].items():
                        d["current"] += int(max(r.normal(d["true_mean"] * (1 + p.get("true_shift", 0)) * frac,
                                                         d["sd"] * math.sqrt(frac)), 0))

    def truth(self) -> dict[str, dict]:
        out = self.truth_common()
        if out:
            out.update(self.props_truth(max(1 - self.elapsed_frac(), 1e-3)))
        return out

    def stats_list(self) -> list[dict]:
        return [
            {"label": "Yardas totales", "home": self.yds["home"], "away": self.yds["away"]},
            {"label": "Primeros downs", "home": self.fd["home"], "away": self.fd["away"]},
            {"label": "Pérdidas de balón", "home": self.to["home"], "away": self.to["away"]},
        ]


class HockeySim(BaseSim):
    sport = "hockey"
    total_game_s = 3600.0

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        gh, ga = _seeded(self.home, "nhl"), _seeded(self.away, "nhl")
        self.base = {"home": float(np.clip(gh.normal(3.2, 0.35), 2.3, 4.2)),
                     "away": float(np.clip(ga.normal(2.95, 0.35), 2.1, 4.0))}
        self.sog = {"home": 0, "away": 0}
        self.hits = {"home": 0, "away": 0}
        self.pp = {"home": 0, "away": 0}
        self.total_lines = [5.5, 6.5]
        self.pl = -1.5 if self.base["home"] >= self.base["away"] else 1.5
        self.players = []
        for team, name in (("home", self.home), ("away", self.away)):
            jit = _seeded(name, "sog")
            tm = round(float(jit.normal(3.4, 0.4)), 2)
            self.players.append({"name": fake_player(name, 20), "team": team, "usage": 0.3, "status": "active",
                                 "stats": {"sog": {"true_mean": tm, "proj": round(tm * float(jit.normal(1, 0.05)), 2),
                                                   "dist": "poisson", "current": 0, "line": math.floor(tm) + 0.5}}})

    def clock(self) -> str:
        if self.status == "pre":
            return ""
        if self.status == "final":
            return "Final"
        p = min(int(self.game_s // 1200) + 1, 3)
        left = 1200 * p - self.game_s
        return f"P{p} {int(left // 60):02d}:{int(left % 60):02d}"

    def _overtime_needed(self) -> bool:
        return self.score["home"] == self.score["away"]

    def _resolve_tie(self) -> None:
        lam, mu = self.base["home"], self.base["away"]
        team = "home" if self.rng.random() < lam / (lam + mu) else "away"
        self._scored(team, 1, "Gol en tiempo extra")

    def _sim(self, dt_s: float) -> None:
        r = self.rng
        frac = dt_s / self.total_game_s
        for team, name in (("home", self.home), ("away", self.away)):
            for _ in range(r.poisson(self.base[team] * self.shock[team] * frac)):
                self._scored(team, 1, f"Gol · {name}")
            s = int(r.poisson(30 * frac))
            self.sog[team] += s
            self.hits[team] += int(r.poisson(21 * frac))
            self.pp[team] += int(r.poisson(3 * frac))
            for p in self.players:
                if p["team"] == team:
                    p["stats"]["sog"]["current"] += int(r.poisson(p["stats"]["sog"]["true_mean"] * frac))

    def truth(self) -> dict[str, dict]:
        if self.status == "final":
            return {}
        rem = max(1 - self.elapsed_frac(), 1e-3)
        lam, mu = self.base["home"] * self.shock["home"] * rem, self.base["away"] * self.shock["away"] * rem
        m = dc_score_matrix(lam, mu, 0.0, 14)
        ch, ca = self.score["home"], self.score["away"]
        n = m.shape[0]
        i = np.arange(n)[:, None] + ch
        j = np.arange(n)[None, :] + ca
        p_ot = self.base["home"] / sum(self.base.values())
        ph = float(m[i > j].sum() + m[i == j].sum() * p_ot)
        out = {"ml": {"type": "ml", "line": None, "main": True, "p": {"home": ph, "away": 1 - ph}}}
        for T in self.total_lines:
            # el tiempo extra suma un gol si hay empate
            po = float(m[(i + j) > T].sum() + m[((i + j) == T - 0.5) & (i == j)].sum())
            out[market_key("total", T)] = {"type": "total", "line": T, "main": T == 5.5, "p": {"over": po, "under": 1 - po}}
        L = self.pl
        pc = float(m[(i - j) + L > 0].sum())
        out[market_key("spread", L)] = {"type": "spread", "line": L, "main": True, "p": {"home": pc, "away": 1 - pc}}
        for p in self.players:
            d = p["stats"]["sog"]
            need = d["line"] - d["current"]
            po = float(1 - stats.poisson.cdf(need, d["true_mean"] * rem)) if need >= 0 else 1.0
            out[market_key("prop", d["line"], p["name"], "sog")] = {"type": "prop", "line": d["line"], "player": p["name"],
                                                                    "stat": "sog", "p": {"over": po, "under": 1 - po}}
        return out

    def stats_list(self) -> list[dict]:
        return [
            {"label": "Tiros a gol", "home": self.sog["home"], "away": self.sog["away"]},
            {"label": "Golpes", "home": self.hits["home"], "away": self.hits["away"]},
            {"label": "Power plays", "home": self.pp["home"], "away": self.pp["away"]},
        ]

    def model_prior(self) -> dict:
        return {"goals": (3.15, 2.95)}

    def players_view(self) -> list[dict]:
        return [{"name": p["name"], "team": p["team"], "usage": p["usage"], "status": p["status"],
                 "stats": {"sog": {"mean": p["stats"]["sog"]["proj"], "current": p["stats"]["sog"]["current"],
                                   "dist": "poisson"}}} for p in self.players]


# --------------------------------------------------------------------------------------
# DEMO · Feed
# --------------------------------------------------------------------------------------

# Velocidad relativa del reloj de juego (fútbol = 1). En la vida real un partido de NBA dura
# ~3x su tiempo de juego; así el demo conserva un ritmo de cuotas creíble.
SPORT_CLOCK = {"football": 1.0, "basketball": 0.35, "americanfootball": 0.35, "hockey": 0.5}


class DemoFeed:
    """Streaming realista sin llaves: el sharp refleja la 'verdad' del simulador al instante y
    cada casa mexicana la ve con su propio retraso, margen y sesgo (proceso Ornstein-Uhlenbeck)."""

    name = "demo"

    def __init__(self, logos: LogoResolver | None, speed: float = 4.0, seed: int | None = None):
        self.logos = logos
        self.speed = max(speed, 0.5)
        self.rng = np.random.default_rng(seed)
        self.sims: dict[str, BaseSim] = {}
        self.queues: dict[str, deque] = {}
        self.lags: dict[tuple[str, str], float] = {}
        self.bias: dict[tuple, float] = {}
        self.last_step = time.time()
        self.counter = 0
        self.status = {"mode": "demo", "detail": "Simulador Zero-Config activo (sin ODDS_API_KEY)."}
        self._bootstrap()

    # ---------------------------------------------------------------- creación
    def _pick_teams(self, pool: list[str]) -> tuple[str, str]:
        busy = {s.home for s in self.sims.values()} | {s.away for s in self.sims.values()}
        free = [t for t in pool if t not in busy] or pool
        idx = self.rng.choice(len(free), size=2, replace=False)
        return free[int(idx[0])], free[int(idx[1])]

    def _new(self, sport: str, start_ts: float, league: str | None = None) -> BaseSim:
        self.counter += 1
        eid = f"demo-{self.counter}-{int(start_ts)}"
        rng = np.random.default_rng(int(self.rng.integers(1 << 31)))
        if sport == "football":
            league = league or str(self.rng.choice(list(FOOTBALL_LEAGUES)))
            meta = FOOTBALL_LEAGUES[league]
            h, a = self._pick_teams(meta["teams"])
            sim = FootballSim(eid, league, h, a, start_ts, self.speed, rng, meta)
        elif sport == "basketball":
            h, a = self._pick_teams(NBA_TEAMS)
            sim = BasketballSim(eid, "NBA", h, a, start_ts, self.speed * SPORT_CLOCK["basketball"], rng, {})
        elif sport == "americanfootball":
            h, a = self._pick_teams(NFL_TEAMS)
            sim = NFLSim(eid, "NFL", h, a, start_ts, self.speed * SPORT_CLOCK["americanfootball"], rng, {})
        else:
            h, a = self._pick_teams(NHL_TEAMS)
            sim = HockeySim(eid, "NHL", h, a, start_ts, self.speed * SPORT_CLOCK["hockey"], rng, {})
        for book, prof in BOOK_PROFILES.items():
            lo, hi = prof["lag"]
            self.lags[(eid, book)] = float(self.rng.uniform(lo, hi)) if hi > 0 else 0.0
        self.sims[eid] = sim
        self.queues[eid] = deque(maxlen=60)
        return sim

    def _bootstrap(self) -> None:
        now = time.time()
        live = [("football", 8), ("football", 23), ("football", 41), ("football", 57), ("football", 74),
                ("basketball", 0.35), ("basketball", 0.62), ("americanfootball", 0.55), ("hockey", 0.4)]
        for sport, at in live:
            if sport == "football":
                game_s = at * 60 if at < 45 else 2700 + 300 + (at - 45) * 60
            else:
                game_s = at * {"basketball": 2880, "americanfootball": 3600, "hockey": 3600}[sport]
            sim = self._new(sport, now - game_s / (self.speed * SPORT_CLOCK[sport]))
            sim.fast_forward(now)
        for sport, mins in (("football", 4), ("football", 12), ("basketball", 9), ("football", 25),
                            ("hockey", 35), ("americanfootball", 60), ("football", 90)):
            self._new(sport, now + mins * 60)
        local = datetime.now(timezone(timedelta(hours=-6)))
        for d, hour, sport in ((1, 19, "football"), (1, 20, "basketball"), (2, 18, "football"),
                               (2, 21, "basketball"), (3, 12, "americanfootball"), (4, 19, "football"),
                               (5, 18, "hockey"), (6, 17, "football")):
            dt = (local + timedelta(days=d)).replace(hour=hour, minute=0, second=0, microsecond=0)
            self._new(sport, dt.timestamp())
        for sim in self.sims.values():
            self._snapshot(sim, now - 300)
            self._snapshot(sim, now)

    # ---------------------------------------------------------------- ciclo
    def _snapshot(self, sim: BaseSim, ts: float) -> None:
        self.queues[sim.id].append((ts, sim.truth()))

    def step(self, now: float | None = None) -> None:
        now = now or time.time()
        dt = max(now - self.last_step, 0.0)
        self.last_step = now
        for sim in list(self.sims.values()):
            sim.advance(now, dt)
            self._snapshot(sim, now)
        # reciclar partidos terminados
        for eid, sim in list(self.sims.items()):
            if sim.status == "final" and sim.final_real and now - sim.final_real > 180:
                sport = sim.sport
                del self.sims[eid]
                self.queues.pop(eid, None)
                self._new(sport, now + float(self.rng.uniform(5, 25)) * 60)
                self._snapshot(list(self.sims.values())[-1], now)
        self._update_bias(dt)

    def _update_bias(self, dt: float) -> None:
        tau = 240.0
        a = math.exp(-dt / tau) if dt > 0 else 1.0
        s = math.sqrt(max(1 - a * a, 0.0))
        for k in list(self.bias):
            if k[0] not in self.sims:
                del self.bias[k]
                continue
            sd = self._noise_sd("Codere" if k[3] == "__common__" else k[3], k[1])
            self.bias[k] = self.bias[k] * a + sd * s * float(self.rng.standard_normal())

    def _noise_sd(self, book: str, mtype: str) -> float:
        return BOOK_PROFILES[book]["noise"] * SOFT_MARKET_NOISE.get(mtype, 1.0)

    def _view(self, eid: str, lag: float, now: float) -> dict:
        q = self.queues.get(eid)
        if not q:
            return {}
        target = now - lag
        view = q[0][1]
        for ts, snap in q:
            if ts <= target:
                view = snap
            else:
                break
        return view

    def _quote(self, sim: BaseSim, book: str, key: str, mk: dict, now: float) -> dict[str, float] | None:
        prof = BOOK_PROFILES[book]
        if mk["type"] not in prof["markets"]:
            return None
        if prof.get("main_only") and mk["type"] == "total" and not mk.get("main"):
            return None
        probs = mk["p"]
        if max(probs.values()) > 0.975:
            return None
        sels = list(probs)
        p = np.array([max(probs[s], 1e-4) for s in sels])
        if not prof.get("sharp"):
            # Las casas MX comparten proveedores de cuotas: su sesgo tiene un componente común
            # (60 % de la varianza) y uno propio de cada casa (40 %).
            offs = []
            for s in sels:
                ck = (sim.id, mk["type"], key, "__common__", s)
                bk = (sim.id, mk["type"], key, book, s)
                for kk, b in ((ck, "Codere"), (bk, book)):
                    if kk not in self.bias:
                        self.bias[kk] = self._noise_sd(b, mk["type"]) * float(self.rng.standard_normal())
                scale = BOOK_PROFILES[book]["noise"] / BOOK_PROFILES["Codere"]["noise"]
                offs.append(math.sqrt(0.6) * self.bias[ck] * scale + math.sqrt(0.4) * self.bias[bk])
            p = p * np.exp(np.array(offs))
        p = p / p.sum()
        odds = apply_margin(p, prof["margin"] * (1.5 if mk["type"] in SOFT_MARKET_NOISE else 1.0))
        dec = prof["decimals"]
        return {s: max(round(float(o), dec), 1.01) for s, o in zip(sels, odds)}

    # ---------------------------------------------------------------- salida
    def events(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        out = []
        for sim in self.sims.values():
            current = self.queues[sim.id][-1][1] if self.queues[sim.id] else {}
            markets: dict[str, dict] = {}
            for book, prof in BOOK_PROFILES.items():
                susp = prof.get("susp", self.lags[(sim.id, book)] + 12)
                if sim.status == "live" and now - sim.last_score_real < susp:
                    continue
                # In-play las casas usan feeds automáticos: el retraso se reduce ~80 %.
                lag = self.lags[(sim.id, book)] * (0.2 if sim.status == "live" else 1.0)
                view = current if prof.get("sharp") else self._view(sim.id, lag, now)
                for key, mk in view.items():
                    if key not in current:
                        continue  # mercado ya cerrado
                    if mk["type"] == "prop" and sim.status == "live" and book not in LIVE_PROP_BOOKS:
                        continue
                    q = self._quote(sim, book, key, mk, now)
                    if not q:
                        continue
                    m = markets.setdefault(key, {"key": key, "type": mk["type"], "line": mk.get("line"),
                                                 "player": mk.get("player"), "stat": mk.get("stat"),
                                                 "selections": {s: {} for s in mk["p"]}})
                    for s, o in q.items():
                        m["selections"].setdefault(s, {})[book] = o
            sport = sim.sport
            ev = {
                "id": sim.id, "sport": sport, "league": sim.league,
                "league_logo": self.logos.league(sim.league) if self.logos else None,
                "home": {"name": sim.home, "short": short_name(sim.home),
                         "logo": self.logos.team(sim.home, sport) if self.logos else None},
                "away": {"name": sim.away, "short": short_name(sim.away),
                         "logo": self.logos.team(sim.away, sport) if self.logos else None},
                "start_ts": sim.start_ts, "status": sim.status, "clock": sim.clock(),
                "minute": sim.minute() if isinstance(sim, FootballSim) else None,
                "elapsed_frac": round(sim.elapsed_frac(), 4) if sim.status != "pre" else 0.0,
                "score": dict(sim.score) if sim.status != "pre" else None,
                "stats": sim.stats_list() if sim.status != "pre" else [],
                "live": sim.live_inputs(), "timeline": sim.timeline[-20:], "markets": markets,
                "players": sim.players_view(), "model_prior": sim.model_prior(), "source": "demo",
            }
            if isinstance(sim, FootballSim):
                ev["elapsed_frac"] = round(min(sim.minute() / 93.0, 1.0), 4) if sim.status != "pre" else 0.0
            out.append(ev)
        out.sort(key=lambda e: ({"live": 0, "pre": 1, "final": 2}[e["status"]], e["start_ts"]))
        return out

    async def run(self) -> None:
        # El demo avanza en main.py (step) sincronizado con el tick de 5 s.
        return None


# --------------------------------------------------------------------------------------
# LIVE · The Odds API
# --------------------------------------------------------------------------------------

ODDS_API_BOOK_NAMES = {"pinnacle": "Pinnacle", "betfair_ex_eu": "Betfair Exchange", "betfair_ex_uk": "Betfair Exchange",
                       "betway": "Betway"}
PROP_MARKETS = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast", "player_threes": "threes",
                "player_pass_yds": "pass_yds", "player_rush_yds": "rush_yds", "player_reception_yds": "rec_yds",
                "player_shots_on_goal": "sog", "pitcher_strikeouts": "k"}
PROP_BY_SPORT = {"basketball": ["player_points", "player_rebounds", "player_assists", "player_threes"],
                 "americanfootball": ["player_pass_yds", "player_rush_yds", "player_reception_yds"],
                 "hockey": ["player_shots_on_goal"], "baseball": ["pitcher_strikeouts"]}
LEAGUE_TITLES = {"soccer_mexico_ligamx": "Liga MX", "soccer_epl": "Premier League", "soccer_spain_la_liga": "LaLiga",
                 "soccer_italy_serie_a": "Serie A", "soccer_germany_bundesliga": "Bundesliga",
                 "soccer_uefa_champs_league": "Champions League", "basketball_nba": "NBA",
                 "americanfootball_nfl": "NFL", "icehockey_nhl": "NHL", "baseball_mlb": "MLB"}
TYPICAL_REAL_DURATION = {"football": 115 * 60, "basketball": 140 * 60, "americanfootball": 190 * 60,
                         "hockey": 150 * 60, "baseball": 180 * 60}


def sport_family(sport_key: str) -> str:
    if sport_key.startswith("soccer_"):
        return "football"
    if sport_key.startswith("basketball_"):
        return "basketball"
    if sport_key.startswith("americanfootball_"):
        return "americanfootball"
    if sport_key.startswith("icehockey_"):
        return "hockey"
    if sport_key.startswith("baseball_"):
        return "baseball"
    return "other"


class OddsApiSource:
    BASE = "https://api.the-odds-api.com/v4"

    def __init__(self, api_key: str, sports: list[str], bookmakers: list[str], mx_keys: set[str],
                 poll_s: float, props: bool, props_max_events: int):
        self.key = api_key
        self.sports = sports
        self.bookmakers = bookmakers
        self.mx_keys = mx_keys
        self.poll_s = max(poll_s, 20.0)
        self.props = props
        self.props_max = props_max_events
        self.raw: dict[str, dict] = {}
        self.scores: dict[str, dict] = {}
        self.prop_raw: dict[str, list] = {}
        self.status = {"ok": False, "detail": "iniciando", "remaining": None, "used": None, "last": None}

    def book_name(self, key: str, title: str) -> str | None:
        if key in ODDS_API_BOOK_NAMES:
            return ODDS_API_BOOK_NAMES[key]
        if key in self.mx_keys:
            return title
        return None

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict) -> Any:
        params = {"apiKey": self.key, **params}
        r = await client.get(self.BASE + path, params=params)
        self.status["remaining"] = r.headers.get("x-requests-remaining", self.status["remaining"])
        self.status["used"] = r.headers.get("x-requests-used", self.status["used"])
        if r.status_code == 401:
            raise RuntimeError("ODDS_API_KEY inválida")
        if r.status_code == 429:
            raise RuntimeError("Cuota de The Odds API agotada (429)")
        r.raise_for_status()
        return r.json()

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            while True:
                try:
                    for sk in self.sports:
                        data = await self._get(client, f"/sports/{sk}/odds", {
                            "bookmakers": ",".join(self.bookmakers), "markets": "h2h,totals,spreads",
                            "oddsFormat": "decimal", "dateFormat": "unix"})
                        seen = set()
                        for ev in data:
                            ev["_sport_key"] = sk
                            self.raw[ev["id"]] = ev
                            seen.add(ev["id"])
                        for eid in [e for e, v in self.raw.items() if v["_sport_key"] == sk and e not in seen]:
                            self.raw.pop(eid, None)
                        if any(v["commence_time"] <= time.time() for v in data):
                            sc = await self._get(client, f"/sports/{sk}/scores", {"daysFrom": 1, "dateFormat": "unix"})
                            for s in sc:
                                self.scores[s["id"]] = s
                        if self.props and sport_family(sk) in PROP_BY_SPORT:
                            upcoming = sorted([v for v in data], key=lambda v: v["commence_time"])[: self.props_max]
                            for v in upcoming:
                                pd = await self._get(client, f"/sports/{sk}/events/{v['id']}/odds", {
                                    "bookmakers": ",".join(self.bookmakers),
                                    "markets": ",".join(PROP_BY_SPORT[sport_family(sk)]),
                                    "oddsFormat": "decimal", "dateFormat": "unix"})
                                self.prop_raw[v["id"]] = pd.get("bookmakers", [])
                    self.status.update(ok=True, detail=f"{len(self.raw)} eventos", last=time.time())
                except Exception as exc:
                    self.status.update(ok=False, detail=str(exc), last=time.time())
                await asyncio.sleep(self.poll_s)

    def build_markets(self, ev: dict, fam: str) -> dict[str, dict]:
        home, away = ev["home_team"], ev["away_team"]
        markets: dict[str, dict] = {}

        def put(key, mtype, line, sel, book, price, **extra):
            m = markets.setdefault(key, {"key": key, "type": mtype, "line": line, "selections": {}, **extra})
            m["selections"].setdefault(sel, {})[book] = float(price)

        for bm in ev.get("bookmakers", []):
            book = self.book_name(bm["key"], bm.get("title", bm["key"]))
            if not book:
                continue
            for mk in bm.get("markets", []):
                outs = mk.get("outcomes", [])
                if mk["key"] == "h2h":
                    mtype = "1x2" if fam == "football" else "ml"
                    for o in outs:
                        sel = "draw" if o["name"].lower() == "draw" else ("home" if o["name"] == home else "away")
                        put(mtype, mtype, None, sel, book, o["price"])
                elif mk["key"] == "totals":
                    for o in outs:
                        L = float(o.get("point"))
                        put(market_key("total", L), "total", L, o["name"].lower(), book, o["price"])
                elif mk["key"] == "spreads":
                    hp = next((float(o["point"]) for o in outs if o["name"] == home), None)
                    if hp is None:
                        continue
                    mtype = "ah" if fam == "football" else "spread"
                    for o in outs:
                        sel = "home" if o["name"] == home else "away"
                        put(market_key(mtype, hp), mtype, hp, sel, book, o["price"])
        for bm in self.prop_raw.get(ev["id"], []):
            book = self.book_name(bm["key"], bm.get("title", bm["key"]))
            if not book:
                continue
            for mk in bm.get("markets", []):
                stat = PROP_MARKETS.get(mk["key"])
                if not stat:
                    continue
                for o in mk.get("outcomes", []):
                    if o.get("point") is None or o.get("name", "").lower() not in ("over", "under"):
                        continue
                    L = float(o["point"])
                    player = o.get("description", "")
                    put(market_key("prop", L, player, stat), "prop", L, o["name"].lower(), book, o["price"],
                        player=player, stat=stat)
        # descarta mercados con una sola selección
        return {k: m for k, m in markets.items() if len(m["selections"]) >= 2}


# --------------------------------------------------------------------------------------
# LIVE · API-Sports (fútbol)
# --------------------------------------------------------------------------------------

class ApiSportsSource:
    BASE = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str, poll_s: float, with_stats: bool, stats_max: int):
        self.key = api_key
        self.poll_s = max(poll_s, 30.0)
        self.with_stats = with_stats
        self.stats_max = stats_max
        self.fixtures: list[dict] = []
        self.stats: dict[int, dict] = {}
        self.status = {"ok": False, "detail": "iniciando", "last": None}

    async def run(self) -> None:
        headers = {"x-apisports-key": self.key}
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            while True:
                try:
                    r = await client.get(self.BASE + "/fixtures", params={"live": "all"})
                    r.raise_for_status()
                    body = r.json()
                    if body.get("errors"):
                        raise RuntimeError(str(body["errors"]))
                    self.fixtures = body.get("response", [])
                    if self.with_stats:
                        for fx in self.fixtures[: self.stats_max]:
                            fid = fx["fixture"]["id"]
                            rs = await client.get(self.BASE + "/fixtures/statistics", params={"fixture": fid})
                            if rs.status_code == 200:
                                self.stats[fid] = self._parse_stats(rs.json().get("response", []))
                    self.status.update(ok=True, detail=f"{len(self.fixtures)} partidos en vivo", last=time.time())
                except Exception as exc:
                    self.status.update(ok=False, detail=str(exc), last=time.time())
                await asyncio.sleep(self.poll_s)

    @staticmethod
    def _parse_stats(resp: list) -> dict:
        out: dict[str, dict] = {}
        for i, side in enumerate(resp[:2]):
            team = "home" if i == 0 else "away"
            for s in side.get("statistics", []):
                v = s.get("value")
                if isinstance(v, str):
                    v = v.replace("%", "")
                    try:
                        v = float(v)
                    except ValueError:
                        continue
                out.setdefault(s.get("type", ""), {})[team] = v or 0
        return out

    def match(self, home: str, away: str) -> dict | None:
        best, score = None, 0.0
        for fx in self.fixtures:
            s = (name_score(fx["teams"]["home"]["name"], home) + name_score(fx["teams"]["away"]["name"], away)) / 2
            if s > score:
                best, score = fx, s
        return best if score >= 0.62 else None


# --------------------------------------------------------------------------------------
# LIVE · Scrapers de casas mexicanas (Caliente.mx, Codere, Stake)
# --------------------------------------------------------------------------------------

PRICE_KEYS = ("odds", "price", "decimal", "odd", "oddsDecimal", "priceDecimal", "decimalOdds", "coef", "value", "o")
NAME_KEYS = ("name", "label", "outcome", "selection", "title", "shortName", "outcomeName", "selectionName", "n")
ML_WORDS = ("1x2", "resultado", "ganador del partido", "match result", "full time result", "moneyline",
            "money line", "ganador", "winner", "tiempo reglamentario", "match winner")
TOTAL_WORDS = ("total", "más/menos", "mas/menos", "over/under", "goles más", "goles totales", "total de puntos",
               "total points", "total goals")


def _to_decimal(v: Any) -> float | None:
    try:
        if isinstance(v, str):
            v = v.strip()
            if "/" in v:
                a, b = v.split("/", 1)
                return 1.0 + float(a) / float(b)
            v = float(v.replace(",", "."))
        v = float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if abs(v) >= 100 and float(v).is_integer():  # momio americano
        return 1.0 + (v / 100.0 if v > 0 else 100.0 / abs(v))
    if 1.01 <= v <= 1000:
        return v
    return None


def _name_of(d: dict) -> str | None:
    for k in NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("es", "es-MX", "en", "default", "text"):
                if isinstance(v.get(kk), str):
                    return v[kk]
    return None


def _price_of(d: dict) -> float | None:
    for k in PRICE_KEYS:
        if k in d:
            p = _to_decimal(d[k])
            if p:
                return p
    return None


def _teams_from(d: dict) -> tuple[str, str] | None:
    for hk, ak in (("home", "away"), ("homeTeam", "awayTeam"), ("homeName", "awayName"), ("team1", "team2")):
        h, a = d.get(hk), d.get(ak)
        if isinstance(h, dict):
            h = _name_of(h)
        if isinstance(a, dict):
            a = _name_of(a)
        if isinstance(h, str) and isinstance(a, str):
            return h, a
    comp = d.get("competitors") or d.get("participants") or d.get("teams")
    if isinstance(comp, list) and len(comp) == 2 and all(isinstance(c, dict) for c in comp):
        names = [_name_of(c) for c in comp]
        if all(names):
            return names[0], names[1]
    nm = _name_of(d)
    if nm:
        for sep in (" vs. ", " vs ", " v ", " - ", " – "):
            if sep in nm:
                h, a = nm.split(sep, 1)
                if len(h) > 1 and len(a) > 1:
                    return h.strip(), a.strip()
    return None


def extract_quotes(obj: Any) -> list[dict]:
    """Extractor heurístico: recorre cualquier JSON de un sportsbook y devuelve cuotas
    {home, away, mtype, line, selection, odds} cuando reconoce mercado 1X2/ganador o totales."""
    out: list[dict] = []

    def visit(node: Any, teams: tuple[str, str] | None, market_name: str | None) -> None:
        if isinstance(node, dict):
            t = _teams_from(node) or teams
            nm = _name_of(node)
            for v in node.values():
                if isinstance(v, list) and len(v) >= 2 and all(isinstance(x, dict) for x in v):
                    priced = [(x, _name_of(x), _price_of(x)) for x in v]
                    if all(p is not None and n for _, n, p in priced) and t:
                        _emit(out, t, (nm or market_name or "").lower(), [(n, p) for _, n, p in priced])
                        continue
                visit(v, t, nm or market_name)
        elif isinstance(node, list):
            for x in node:
                visit(x, teams, market_name)

    visit(obj, None, None)
    return out


def _emit(out: list, teams: tuple[str, str], mname: str, outcomes: list[tuple[str, float]]) -> None:
    home, away = teams
    names = [n.lower() for n, _ in outcomes]
    is_total = any(w in mname for w in TOTAL_WORDS) or all(
        n.startswith(("más", "mas", "over", "menos", "under", "o ", "u ")) for n in names)
    if is_total and len(outcomes) == 2:
        for n, p in outcomes:
            num = re.search(r"(\d+(?:[.,]\d+)?)", n) or re.search(r"(\d+(?:[.,]\d+)?)", mname)
            if not num:
                return
            L = float(num.group(1).replace(",", "."))
            sel = "over" if n.lower().startswith(("más", "mas", "over", "o ")) else "under"
            out.append({"home": home, "away": away, "mtype": "total", "line": L, "selection": sel, "odds": p})
        return
    if any(w in mname for w in ML_WORDS) or len(outcomes) in (2, 3):
        if len(outcomes) not in (2, 3):
            return
        res = []
        for n, p in outcomes:
            nl = n.lower()
            if nl in ("x", "empate", "draw", "tie"):
                sel = "draw"
            elif nl == "1" or name_score(n, home) >= 0.75:
                sel = "home"
            elif nl == "2" or name_score(n, away) >= 0.75:
                sel = "away"
            else:
                return
            res.append((sel, p))
        if len({s for s, _ in res}) != len(res):
            return
        mtype = "1x2" if len(res) == 3 else "ml"
        for sel, p in res:
            out.append({"home": home, "away": away, "mtype": mtype, "line": None, "selection": sel, "odds": p})


class MXScraper:
    """Lee el feed JSON público de una casa mexicana.

    · mode=http        GET (o POST con *_FEED_BODY) con httpx, User-Agent rotativo.
    · mode=playwright  Abre la página en Chromium headless y captura todas las respuestas JSON.
    No resuelve CAPTCHAs ni evade protecciones anti-bot: si la casa bloquea, se reporta el estado.
    """

    def __init__(self, book: str, url: str, mode: str = "http", body: str | None = None, poll_s: float = 20.0):
        self.book, self.url, self.mode, self.body = book, url, mode, body
        self.poll_s = max(poll_s, 8.0)
        self.quotes: list[dict] = []
        self.status = {"ok": False, "detail": "iniciando", "last": None, "count": 0}
        self._ua = 0

    def _headers(self) -> dict:
        self._ua = (self._ua + 1) % len(USER_AGENTS)
        return {"User-Agent": USER_AGENTS[self._ua], "Accept": "application/json, text/plain, */*",
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.6", "Referer": self.url}

    async def run(self) -> None:
        if self.mode == "playwright":
            await self._run_playwright()
        else:
            await self._run_http()

    async def _run_http(self) -> None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            while True:
                try:
                    if self.body:
                        r = await client.post(self.url, content=self.body,
                                              headers={**self._headers(), "Content-Type": "application/json"})
                    else:
                        r = await client.get(self.url, headers=self._headers())
                    if r.status_code in (403, 429, 503):
                        raise RuntimeError(f"bloqueado por la casa (HTTP {r.status_code})")
                    r.raise_for_status()
                    self.quotes = extract_quotes(r.json())
                    self.status.update(ok=bool(self.quotes), count=len(self.quotes), last=time.time(),
                                       detail=f"{len(self.quotes)} cuotas" if self.quotes else "JSON sin cuotas reconocibles")
                except Exception as exc:
                    self.status.update(ok=False, detail=str(exc)[:160], last=time.time())
                await asyncio.sleep(self.poll_s + random.uniform(0, 3))

    async def _run_playwright(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.status.update(ok=False, detail="Playwright no instalado: pip install playwright && playwright install chromium")
            return
        captured: list[dict] = []

        async def on_response(resp):
            try:
                if "json" in (resp.headers.get("content-type") or ""):
                    captured.extend(extract_quotes(await resp.json()))
            except Exception:
                pass

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            while True:
                ctx = await browser.new_context(user_agent=self._headers()["User-Agent"], locale="es-MX")
                page = await ctx.new_page()
                page.on("response", on_response)
                try:
                    captured.clear()
                    await page.goto(self.url, wait_until="networkidle", timeout=45000)
                    await page.wait_for_timeout(3000)
                    self.quotes = list(captured)
                    self.status.update(ok=bool(self.quotes), count=len(self.quotes), last=time.time(),
                                       detail=f"{len(self.quotes)} cuotas" if self.quotes else "sin cuotas en las respuestas JSON")
                except Exception as exc:
                    self.status.update(ok=False, detail=str(exc)[:160], last=time.time())
                finally:
                    await ctx.close()
                await asyncio.sleep(self.poll_s)


# --------------------------------------------------------------------------------------
# LIVE · Feed combinado
# --------------------------------------------------------------------------------------

class LiveFeed:
    name = "live"

    def __init__(self, odds: OddsApiSource, apisports: ApiSportsSource | None, scrapers: list[MXScraper],
                 logos: LogoResolver | None):
        self.odds, self.apisports, self.scrapers, self.logos = odds, apisports, scrapers, logos
        self.status = {"mode": "live", "detail": "The Odds API + fuentes en vivo"}

    def step(self, now: float | None = None) -> None:
        return None

    async def run(self) -> None:
        tasks = [asyncio.create_task(self.odds.run())]
        if self.apisports:
            tasks.append(asyncio.create_task(self.apisports.run()))
        tasks += [asyncio.create_task(s.run()) for s in self.scrapers]
        await asyncio.gather(*tasks)

    def sources(self) -> dict:
        out = {"The Odds API": self.odds.status}
        if self.apisports:
            out["API-Sports"] = self.apisports.status
        for s in self.scrapers:
            out[s.book] = s.status
        return out

    def events(self) -> list[dict]:
        now = time.time()
        out = []
        for raw in list(self.odds.raw.values()):
            fam = sport_family(raw["_sport_key"])
            if fam == "other":
                continue
            start = float(raw["commence_time"])
            if start - now > 7 * 86400:
                continue
            home, away = raw["home_team"], raw["away_team"]
            league = LEAGUE_TITLES.get(raw["_sport_key"], raw.get("sport_title", raw["_sport_key"]))
            markets = self.odds.build_markets(raw, fam)
            self._merge_scrapers(markets, home, away, fam)
            sc = self.odds.scores.get(raw["id"])
            status = "pre"
            score = None
            if sc and sc.get("completed"):
                status = "final"
            elif start <= now:
                status = "live"
            if sc and sc.get("scores"):
                d = {s["name"]: s["score"] for s in sc["scores"]}
                try:
                    score = {"home": int(float(d.get(home, 0))), "away": int(float(d.get(away, 0)))}
                except (TypeError, ValueError):
                    score = None
            elapsed = min(max((now - start) / TYPICAL_REAL_DURATION[fam], 0.0), 0.97) if status == "live" else 0.0
            clock = "EN VIVO" if status == "live" else ("Final" if status == "final" else "")
            minute = None
            stats_l, live_in, h_logo, a_logo, l_logo = [], {}, None, None, None
            if fam == "football":
                minute = elapsed * 93 if status == "live" else None
                fx = self.apisports.match(home, away) if (self.apisports and status == "live") else None
                if fx:
                    el = fx["fixture"]["status"].get("elapsed") or 0
                    short = fx["fixture"]["status"].get("short")
                    minute = float(el)
                    elapsed = min(minute / 93.0, 1.0)
                    clock = "MT" if short == "HT" else f"{el}'"
                    score = {"home": fx["goals"]["home"] or 0, "away": fx["goals"]["away"] or 0}
                    h_logo, a_logo = fx["teams"]["home"].get("logo"), fx["teams"]["away"].get("logo")
                    l_logo = fx["league"].get("logo")
                    st = self.apisports.stats.get(fx["fixture"]["id"], {})
                    if st:
                        stats_l, live_in = self._football_stats(st)
            if status == "live" and score is None:
                score = {"home": 0, "away": 0}
            ev = {
                "id": raw["id"], "sport": fam, "league": league,
                "league_logo": l_logo or (self.logos.league(league) if self.logos else None),
                "home": {"name": home, "short": short_name(home), "logo": h_logo or (self.logos.team(home, fam) if self.logos else None)},
                "away": {"name": away, "short": short_name(away), "logo": a_logo or (self.logos.team(away, fam) if self.logos else None)},
                "start_ts": start, "status": status, "clock": clock, "minute": minute,
                "elapsed_frac": round(elapsed, 4), "score": score, "stats": stats_l, "live": live_in,
                "timeline": [], "markets": markets, "players": [], "model_prior": {}, "source": "odds_api",
            }
            out.append(ev)
        out.sort(key=lambda e: ({"live": 0, "pre": 1, "final": 2}[e["status"]], e["start_ts"]))
        return out

    @staticmethod
    def _football_stats(st: dict) -> tuple[list, dict]:
        def g(k, t):
            return st.get(k, {}).get(t, 0) or 0
        labels = [("expected_goals", "xG", "dec"), ("Shots on Goal", "Tiros a puerta", None),
                  ("Total Shots", "Tiros totales", None), ("Ball Possession", "Posesión", "pct"),
                  ("Corner Kicks", "Córners", None), ("Yellow Cards", "Amarillas", None), ("Red Cards", "Rojas", None)]
        stats_l = [{"label": lab, "home": g(k, "home"), "away": g(k, "away"), **({"fmt": f} if f else {})}
                   for k, lab, f in labels if k in st]
        live_in = {"xg": {"home": float(g("expected_goals", "home")), "away": float(g("expected_goals", "away"))},
                   "corners": {"home": g("Corner Kicks", "home"), "away": g("Corner Kicks", "away")},
                   "cards": {"home": g("Yellow Cards", "home"), "away": g("Yellow Cards", "away")},
                   "red": {"home": g("Red Cards", "home"), "away": g("Red Cards", "away")}}
        return stats_l, live_in

    def _merge_scrapers(self, markets: dict, home: str, away: str, fam: str) -> None:
        for sc in self.scrapers:
            for q in sc.quotes:
                s = (name_score(q["home"], home) + name_score(q["away"], away)) / 2
                if s < 0.7:
                    continue
                mtype = q["mtype"]
                if fam == "football" and mtype == "ml":
                    continue
                if fam != "football" and mtype == "1x2":
                    continue
                key = market_key(mtype, q["line"]) if mtype == "total" else mtype
                if key not in markets:
                    continue  # sólo mercados que también cotiza el sharp o Betway (mismo formato)
                markets[key]["selections"].setdefault(q["selection"], {})[sc.book] = float(q["odds"])


# --------------------------------------------------------------------------------------
# Fábrica
# --------------------------------------------------------------------------------------

def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def build_feed(logos: LogoResolver | None):
    """auto/live → ESPN (real, sin llaves) · oddsapi → The Odds API (requiere llave) · demo → simulador."""
    mode = os.getenv("NURI_MODE", "auto").strip().lower()
    key = os.getenv("ODDS_API_KEY", "").strip()
    if mode == "demo":
        return DemoFeed(logos, speed=float(os.getenv("DEMO_SPEED", "4")))
    if mode != "oddsapi" or not key:
        leagues = [x.strip() for x in os.getenv("ESPN_LEAGUES", ",".join(ESPN_LEAGUES)).split(",") if x.strip()]
        return EspnFeed(leagues)
    sports = [s.strip() for s in os.getenv("ODDS_API_SPORTS", "soccer_mexico_ligamx,soccer_epl,basketball_nba,"
                                                              "americanfootball_nfl,icehockey_nhl").split(",") if s.strip()]
    books = [b.strip() for b in os.getenv("ODDS_API_BOOKMAKERS", "pinnacle,betfair_ex_eu,betway").split(",") if b.strip()]
    mx_keys = {b.strip() for b in os.getenv("ODDS_API_MX_KEYS", "betway").split(",") if b.strip()}
    odds = OddsApiSource(key, sports, books, mx_keys, float(os.getenv("ODDS_API_POLL_SECONDS", "120")),
                         _env_bool("ODDS_API_PROPS"), int(os.getenv("ODDS_API_PROPS_MAX_EVENTS", "3")))
    aps = None
    if os.getenv("API_SPORTS_KEY", "").strip():
        aps = ApiSportsSource(os.getenv("API_SPORTS_KEY").strip(), float(os.getenv("API_SPORTS_POLL_SECONDS", "900")),
                              _env_bool("API_SPORTS_STATS"), int(os.getenv("API_SPORTS_STATS_MAX", "2")))
    scrapers = []
    for book, prefix in (("Caliente.mx", "CALIENTE"), ("Codere", "CODERE"), ("Stake", "STAKE")):
        url = os.getenv(f"{prefix}_FEED_URL", "").strip()
        if url:
            scrapers.append(MXScraper(book, url, os.getenv(f"{prefix}_MODE", "http").strip().lower(),
                                      os.getenv(f"{prefix}_FEED_BODY", "").strip() or None,
                                      float(os.getenv("SCRAPER_POLL_SECONDS", "20"))))
    return LiveFeed(odds, aps, scrapers, logos)


# --------------------------------------------------------------------------------------
# LIVE SIN LLAVES · ESPN: marcadores, reloj, logos oficiales, estadísticas y línea de referencia
# --------------------------------------------------------------------------------------
# ESPN publica, sin registro, el marcador en vivo, los logos oficiales y la línea de mercado
# (DraftKings) de cada partido con su apertura y su valor actual. NURI usa esa línea sólo como
# REFERENCIA de probabilidad real (quitándole el margen con Shin); las apuestas son en casas MX.

from quant_engine import OPEN_BOOK, REF_BOOK, SPORT_SD, fit_two_rates, nb_score_matrix, shin_devig  # noqa: E402
import espn_facts as facts  # noqa: E402
from oddsio import OddsIoSource  # noqa: E402

B365 = "Bet365"  # momios reales de Bet365 que ESPN publica para el fútbol
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_LEAGUES = {
    "mex.1": ("football", "soccer", "Liga MX"),
    "eng.1": ("football", "soccer", "Premier League"),
    "esp.1": ("football", "soccer", "LaLiga"),
    "ita.1": ("football", "soccer", "Serie A"),
    "ger.1": ("football", "soccer", "Bundesliga"),
    "fra.1": ("football", "soccer", "Ligue 1"),
    "uefa.champions": ("football", "soccer", "Champions League"),
    "usa.1": ("football", "soccer", "MLS"),
    "eng.2": ("football", "soccer", "Championship"),
    "sco.1": ("football", "soccer", "Liga de Escocia"),
    "ned.1": ("football", "soccer", "Eredivisie"),
    "bra.1": ("football", "soccer", "Brasileirão"),
    "arg.1": ("football", "soccer", "Liga Argentina"),
    "bel.1": ("football", "soccer", "Liga de Bélgica"),
    "nba": ("basketball", "basketball", "NBA"),
    "mens-college-basketball": ("basketball", "basketball", "NCAA Basketball"),
    "nfl": ("americanfootball", "football", "NFL"),
    "college-football": ("americanfootball", "football", "NCAA Football"),
    "nhl": ("hockey", "hockey", "NHL"),
    "mlb": ("baseball", "baseball", "MLB"),
}
LEAGUE_GOALS = {"mex.1": 2.8, "eng.1": 2.9, "esp.1": 2.6, "ita.1": 2.7, "ger.1": 3.1, "fra.1": 2.8,
                "uefa.champions": 3.0, "usa.1": 3.0, "eng.2": 2.5, "sco.1": 2.8, "ned.1": 3.1, "bra.1": 2.4,
                "arg.1": 2.2, "bel.1": 2.9}
PERIODS = {"basketball": (4, 720.0), "americanfootball": (4, 900.0), "hockey": (3, 1200.0)}
STAT_ES = {
    "possessionPct": ("Posesión", "pct"), "shotsOnTarget": ("Tiros a puerta", None), "totalShots": ("Tiros totales", None),
    "wonCorners": ("Córners", None), "yellowCards": ("Amarillas", None), "redCards": ("Rojas", None),
    "foulsCommitted": ("Faltas", None), "offsides": ("Fueras de lugar", None), "saves": ("Atajadas", None),
    "fieldGoalPct": ("% tiros de campo", "dec"), "threePointFieldGoalPct": ("% triples", "dec"),
    "freeThrowPct": ("% tiros libres", "dec"), "totalRebounds": ("Rebotes", None), "assists": ("Asistencias", None),
    "turnovers": ("Pérdidas", None), "steals": ("Robos", None), "blocks": ("Tapones", None),
    "totalYards": ("Yardas totales", None), "firstDowns": ("Primeros downs", None), "netPassingYards": ("Yardas por pase", None),
    "rushingYards": ("Yardas por tierra", None), "possessionTime": ("Posesión (min)", None),
    "shotsTotal": ("Tiros a gol", None), "hits": ("Golpes", None), "blockedShots": ("Tiros bloqueados", None),
    "powerPlayGoals": ("Goles en power play", None), "faceoffsWon": ("Faceoffs ganados", None),
}


def american_to_decimal(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        s = str(v).strip().upper().replace("−", "-")
        if s in ("EVEN", "EV"):
            return 2.0
        try:
            x = float(s.replace("+", ""))
        except ValueError:
            return None
    if abs(x) < 100:
        return None
    return round(1 + (x / 100 if x > 0 else 100 / abs(x)), 4)


def parse_line(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if s in ("pk", "pick", "even"):
        return 0.0
    s = s.lstrip("ou")
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


def _iso_ts(s: str | None) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _clock_secs(s) -> float:
    try:
        parts = [float(p) for p in str(s).replace("'", "").split(":")]
        return parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0]
    except (TypeError, ValueError):
        return 0.0


def _num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("%", "").strip()
    if "-" in s and not s.startswith("-"):
        s = s.split("-")[0]
    if ":" in s:
        return _clock_secs(s) / 60
    try:
        return float(s)
    except ValueError:
        return None


def parse_espn_odds(comp: dict) -> dict | None:
    """Línea de referencia (DraftKings vía ESPN): moneyline, total y hándicap, apertura y actual."""
    odds = [x for x in (comp.get("odds") or []) if isinstance(x, dict)]
    if not odds:
        return None
    o = odds[0]
    out: dict = {"provider": (o.get("provider") or {}).get("name", "DraftKings")}
    ml = o.get("moneyline") or {}
    for phase in ("close", "open"):
        d = {}
        for side in ("home", "away", "draw"):
            v = american_to_decimal(((ml.get(side) or {}).get(phase) or {}).get("odds"))
            if v:
                d[side] = v
        if d:
            out.setdefault("ml", {})[phase] = d
    if "ml" not in out:  # formato antiguo
        d = {}
        h = american_to_decimal((o.get("homeTeamOdds") or {}).get("moneyLine"))
        a = american_to_decimal((o.get("awayTeamOdds") or {}).get("moneyLine"))
        dr = american_to_decimal((o.get("drawOdds") or {}).get("moneyLine"))
        if h and a:
            d = {"home": h, "away": a}
            if dr:
                d["draw"] = dr
            out["ml"] = {"close": d}
    tot = o.get("total") or {}
    for phase in ("close", "open"):
        ov = (tot.get("over") or {}).get(phase) or {}
        un = (tot.get("under") or {}).get(phase) or {}
        L = parse_line(ov.get("line")) or parse_line(un.get("line"))
        po, pu = american_to_decimal(ov.get("odds")), american_to_decimal(un.get("odds"))
        if L and po and pu:
            out.setdefault("total", {})[phase] = {"line": L, "over": po, "under": pu}
    if "total" not in out and o.get("overUnder"):
        po = american_to_decimal(o.get("overOdds")) or 1.91
        pu = american_to_decimal(o.get("underOdds")) or 1.91
        out["total"] = {"close": {"line": float(o["overUnder"]), "over": po, "under": pu}}
    ps = o.get("pointSpread") or {}
    for phase in ("close", "open"):
        hm = (ps.get("home") or {}).get(phase) or {}
        aw = (ps.get("away") or {}).get(phase) or {}
        L = parse_line(hm.get("line"))
        ph, pa = american_to_decimal(hm.get("odds")), american_to_decimal(aw.get("odds"))
        if L is not None and ph and pa:
            out.setdefault("spread", {})[phase] = {"line": L, "home": ph, "away": pa}
    if "spread" not in out and o.get("spread") is not None:
        sp = abs(float(o["spread"]))
        fav_home = bool((o.get("homeTeamOdds") or {}).get("favorite"))
        L = -sp if fav_home else sp
        ph = american_to_decimal((o.get("homeTeamOdds") or {}).get("spreadOdds")) or 1.91
        pa = american_to_decimal((o.get("awayTeamOdds") or {}).get("spreadOdds")) or 1.91
        out["spread"] = {"close": {"line": L, "home": ph, "away": pa}}
    return out if len(out) > 1 else None


def model_prior_from_line(fam: str, league: str, odds: dict | None) -> dict:
    """Convierte la línea previa en parámetros del modelo (λ de Dixon-Coles, margen y total)."""
    prior: dict = {"from_line": False}
    ml = ((odds or {}).get("ml") or {}).get("close") or {}
    tot = ((odds or {}).get("total") or {}).get("close")
    spr = ((odds or {}).get("spread") or {}).get("close")

    def devig(d: dict, keys: tuple) -> dict | None:
        if not all(k in d for k in keys):
            return None
        try:
            p, _ = shin_devig([d[k] for k in keys])
            return dict(zip(keys, (float(x) for x in p)))
        except ValueError:
            return None

    p_over = None
    if tot:
        pt = devig(tot, ("over", "under"))
        p_over = pt["over"] if pt else None
    if fam == "football":
        g = LEAGUE_GOALS.get(league, 2.75)
        prior.update({"goals": (g / 2 * 1.12, g / 2 * 0.89), "corners": 10.0, "cards": 4.4})
        p3 = devig(ml, ("home", "draw", "away"))
        if p3:
            targets = dict(p3)
            if p_over is not None:
                targets["over"] = p_over
            lam, mu = fit_two_rates(lambda a, b: dc_score_matrix(a, b, -0.06), targets, 0, 0,
                                    tot["line"] if p_over is not None else None, prior["goals"])
            prior.update({"goals": (lam, mu), "from_line": True})
    elif fam in ("basketball", "americanfootball"):
        sd_m, sd_t = SPORT_SD.get(fam, (12.5, 18.0))
        margin = 2.0 if fam == "basketball" else 1.5
        total = 226.0 if fam == "basketball" else 44.0
        if spr:
            ps = devig(spr, ("home", "away"))
            if ps:
                margin = float(sd_m * stats.norm.ppf(min(max(ps["home"], 0.01), 0.99)) - spr["line"])
                prior["from_line"] = True
        elif ml.get("home") and ml.get("away"):
            p2 = devig(ml, ("home", "away"))
            if p2:
                margin = float(sd_m * stats.norm.ppf(min(max(p2["home"], 0.01), 0.99)))
                prior["from_line"] = True
        if tot and p_over is not None:
            total = float(tot["line"] + sd_t * stats.norm.ppf(min(max(p_over, 0.01), 0.99)))
            prior["from_line"] = True
        prior.update({"margin": margin, "total": total, "home_pts": (total + margin) / 2, "away_pts": (total - margin) / 2})
    else:
        is_mlb = fam == "baseball"
        base = (4.6, 4.3) if is_mlb else (3.15, 2.95)
        prior["goals"] = base
        p2 = devig(ml, ("home", "away"))
        if p2:
            mfn = (lambda a, b: nb_score_matrix(a, b)) if is_mlb else (lambda a, b: dc_score_matrix(a, b, 0.0, 14))
            targets = {"home": p2["home"], "away": p2["away"]}
            if p_over is not None:
                targets["over"] = p_over
            lam, mu = fit_two_rates(mfn, targets, 0, 0, tot["line"] if p_over is not None else None, base,
                                    tie_split=0.52 if is_mlb else 0.5)
            prior.update({"goals": (lam, mu), "from_line": True})
    return prior


class EspnFeed:
    """Feed real y sin llaves. Partidos de hoy cada 15 s (si hay en vivo) y de la semana cada 30 min."""

    name = "live"

    def __init__(self, leagues: list[str] | None = None):
        self.leagues = [l for l in (leagues or list(ESPN_LEAGUES)) if l in ESPN_LEAGUES]
        self.raw: dict[str, dict] = {}
        self.league_logo: dict[str, str] = {}
        self.summary: dict[str, dict] = {}
        self.pregame: dict[str, dict] = {}
        self.priors: dict[str, tuple] = {}
        self.last_today: dict[str, float] = {}
        self.last_week: dict[tuple, float] = {}
        self.last_summary: dict[str, float] = {}
        self.injuries: dict[str, dict[str, str]] = {}
        self.last_injuries: dict[str, float] = {}
        self.dossier: dict[str, dict] = {}      # estadísticas reales por partido (espn_facts)
        self.b365: dict[str, dict] = {}         # momios reales de Bet365 (fútbol, pre-partido)
        self.oddsio: OddsIoSource | None = None  # Caliente / Codere MX (si el usuario pegó su clave)
        self._oddsio_task: asyncio.Task | None = None
        self.status = {"mode": "live", "detail": "ESPN"}
        self.src = {"ESPN · marcadores y líneas": {"ok": False, "detail": "conectando…", "last": None}}

    def step(self, now: float | None = None) -> None:
        return None

    def sources(self) -> dict:
        out = dict(self.src)
        now = time.time()
        n_facts = sum(1 for d in self.dossier.values() if now - d.get("updated", 0) < 3 * 3600)
        if n_facts:
            out["Estadísticas reales (ESPN)"] = {"ok": True, "detail": f"{n_facts} partidos con datos", "last": now}
        n_b = sum(1 for q in self.b365.values() if now - q.get("ts", 0) < 3 * 3600 and (q.get("1x2") or q.get("total")))
        out["Bet365 · momios reales"] = {"ok": n_b > 0, "detail": f"{n_b} partidos de fútbol" if n_b else "esperando partidos",
                                         "last": now}
        if self.oddsio:
            st = self.oddsio.source_status()
            out["Caliente / Codere · odds-api.io"] = {"ok": bool(st.get("ok")), "detail": st.get("detail"),
                                                     "last": st.get("last"), "remaining": st.get("remaining")}
        return out

    # ------------------------------------------------------------------ momios de casas MX (odds-api.io)
    def configure_oddsio(self, key: str | None) -> OddsIoSource | None:
        """Activa (o quita) la fuente de momios reales de Caliente / Codere. Se puede llamar en caliente."""
        if self._oddsio_task:
            self._oddsio_task.cancel()
            self._oddsio_task = None
        self.oddsio = OddsIoSource(key) if key else None
        return self.oddsio

    def facts_for(self, eid: str) -> dict | None:
        return facts.public(self.dossier.get(eid))

    async def fetch_result(self, espn_id: str, league_code: str | None) -> dict | None:
        """Marcador oficial de un partido (para calificar picks aunque NURI haya estado cerrado).
        Fútbol: si hubo tiempo extra o penales, devuelve también el marcador de los 90 minutos."""
        if not league_code or league_code not in ESPN_LEAGUES:
            return None
        fam, path, _ = ESPN_LEAGUES[league_code]
        js = await self._summary_json(espn_id, league_code)
        comp = (((js.get("header") or {}).get("competitions")) or [{}])[0] or {}
        st = (comp.get("status") or {}).get("type") or {}
        name = (st.get("name") or "").upper()
        if any(x in name for x in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "FORFEIT", "SUSPENDED")):
            return {"state": "void"}
        if st.get("state") != "post" and not st.get("completed"):
            return {"state": st.get("state") or "pre"}
        sc: dict[str, tuple] = {}
        for c in comp.get("competitors") or []:
            score = facts.f_num(c.get("score") if not isinstance(c.get("score"), dict)
                                else (c["score"].get("value") or c["score"].get("displayValue")))
            lines = [facts.f_num(x.get("displayValue") if isinstance(x, dict) else x) or 0.0
                     for x in c.get("linescores") or []]
            sc[c.get("homeAway")] = (score, lines)
        if "home" not in sc or "away" not in sc or sc["home"][0] is None or sc["away"][0] is None:
            return None
        reg = None
        if fam == "football":
            lh, la = sc["home"][1], sc["away"][1]
            if ("AET" in name or "PEN" in name or len(lh) > 2) and len(lh) >= 2 and len(la) >= 2:
                reg = (int(sum(lh[:2])), int(sum(la[:2])))
        return {"state": "final", "home": int(sc["home"][0]), "away": int(sc["away"][0]), "reg": reg}

    async def _summary_json(self, espn_id: str, league_code: str) -> dict:
        fam, path, _ = ESPN_LEAGUES[league_code]
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENTS[0]}) as client:
            r = await client.get(f"{ESPN_BASE}/{path}/{league_code}/summary", params={"event": espn_id})
            r.raise_for_status()
            return r.json()

    async def fetch_box(self, espn_id: str, league_code: str | None) -> tuple[str | None, dict | None]:
        """Boxscore oficial (para calificar props de jugadores): (estado, resumen completo)."""
        if not league_code or league_code not in ESPN_LEAGUES:
            return None, None
        js = await self._summary_json(espn_id, league_code)
        comp = (((js.get("header") or {}).get("competitions")) or [{}])[0] or {}
        st = (comp.get("status") or {}).get("type") or {}
        name = (st.get("name") or "").upper()
        if any(x in name for x in ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "FORFEIT", "SUSPENDED")):
            return "void", js
        if st.get("state") == "post" or st.get("completed"):
            return "final", js
        return st.get("state") or "pre", js

    # ------------------------------------------------------------------ una sola pasada (versión en la nube)
    async def run_once(self, days: int = 3, summary_hours: float = 36, concurrency: int = 8) -> dict:
        """Baja todo una vez: marcadores de hoy y los próximos días, y el resumen (estadísticas y
        momios de Bet365) de cada partido en vivo o de las próximas horas. Devuelve un conteo."""
        sem = asyncio.Semaphore(concurrency)
        stats = {"ok": 0, "err": 0}

        async def guarded(coro):
            async with sem:
                try:
                    await coro
                    stats["ok"] += 1
                except Exception as exc:
                    stats["err"] += 1
                    print(f"[ESPN] {type(exc).__name__}: {exc}"[:160])

        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENTS[0]},
                                     follow_redirects=True) as client:
            # el "hoy" de ESPN cambia de día más tarde que México: pedimos también las fechas exactas
            offsets = [None] + list(range(days))
            await asyncio.gather(*(guarded(self._fetch(client, lg, d)) for lg in self.leagues for d in offsets))
            now = time.time()
            evs = [e for e in self.raw.values()
                   if e["status"] == "live" or (e["status"] == "pre" and -600 < e["start_ts"] - now < summary_hours * 3600)]
            await asyncio.gather(*(guarded(self._fetch_summary(client, e)) for e in evs))
        n_live = sum(1 for e in self.raw.values() if e["status"] == "live")
        self.src["ESPN · marcadores y líneas"].update(ok=stats["ok"] > 0, last=time.time(),
                                                       detail=f"{len(self.raw)} partidos · {n_live} en vivo")
        stats.update(events=len(self.raw), summaries=len(evs))
        return stats

    # ------------------------------------------------------------------ red
    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENTS[0]},
                                     follow_redirects=True) as client:
            while True:
                now = time.time()
                if self.oddsio and (self._oddsio_task is None or self._oddsio_task.done()):
                    self._oddsio_task = asyncio.create_task(self.oddsio.run(self))
                jobs = []
                for lg in self.leagues:
                    evs = [e for e in self.raw.values() if e["_league"] == lg]
                    hot = any(e["status"] == "live" or (e["status"] == "pre" and e["start_ts"] - now < 1800) for e in evs)
                    if now - self.last_today.get(lg, 0) >= (15 if hot else 180):
                        self.last_today[lg] = now
                        jobs.append(self._fetch(client, lg, None))
                    # ESPN no acepta rangos de fechas: pedimos día por día la próxima semana
                    for d in range(1, 7):
                        if now - self.last_week.get((lg, d), 0) >= 1800 + d * 60:
                            self.last_week[(lg, d)] = now
                            jobs.append(self._fetch(client, lg, d))
                            break  # una petición nueva por liga y vuelta: no saturamos la red
                # Resumen de cada partido: en vivo cada 45 s; antes del partido (≤ 36 h) cada 20 min,
                # y cada 5 min en la última hora y media (alineaciones, bajas de último momento).
                pre_jobs = 0
                upcoming = sorted((e for e in self.raw.values() if e["status"] == "pre"
                                   and -600 < e["start_ts"] - now < 36 * 3600), key=lambda x: x["start_ts"])
                for e in [x for x in self.raw.values() if x["status"] == "live"] + upcoming:
                    eid = e["id"]
                    age = now - self.last_summary.get(eid, 0)
                    if e["status"] == "live":
                        if age >= 45:
                            self.last_summary[eid] = now
                            jobs.append(self._fetch_summary(client, e))
                        continue
                    every = 300 if e["start_ts"] - now < 5400 else 1200
                    if age >= every and pre_jobs < 10:
                        self.last_summary[eid] = now
                        pre_jobs += 1
                        jobs.append(self._fetch_summary(client, e))
                if jobs:
                    res = await asyncio.gather(*jobs, return_exceptions=True)
                    errs = [r for r in res if isinstance(r, Exception)]
                    if len(errs) < len(res):
                        n_live = sum(1 for e in self.raw.values() if e["status"] == "live")
                        self.src["ESPN · marcadores y líneas"].update(
                            ok=True, last=now, detail=f"{len(self.raw)} partidos · {n_live} en vivo")
                    else:
                        err = errs[0]
                        self.src["ESPN · marcadores y líneas"].update(
                            ok=False, last=now, detail=f"sin conexión ({type(err).__name__})")
                for eid in [k for k, e in self.raw.items()
                            if (e["status"] == "final" and now - e["start_ts"] > 8 * 3600) or e["start_ts"] - now > 8 * 86400]:
                    self.raw.pop(eid, None)
                    self.summary.pop(eid, None)
                    self.dossier.pop(eid, None)
                    self.b365.pop(eid, None)
                await asyncio.sleep(5)

    async def _fetch(self, client: httpx.AsyncClient, lg: str, day_offset: int | None) -> None:
        """day_offset None = el marcador "de hoy" de ESPN; un número = esa fecha de México (0 = hoy)."""
        fam, path, title = ESPN_LEAGUES[lg]
        params: dict = {"limit": 300}
        if day_offset is not None:
            d = datetime.now(timezone(timedelta(hours=-6))) + timedelta(days=day_offset)
            params["dates"] = f"{d:%Y%m%d}"
        r = await client.get(f"{ESPN_BASE}/{path}/{lg}/scoreboard", params=params)
        r.raise_for_status()
        data = r.json()
        lgs = data.get("leagues") or []
        if lgs and lgs[0].get("logos"):
            self.league_logo[lg] = lgs[0]["logos"][0].get("href")
        for ev in data.get("events", []):
            try:
                parsed = self._parse(ev, lg, fam, title)
            except Exception as exc:  # un partido raro no debe romper la liga
                print(f"[ESPN] evento ignorado ({lg}): {exc!r}")
                continue
            if parsed:
                self.raw[parsed["id"]] = parsed

    async def _fetch_summary(self, client: httpx.AsyncClient, e: dict) -> None:
        fam, path, _ = ESPN_LEAGUES[e["_league"]]
        r = await client.get(f"{ESPN_BASE}/{path}/{e['_league']}/summary", params={"event": e["_espn_id"]})
        r.raise_for_status()
        js = r.json()
        stats: dict[str, dict] = {}
        for t in ((js.get("boxscore") or {}).get("teams") or []):
            side = t.get("homeAway")
            if side not in ("home", "away"):
                side = "home" if str((t.get("team") or {}).get("id")) == e["_home_id"] else "away"
            for s in t.get("statistics", []) or []:
                name = s.get("name") or s.get("label")
                if not name:
                    continue
                stats.setdefault(name, {"label": s.get("label") or name})[side] = s.get("displayValue")
        self.summary[e["id"]] = {"stats": stats, "ts": time.time()}
        inj = {}
        for t in js.get("injuries") or []:
            for i in t.get("injuries") or []:
                name = (i.get("athlete") or {}).get("displayName")
                st_ = (i.get("status") or "").lower()
                if not name:
                    continue
                inj[name] = ("out" if "out" in st_ else "doubtful" if "doubt" in st_ else
                             "questionable" if ("day" in st_ or "quest" in st_) else "probable" if "prob" in st_ else "active")
        if inj or "injuries" in js:
            self.injuries[e["id"]] = inj
        # Si NURI se abrió con el partido ya empezado, la línea previa viene en "pickcenter".
        if e["id"] not in self.pregame and js.get("pickcenter"):
            pc = parse_espn_odds({"odds": [x for x in js["pickcenter"] if x]})
            if pc:
                self.pregame[e["id"]] = pc
        # Estadísticas reales del partido (récord, forma, promedios, bajas, líderes, H2H…)
        live = e["status"] == "live"
        try:
            d = facts.build_dossier(js, e["sport"], e["league"], e["home"], e["away"], e["_home_id"],
                                    e.get("_away_id", ""), live=live)
            self.dossier[e["id"]] = facts.merge_live(self.dossier.get(e["id"]), d) if live else d
        except Exception as exc:  # un resumen raro no debe tumbar el feed
            print(f"[ESPN] datos del partido {e['id']}: {exc!r}")
        # Momios reales de Bet365 (sólo pre-partido: en vivo ESPN los refresca muy lento)
        if e["sport"] == "football" and not live:
            try:
                b = facts.parse_bet365(js, e["_home_id"], e.get("_away_id", ""))
            except Exception as exc:
                print(f"[ESPN] Bet365 {e['id']}: {exc!r}")
                b = None
            if b:
                self.b365[e["id"]] = b
            elif e["id"] in self.b365 and time.time() - self.b365[e["id"]].get("ts", 0) > 1800:
                self.b365.pop(e["id"], None)

    # ------------------------------------------------------------------ parseo
    def _parse(self, ev: dict, lg: str, fam: str, title: str) -> dict | None:
        comp = (ev.get("competitions") or [{}])[0]
        st = comp.get("status") or ev.get("status") or {}
        tp = st.get("type") or {}
        status = {"pre": "pre", "in": "live", "post": "final"}.get(tp.get("state"), "pre")
        if tp.get("name") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED"):
            return None
        comps = comp.get("competitors") or []
        home = next((c for c in comps if c.get("homeAway") == "home"), None)
        away = next((c for c in comps if c.get("homeAway") == "away"), None)
        if not home or not away:
            return None

        def team(c):
            t = c.get("team") or {}
            logo = t.get("logo") or ((t.get("logos") or [{}])[0].get("href"))
            return {"name": t.get("shortDisplayName") if fam == "football" else t.get("displayName") or t.get("name"),
                    "short": t.get("abbreviation") or short_name(t.get("displayName", "")), "logo": logo}

        eid = f"espn-{ev['id']}"
        period = int(st.get("period") or 0)
        disp = st.get("displayClock") or ""
        short = tp.get("shortDetail") or ""
        minute = None
        if status == "live":
            if fam == "football":
                minute = float(st.get("clock") or 0) / 60.0
                if short.upper() in ("HT", "HALFTIME"):
                    minute = 45.0
                clock = "MT" if short.upper() in ("HT", "HALFTIME") else (short if "'" in short else disp or short)
                frac = min(minute / 93.0, 1.0)
            elif fam in PERIODS:
                n_per, per_len = PERIODS[fam]
                rem = _clock_secs(disp)
                if period > n_per:
                    frac = 0.99
                else:
                    frac = min(((period - 1) * per_len + (per_len - rem)) / (n_per * per_len), 0.99)
                pre = {"basketball": "Q", "americanfootball": "Q", "hockey": "P"}[fam]
                clock = short if "half" in short.lower() or "end" in short.lower() else f"{pre}{period} {disp}"
            else:  # béisbol
                low = short.lower()
                half = 0.5 if any(w in low for w in ("bot", "mid", "end")) else 0.0
                frac = min((max(period, 1) - 1 + half) / 9.0, 0.99)
                clock = short
        else:
            frac = 1.0 if status == "final" else 0.0
            clock = "Final" if status == "final" else ""

        reds = {"home": 0, "away": 0}
        yellows = {"home": 0, "away": 0}
        timeline = []
        hid, aid = str((home.get("team") or {}).get("id")), str((away.get("team") or {}).get("id"))
        for d in comp.get("details") or []:
            side = "home" if str((d.get("team") or {}).get("id")) == hid else ("away" if str((d.get("team") or {}).get("id")) == aid else None)
            who = ", ".join(a.get("displayName", "") for a in (d.get("athletesInvolved") or [])[:1])
            t_txt = ((d.get("type") or {}).get("text") or "").strip()
            clk = (d.get("clock") or {}).get("displayValue", "")
            if d.get("redCard") and side:
                reds[side] += 1
                kind = "red"
            elif d.get("yellowCard") and side:
                yellows[side] += 1
                kind = "yellow"
            elif d.get("scoringPlay"):
                kind = "score"
            else:
                kind = "news"
            label = {"score": "Gol", "red": "Roja", "yellow": "Amarilla"}.get(kind, t_txt)
            timeline.append({"t": clk, "type": kind, "team": side, "text": f"{label}{' · ' + who if who else ''}"})

        odds = parse_espn_odds(comp)
        if odds and status == "pre":
            self.pregame[eid] = odds
        leaders = []
        if fam == "basketball":
            for side, c in (("home", home), ("away", away)):
                for g in c.get("leaders") or []:
                    stat = {"pointsPerGame": "pts", "reboundsPerGame": "reb", "assistsPerGame": "ast"}.get(g.get("name"))
                    top = (g.get("leaders") or [None])[0]
                    if stat and top and top.get("athlete") and top.get("value"):
                        leaders.append({"side": side, "stat": stat, "name": top["athlete"].get("displayName"),
                                        "avg": float(top["value"])})
        score = None
        if status != "pre":
            score = {"home": int(_num(home.get("score")) or 0), "away": int(_num(away.get("score")) or 0)}

        def names(c):
            t = c.get("team") or {}
            return [x for x in (t.get("displayName"), t.get("shortDisplayName"), t.get("name"), t.get("location"))
                    if x]

        return {
            "id": eid, "_espn_id": ev["id"], "_league": lg, "_home_id": hid, "_away_id": aid,
            "_names": {"home": names(home), "away": names(away)}, "_reds": reds, "_yellows": yellows,
            "_leaders": leaders or (self.raw.get(eid) or {}).get("_leaders", []),
            "_odds": odds, "sport": fam, "league": title,
            "home": team(home), "away": team(away), "start_ts": _iso_ts(ev.get("date")),
            "status": status, "clock": clock, "minute": minute, "elapsed_frac": round(frac, 4),
            "score": score, "timeline": timeline[-20:],
        }

    # ------------------------------------------------------------------ salida para el motor
    def _prior(self, e: dict) -> dict:
        odds = self.pregame.get(e["id"]) or e.get("_odds")
        if not odds and e["sport"] == "football" and self.b365.get(e["id"], {}).get("1x2"):
            # sin línea de DraftKings: calibramos el modelo con la de Bet365 (sólo para el modelo; contra
            # Bet365 mismo no habrá ventaja, pero sí sirve para BTTS, goleadores, 1er tiempo…)
            b = self.b365[e["id"]]
            odds = {"ml": {"close": dict(b["1x2"])}}
            if b.get("total") and float(b["total"]["line"]) % 0.5 == 0:
                odds["total"] = {"close": dict(b["total"])}
        key = json.dumps(odds, sort_keys=True, default=str)
        cached = self.priors.get(e["id"])
        if cached and cached[0] == key:
            return cached[1]
        try:
            prior = model_prior_from_line(e["sport"], e["_league"], odds)
        except Exception as exc:
            print(f"[ESPN] prior falló {e['id']}: {exc!r}")
            prior = model_prior_from_line(e["sport"], e["_league"], None)
        self.priors[e["id"]] = (key, prior)
        return prior

    def _markets(self, e: dict, prior: dict) -> dict:
        fam, pre = e["sport"], e["status"] == "pre"
        odds = (self.pregame.get(e["id"]) or e.get("_odds")) if pre else None
        markets: dict[str, dict] = {}

        def put(key, mtype, line, sels: dict | None, open_sels: dict | None = None):
            m = markets.setdefault(key, {"key": key, "type": mtype, "line": line, "selections": {}})
            for s_ in (sels or {}):
                m["selections"].setdefault(s_, {})
            for s_, v in (sels or {}).items():
                if v and v > 1.0:
                    m["selections"][s_][REF_BOOK] = v
            for s_, v in (open_sels or {}).items():
                if v and v > 1.0 and s_ in m["selections"]:
                    m["selections"][s_][OPEN_BOOK] = v

        def virtual(key, mtype, line, sels):
            m = markets.setdefault(key, {"key": key, "type": mtype, "line": line, "selections": {}})
            for s_ in sels:
                m["selections"].setdefault(s_, {})

        line_odds = odds or {}
        ml_c = (line_odds.get("ml") or {}).get("close") or {}
        ml_o = (line_odds.get("ml") or {}).get("open") or {}
        tot = line_odds.get("total") or {}
        spr = line_odds.get("spread") or {}
        ref_tot = (self.pregame.get(e["id"]) or e.get("_odds") or {}).get("total", {}).get("close")
        ref_spr = (self.pregame.get(e["id"]) or e.get("_odds") or {}).get("spread", {}).get("close")

        if fam == "football":
            sel3 = ("home", "draw", "away")
            if all(k in ml_c for k in sel3):
                put("1x2", "1x2", None, {k: ml_c[k] for k in sel3}, {k: ml_o.get(k) for k in sel3})
            virtual("1x2", "1x2", None, sel3)
            lines = {1.5, 2.5, 3.5}
            if tot.get("close"):
                L = tot["close"]["line"]
                put(market_key("total", L), "total", L, {"over": tot["close"]["over"], "under": tot["close"]["under"]},
                    tot.get("open") if (tot.get("open") or {}).get("line") == L else None)
            if ref_tot:
                lines.add(ref_tot["line"])
            for L in sorted(lines):
                virtual(market_key("total", L), "total", L, ("over", "under"))
            if spr.get("close"):
                L = spr["close"]["line"]
                put(market_key("ah", L), "ah", L, {"home": spr["close"]["home"], "away": spr["close"]["away"]},
                    spr.get("open") if (spr.get("open") or {}).get("line") == L else None)
            elif ref_spr:
                virtual(market_key("ah", ref_spr["line"]), "ah", ref_spr["line"], ("home", "away"))
            virtual("btts", "btts", None, ("yes", "no"))
            minute = e.get("minute") or 0
            if pre or (e["status"] == "live" and minute < 40):
                for L in (0.5, 1.5):
                    virtual(market_key("total_1h", L), "total_1h", L, ("over", "under"))
        else:
            if ml_c.get("home") and ml_c.get("away"):
                put("ml", "ml", None, {"home": ml_c["home"], "away": ml_c["away"]},
                    {"home": ml_o.get("home"), "away": ml_o.get("away")})
            virtual("ml", "ml", None, ("home", "away"))
            if tot.get("close"):
                L = tot["close"]["line"]
                put(market_key("total", L), "total", L, {"over": tot["close"]["over"], "under": tot["close"]["under"]},
                    tot.get("open") if (tot.get("open") or {}).get("line") == L else None)
            elif ref_tot:
                virtual(market_key("total", ref_tot["line"]), "total", ref_tot["line"], ("over", "under"))
            if spr.get("close"):
                L = spr["close"]["line"]
                put(market_key("spread", L), "spread", L, {"home": spr["close"]["home"], "away": spr["close"]["away"]},
                    spr.get("open") if (spr.get("open") or {}).get("line") == L else None)
            elif ref_spr:
                virtual(market_key("spread", ref_spr["line"]), "spread", ref_spr["line"], ("home", "away"))
            if fam == "hockey":
                for L in (5.5, 6.5):
                    virtual(market_key("total", L), "total", L, ("over", "under"))
                g = prior.get("goals", (3.15, 2.95))
                pl = -1.5 if g[0] >= g[1] else 1.5
                virtual(market_key("spread", pl), "spread", pl, ("home", "away"))
            if fam == "baseball" and not ref_spr:
                g = prior.get("goals", (4.6, 4.3))
                rl = -1.5 if g[0] >= g[1] else 1.5
                virtual(market_key("spread", rl), "spread", rl, ("home", "away"))
        self._real_quotes(e, markets, virtual)
        return markets

    def _real_quotes(self, e: dict, markets: dict, virtual) -> None:
        """Pega en cada mercado los momios REALES de casas que operan en México:
        Bet365 (vía ESPN, fútbol pre-partido) y Caliente / Codere MX (vía odds-api.io, si hay clave)."""
        fam, live = e["sport"], e["status"] == "live"
        b = self.b365.get(e["id"]) if (fam == "football" and not live) else None
        if b and time.time() - b.get("ts", 0) < 3 * 3600:
            if b.get("1x2") and "1x2" in markets:
                for s_, o in b["1x2"].items():
                    if s_ in markets["1x2"]["selections"]:
                        markets["1x2"]["selections"][s_][B365] = o
            if b.get("dc") and "1x2" in markets:
                virtual("dc", "dc", None, ("1x", "x2", "12"))
                for s_, o in b["dc"].items():
                    markets["dc"]["selections"][s_][B365] = o
            L = (b.get("total") or {}).get("line")
            # el total de Bet365 sólo se compara si el modelo tiene un total de mercado calibrado
            # (DraftKings, o la propia línea de Bet365 cuando es .5); con línea asiática sin
            # referencia, la "ventaja" saldría del promedio de la liga y no sería real.
            dk_total = bool(((self.pregame.get(e["id"]) or e.get("_odds") or {}).get("total") or {}).get("close"))
            if L and (dk_total or float(L) % 0.5 == 0):
                k = market_key("total", L)
                virtual(k, "total", L, ("over", "under"))
                markets[k]["selections"]["over"][B365] = b["total"]["over"]
                markets[k]["selections"]["under"][B365] = b["total"]["under"]
        q = self.oddsio.quotes_for(e["id"], live=live) if self.oddsio else None
        if not q:
            return
        for book, mks in q["books"].items():
            # de cada tipo de línea sólo las 3 más parejas (las que la casa pone como principales)
            lines: dict[str, list] = {}
            for k, sels in mks.items():
                t = sels.get("_type")
                if t:
                    vals = [v for s_, v in sels.items() if not s_.startswith("_")]
                    if len(vals) == 2:
                        lines.setdefault(t, []).append((abs(1 / vals[0] - 1 / vals[1]), k))
            keep = {k for t, xs in lines.items() for _, k in sorted(xs)[:3]}
            for k, sels in mks.items():
                if k in ("1x2", "ml", "btts"):
                    mtype, line = k, None
                    if (mtype == "1x2") != (fam == "football") and mtype != "btts":
                        continue
                elif k in keep:
                    mtype, line = sels["_type"], sels["_line"]
                else:
                    continue
                names = tuple(s_ for s_ in sels if not s_.startswith("_"))
                if k not in markets:
                    if live and mtype != "total" and mtype not in ("ml", "1x2"):
                        continue  # en vivo sólo líneas que el modelo ya sigue de cerca
                    virtual(k, mtype, line, names)
                for s_ in names:
                    if s_ in markets[k]["selections"]:
                        markets[k]["selections"][s_][book] = sels[s_]
            if q.get("urls", {}).get(book):
                e.setdefault("_book_urls", {})[book] = q["urls"][book]

    # ------------------------------------------------------------------ props con datos reales
    def _soccer_scorers(self, e: dict, prior: dict) -> tuple[list[dict], dict]:
        """Goleador en cualquier momento: tasa de gol del jugador en la temporada (con contracción
        hacia 0.30 por partido), ajustada a los goles que el modelo espera de su equipo HOY, y el
        momio real de Bet365 cuando ESPN lo publica."""
        d = self.dossier.get(e["id"])
        if not d:
            return [], {}
        lam = prior.get("goals") or (1.4, 1.1)
        season = d.get("_season_raw") or {}
        gp = d.get("_gp") or {}
        status = d.get("_inj_status") or {}
        starters = {s: {facts.norm_person(n) for n in (d.get("_starters") or {}).get(s, [])} for s in ("home", "away")}
        b = self.b365.get(e["id"]) or {}
        players, markets = [], {}
        for idx, side in enumerate(("home", "away")):
            tg = facts.f_num((season.get(side) or {}).get("totalGoals"))
            team_rate = (tg / gp[side]) if (tg and gp.get(side)) else None
            for pl in ((d.get("_leaders_raw") or {}).get(side, {}).get("goalsLeaders") or [])[:3]:
                goals, apps = pl.get("goals") or 0, pl.get("apps") or 0
                if goals < 3 or apps < 4:
                    continue
                if status.get(pl["name"]) in ("out", "doubtful"):
                    continue
                if starters[side] and facts.norm_person(pl["name"]) not in starters[side]:
                    continue  # ya salió la alineación y no es titular
                rate = (goals + 0.30 * 6) / (apps + 6)
                adj = min(max(lam[idx] / team_rate, 0.6), 1.6) if team_rate else 1.0
                mean = min(rate * adj, 1.25)
                odds = facts.find_scorer_odds(b.get("scorers") or {}, pl["name"])
                key = market_key("prop", 0.5, pl["name"], "goals")
                markets[key] = {"key": key, "type": "prop", "line": 0.5, "player": pl["name"], "stat": "goals",
                                "selections": {"over": ({B365: odds} if odds else {})},
                                "avg": goals / max(apps, 1), "season": {"goals": int(goals), "apps": int(apps)},
                                "proj": round(mean, 3)}
                players.append({"name": pl["name"], "team": side, "usage": 0.2, "status": "active",
                                "stats": {"goals": {"mean": round(mean, 3), "sd": None, "dist": "poisson",
                                                    "current": 0}},
                                "season_avg": {"goals": round(goals / max(apps, 1), 2)}})
        return players, markets

    NFL_PROPS = (("passingYards", "pass_yds", 215.0, "passingYardsPerGameAllowed", (0.22, 30.0), 150.0),
                 ("rushingYards", "rush_yds", 115.0, "rushingYardsPerGameAllowed", (0.38, 12.0), 40.0),
                 ("receivingYards", "rec_yds", 215.0, "passingYardsPerGameAllowed", (0.45, 10.0), 40.0))

    def _nfl_props(self, e: dict, prior: dict) -> tuple[list[dict], dict]:
        """Yardas del QB, del corredor y del receptor líderes: promedio real de la temporada,
        ajustado por lo que permite la defensa rival y por los puntos que la línea espera."""
        d = self.dossier.get(e["id"])
        if not d:
            return [], {}
        season = d.get("_season_raw") or {}
        gp = d.get("_gp") or {}
        status = d.get("_inj_status") or {}
        players, markets = [], {}
        for side, opp, pts_key in (("home", "away", "home_pts"), ("away", "home", "away_pts")):
            g = gp.get(side)
            if not g:
                continue
            ppg = facts.f_num((season.get(side) or {}).get("totalPointsPerGame"))
            implied = prior.get(pts_key)
            adj_pts = min(max((implied / ppg) ** 0.35, 0.9), 1.1) if (implied and ppg) else 1.0
            for cat, stat, lg_avg, allowed_key, (a, b0), min_avg in self.NFL_PROPS:
                lst = ((d.get("_leaders_raw") or {}).get(side) or {}).get(cat) or []
                if not lst or lst[0].get("value") is None:
                    continue
                pl = lst[0]
                if status.get(pl["name"]) in ("out", "doubtful"):
                    continue
                avg = pl["value"] / g
                if avg < min_avg:
                    continue
                allowed = facts.f_num((season.get(opp) or {}).get(allowed_key))
                adj = min(max((allowed / lg_avg) ** 0.6, 0.85), 1.15) if allowed else 1.0
                mean = avg * adj * adj_pts
                fl = math.floor(avg)
                line = fl + 0.5 if avg - fl >= 0.5 else fl - 0.5
                key = market_key("prop", line, pl["name"], stat)
                markets[key] = {"key": key, "type": "prop", "line": line, "player": pl["name"], "stat": stat,
                                "selections": {"over": {}, "under": {}}, "avg": avg,
                                "opp_allowed": allowed, "proj": round(mean, 1)}
                players.append({"name": pl["name"], "team": side, "usage": 0.25,
                                "status": status.get(pl["name"], "active"),
                                "stats": {stat: {"mean": round(mean, 1), "sd": round(a * mean + b0, 1),
                                                 "dist": "normal", "current": 0}},
                                "season_avg": {stat: round(avg, 1)}})
        return players, markets

    def _facts_brief(self, e: dict) -> dict | None:
        d = self.dossier.get(e["id"])
        if not d:
            return None
        def form(s):
            return "".join(g["r"] for g in (d[s].get("form") or [])[-5:])
        return {"bullets": d.get("bullets") or [], "form": {"home": form("home"), "away": form("away")},
                "record": {"home": d["home"].get("record"), "away": d["away"].get("record")},
                "rank": {"home": d["home"].get("rank"), "away": d["away"].get("rank")},
                "predictor": d.get("predictor"), "has_b365": e["id"] in self.b365}

    NBA_AVG_TOTAL = 229.0
    PROP_SD = {"pts": (0.27, 2.8), "reb": (0.30, 1.4), "ast": (0.33, 1.1)}

    def _nba_props(self, e: dict) -> tuple[list[dict], dict]:
        """Props de los líderes de cada equipo: promedio de temporada ajustado por el ritmo del
        partido (total de la línea), riesgo de paliza (hándicap) y bajas confirmadas."""
        odds = self.pregame.get(e["id"]) or e.get("_odds") or {}
        tot = ((odds.get("total") or {}).get("close") or {}).get("line")
        spr = ((odds.get("spread") or {}).get("close") or {}).get("line")
        pace = (tot / self.NBA_AVG_TOTAL) if tot else 1.0
        inj = self.injuries.get(e["id"], {})
        players: dict[str, dict] = {}
        for ld in e["_leaders"]:
            p = players.setdefault(ld["name"], {"name": ld["name"], "team": ld["side"], "usage": 0.22,
                                                "status": inj.get(ld["name"], "active"), "stats": {}, "_avg": {}})
            if ld["stat"] == "pts":
                p["usage"] = 0.30
            mult = pace ** (0.9 if ld["stat"] == "pts" else 0.5)
            if spr is not None and abs(spr) >= 10:  # paliza probable: los titulares juegan menos
                fav_home = spr < 0
                mult *= 0.96 if (ld["side"] == "home") == fav_home else 0.98
            a, b = self.PROP_SD[ld["stat"]]
            mean = ld["avg"] * mult
            p["stats"][ld["stat"]] = {"mean": round(mean, 2), "sd": round(a * mean + b, 2), "dist": "normal", "current": 0}
            p["_avg"][ld["stat"]] = ld["avg"]
        markets = {}
        for p in players.values():
            if p["status"] in ("out", "doubtful"):
                continue
            for stat, avg in p["_avg"].items():
                fl = math.floor(avg)
                line = fl + 0.5 if avg - fl >= 0.5 else fl - 0.5
                if line <= 0:
                    continue
                key = market_key("prop", line, p["name"], stat)
                markets[key] = {"key": key, "type": "prop", "line": line, "player": p["name"], "stat": stat,
                                "selections": {"over": {}, "under": {}}, "avg": avg}
        out = []
        for p in players.values():
            q = {k: v for k, v in p.items() if k != "_avg"}
            q["season_avg"] = p["_avg"]
            out.append(q)
        return out, markets

    def _stats_list(self, e: dict) -> list[dict]:
        summ = (self.summary.get(e["id"]) or {}).get("stats") or {}
        out = []
        for name, d in summ.items():
            if name not in STAT_ES:
                continue
            label, fmt = STAT_ES[name]
            h, a = _num(d.get("home")), _num(d.get("away"))
            if h is None or a is None:
                continue
            item = {"label": label, "home": round(h, 1) if fmt == "dec" else int(round(h)) if fmt != "dec" else h,
                    "away": round(a, 1) if fmt == "dec" else int(round(a))}
            if fmt:
                item["fmt"] = fmt
            out.append(item)
        order = list(STAT_ES)
        out.sort(key=lambda x: next((i for i, k in enumerate(order) if STAT_ES[k][0] == x["label"]), 99))
        return out[:10]

    def _live_inputs(self, e: dict) -> dict:
        if e["sport"] != "football":
            return {}
        st = (self.summary.get(e["id"]) or {}).get("stats") or {}

        def g(name, side):
            return _num((st.get(name) or {}).get(side)) or 0.0

        xg = {}
        for side in ("home", "away"):
            shots, sot = g("totalShots", side), g("shotsOnTarget", side)
            xg[side] = round(0.30 * sot + 0.06 * max(shots - sot, 0), 2)   # xG estimado por tiros
        return {"xg": xg, "corners": {s: g("wonCorners", s) for s in ("home", "away")},
                "cards": {s: g("yellowCards", s) or e["_yellows"][s] for s in ("home", "away")},
                "red": {s: int(max(g("redCards", s), e["_reds"][s])) for s in ("home", "away")}}

    def events(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        out = []
        for e in list(self.raw.values()):
            if e["status"] == "final" and now - e["start_ts"] > 5 * 3600:
                continue
            prior = self._prior(e)
            ev = {k: v for k, v in e.items() if not k.startswith("_")}
            ev["league_logo"] = self.league_logo.get(e["_league"])
            ev["stats"] = self._stats_list(e) if e["status"] != "pre" else []
            ev["live"] = self._live_inputs(e)
            ev["model_prior"] = prior
            ev["markets"] = self._markets(e, prior) if e["status"] != "final" else {}
            ev["players"] = []
            if e["status"] == "pre" and e["start_ts"] - now < 36 * 3600:
                props_fn = {"basketball": None, "football": self._soccer_scorers,
                            "americanfootball": self._nfl_props}.get(e["sport"])
                try:
                    if e["sport"] == "basketball" and e.get("_leaders"):
                        ev["players"], props = self._nba_props(e)
                    elif props_fn:
                        ev["players"], props = props_fn(e, prior)
                    else:
                        props = {}
                    ev["markets"].update(props)
                except Exception as exc:  # un dato raro no debe tumbar el partido
                    print(f"[ESPN] props {e['id']}: {exc!r}")
            ev["facts"] = self._facts_brief(e)
            if e.get("_book_urls"):
                ev["book_urls"] = e["_book_urls"]
            ev["source"] = "espn"
            out.append(ev)
        out.sort(key=lambda x: ({"live": 0, "pre": 1, "final": 2}[x["status"]], x["start_ts"]))
        return out
