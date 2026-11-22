"""
NURI · 누리 — oddsio.py
=======================
Momios REALES de Caliente y Codere MX (y de cualquier casa mexicana que elijas) vía
odds-api.io, el único servicio autorizado que hoy publica esas dos casas en una API.

    · Necesita una clave propia (plan Solo: 2 casas a elegir, 5,000 consultas por hora,
      pre-partido y en vivo). La clave se pega una vez en NURI (botón «Momios reales»).
    · NURI no entra a las páginas de las casas: sólo lee la API de odds-api.io.

Flujo:
    1. /bookmakers/selected      → qué casas tiene activas tu plan
    2. /events?sport=…           → partidos de las próximas 48 h (y en vivo)
    3. empata cada partido con el de ESPN (nombres + hora)
    4. /odds/multi (10 por llamada) → momios: en vivo cada 8 s, pre-partido cada 2 min
"""
from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import httpx

BASE = "https://api.odds-api.io/v3"
SPORTS = {"football": "football", "basketball": "basketball", "americanfootball": "american-football",
          "hockey": "ice-hockey", "baseball": "baseball"}
# nombre en odds-api.io → nombre que usa NURI (y el motor) para la casa
BOOKS = {"Caliente": "Caliente.mx", "Codere MX": "Codere", "Bet365": "Bet365", "Stake": "Stake",
         "Betway": "Betway", "Novibet": "Novibet", "Betano MX": "Betano", "Winpot MX": "Winpot",
         "Strendus": "Strendus", "Playdoit": "Playdoit", "RushBet MX": "RushBet", "BetMexico": "BetMexico",
         "Betmaster MX": "Betmaster", "MexBoss": "MexBoss"}
DEFAULT_BOOKS = ("Caliente", "Codere MX")
MX_CHOICES = ("Caliente", "Codere MX", "Bet365", "Stake", "Betway", "Novibet", "Betano MX", "Winpot MX",
              "Strendus", "Playdoit", "RushBet MX")

ML_NAMES = {"ml", "1x2", "moneyline", "match winner", "full time result", "winner", "money line", "h2h"}
SPREAD_NAMES = {"spread", "handicap", "asian handicap", "point spread", "puck line", "run line", "spreads"}
TOTAL_NAMES = {"totals", "total", "over/under", "goals over/under", "total points", "total goals", "over under"}
BTTS_NAMES = {"both teams to score", "btts", "both teams score"}
SKIP_WORDS = ("half", "1st", "2nd", "3rd", "4th", "quarter", "period", " ht", "ht ", "team total", "corner",
              "card", "player", "inning", "booking", "first", "second")

TEAM_STOP = {"fc", "cf", "club", "ac", "sc", "cd", "ud", "sd", "afc", "cfc", "fk", "sk", "if", "the", "de",
             "del", "la", "el", "los", "las", "ssc", "as", "rc", "calcio", "bk"}


def _ascii(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_team(s: str) -> str:
    s = _ascii(s).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    toks = [t for t in s.split() if t not in TEAM_STOP]
    return " ".join(toks) or s.strip()


def team_sim(a: str, b: str) -> float:
    """Parecido entre dos nombres de equipo ya normalizados (0–1)."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    inter = ta & tb
    jac = len(inter) / max(len(ta | tb), 1)
    # 'america' ⊂ 'club america', 'man united' ~ 'manchester united'
    contain = 0.92 if inter and (ta <= tb or tb <= ta) else 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    return max(jac, contain, seq * 0.95)


def _odd(v) -> float | None:
    try:
        x = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return round(x, 3) if 1.0 < x < 1000 else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(s) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _mkey(mtype: str, line: float | None = None) -> str:
    return mtype if line is None else f"{mtype}:{line:g}"


def parse_markets(fam: str, bookmakers: dict) -> dict:
    """{'Caliente': [mercados…]} → {'Caliente.mx': {'1x2': {...}, 'total:2.5': {...}}}"""
    out: dict[str, dict] = {}
    for bname, markets in (bookmakers or {}).items():
        disp = BOOKS.get(bname, bname)
        q: dict[str, dict] = {}
        for m in markets or []:
            if not isinstance(m, dict):
                continue
            nm = f" {(m.get('name') or '').strip().lower()} "
            if any(w in nm for w in SKIP_WORDS):
                continue
            nm = nm.strip()
            rows = [r for r in (m.get("odds") or []) if isinstance(r, dict)]
            if not rows:
                continue
            if nm in ML_NAMES:
                r0 = rows[0]
                h, d, a = _odd(r0.get("home")), _odd(r0.get("draw")), _odd(r0.get("away"))
                if fam == "football":
                    if h and d and a and 0.98 < 1 / h + 1 / d + 1 / a < 1.3:
                        q["1x2"] = {"home": h, "draw": d, "away": a}
                elif h and a and 0.98 < 1 / h + 1 / a < 1.25:
                    q["ml"] = {"home": h, "away": a}
            elif nm in SPREAD_NAMES:
                mt = "ah" if fam == "football" else "spread"
                for r0 in rows:
                    L = _num(r0.get("hdp"))
                    h, a = _odd(r0.get("home")), _odd(r0.get("away"))
                    if L is None or not (h and a) or not (0.98 < 1 / h + 1 / a < 1.25):
                        continue
                    q[_mkey(mt, L)] = {"home": h, "away": a, "_line": L, "_type": mt}
            elif nm in TOTAL_NAMES:
                for r0 in rows:
                    L = _num(r0.get("hdp"))
                    o, u = _odd(r0.get("over")), _odd(r0.get("under"))
                    if not L or not (o and u) or not (0.98 < 1 / o + 1 / u < 1.25):
                        continue
                    q[_mkey("total", L)] = {"over": o, "under": u, "_line": L, "_type": "total"}
            elif nm in BTTS_NAMES and fam == "football":
                r0 = rows[0]
                y, n = _odd(r0.get("yes")), _odd(r0.get("no"))
                if y and n and 0.98 < 1 / y + 1 / n < 1.25:
                    q["btts"] = {"yes": y, "no": n}
        if q:
            out[disp] = q
    return out


class OddsIoSource:
    """Cliente de odds-api.io. Corre en segundo plano dentro del feed de ESPN."""

    def __init__(self, key: str):
        self.key = key.strip()
        self.books: list[str] = []          # nombres de odds-api.io
        self.status = {"ok": False, "detail": "conectando…", "remaining": None, "error": None, "last": None}
        self.events: dict[str, dict] = {}   # id odds-api → evento
        self.match: dict[str, str] = {}     # id NURI (espn-…) → id odds-api
        self.rev: dict[str, str] = {}
        self.quotes: dict[str, dict] = {}   # id NURI → {"ts", "books": {...}, "urls": {...}}
        self.last_books = 0.0
        self.last_events: dict[str, float] = {}
        self.last_odds: dict[str, float] = {}
        self.backoff_until = 0.0
        self.calls_hour: list[float] = []

    # ------------------------------------------------------------------ http
    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None, method: str = "GET"):
        params = dict(params or {})
        params["apiKey"] = self.key
        r = await client.request(method, BASE + path, params=params)
        self.calls_hour = [t for t in self.calls_hour if time.time() - t < 3600] + [time.time()]
        rem = r.headers.get("x-ratelimit-remaining")
        if rem is not None:
            try:
                self.status["remaining"] = int(float(rem))
            except ValueError:
                pass
        if r.status_code == 401:
            self.status.update(ok=False, error="clave", detail="la clave no es válida o se canceló el plan")
            self.backoff_until = time.time() + 600
            raise RuntimeError("401")
        if r.status_code == 403:
            self.status.update(ok=False, error="plan", detail="tu plan no incluye esa casa o ese dato")
            raise RuntimeError("403")
        if r.status_code == 429:
            wait = _num(r.headers.get("Retry-After")) or 60
            self.backoff_until = time.time() + min(wait, 900)
            self.status.update(detail=f"límite de consultas: espera {int(wait)} s")
            raise RuntimeError("429")
        r.raise_for_status()
        return r.json()

    async def check(self) -> dict:
        """Valida la clave al guardarla. Devuelve {ok, books, detail}."""
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                sel = await self._get(client, "/bookmakers/selected")
            except RuntimeError:
                return {"ok": False, "books": [], "detail": self.status.get("detail")}
            except Exception as exc:  # sin internet, etc.
                return {"ok": False, "books": [], "detail": f"sin conexión con odds-api.io ({type(exc).__name__})"}
        self.books = self._book_list(sel)
        self.last_books = time.time()
        return {"ok": True, "books": self.books,
                "detail": ("casas activas: " + ", ".join(self.books)) if self.books else "clave válida; elige tus casas"}

    async def select_books(self, books: list[str]) -> dict:
        names = [b for b in books if b in MX_CHOICES]
        if not names:
            return {"ok": False, "detail": "elige al menos una casa"}
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                await self._get(client, "/bookmakers/selected/select", {"bookmakers": ",".join(names)}, method="PUT")
                sel = await self._get(client, "/bookmakers/selected")
            except RuntimeError:
                return {"ok": False, "detail": self.status.get("detail")}
            except Exception as exc:
                return {"ok": False, "detail": f"no se pudo guardar ({type(exc).__name__})"}
        self.books = self._book_list(sel)
        self.last_books = time.time()
        return {"ok": True, "books": self.books}

    @staticmethod
    def _book_list(sel) -> list[str]:
        if isinstance(sel, dict):
            sel = sel.get("bookmakers") or sel.get("selected") or sel.get("data") or []
        out = []
        for b in sel or []:
            name = b.get("name") if isinstance(b, dict) else b
            if name and name not in out:
                out.append(str(name))
        return out

    # ------------------------------------------------------------------ ciclo
    async def run(self, feed) -> None:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            while True:
                try:
                    await self._cycle(client, feed)
                except asyncio.CancelledError:
                    raise
                except RuntimeError:
                    pass
                except Exception as exc:
                    self.status.update(ok=False, detail=f"sin conexión ({type(exc).__name__})")
                await asyncio.sleep(2.0)

    async def _cycle(self, client: httpx.AsyncClient, feed) -> None:
        now = time.time()
        if now < self.backoff_until:
            return
        if now - self.last_books > 900 or not self.books:
            if now - self.last_books > 60 or not self.last_books:
                self.last_books = now
                self.books = self._book_list(await self._get(client, "/bookmakers/selected"))
            if not self.books:
                self.status.update(ok=False, error="casas", detail="tu plan aún no tiene casas elegidas")
                return
        raw = list(feed.raw.values())
        sports_live = {e["sport"] for e in raw if e["status"] == "live"}
        sports = {e["sport"] for e in raw if e["status"] != "final" and e["sport"] in SPORTS}
        # 1) catálogo de partidos por deporte
        for fam in sports:
            every = 120 if fam in sports_live else 420
            if now - self.last_events.get(fam, 0) < every:
                continue
            self.last_events[fam] = now
            t0 = datetime.now(timezone.utc) - timedelta(hours=4)
            t1 = datetime.now(timezone.utc) + timedelta(hours=48)
            data = await self._get(client, "/events", {
                "sport": SPORTS[fam], "status": "pending,live", "limit": 5000,
                "from": t0.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": t1.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "bookmaker": self.books[0]})
            items = data if isinstance(data, list) else (data or {}).get("data") or []
            for x in items:
                if not isinstance(x, dict) or x.get("id") is None:
                    continue
                self.events[str(x["id"])] = {"id": str(x["id"]), "fam": fam, "ts": _ts(x.get("date")),
                                             "status": x.get("status"), "home": x.get("home") or "",
                                             "away": x.get("away") or "", "nh": norm_team(x.get("home") or ""),
                                             "na": norm_team(x.get("away") or ""),
                                             "league": ((x.get("league") or {}).get("name") or "")}
            self._match(raw)
        # 2) momios: en vivo cada 8 s, pre-partido (≤ 36 h) cada 2 min
        due_live, due_pre = [], []
        for e in raw:
            io = self.match.get(e["id"])
            if not io or e["status"] == "final":
                continue
            last = self.last_odds.get(io, 0)
            if e["status"] == "live" and now - last >= 8:
                due_live.append(io)
            elif e["status"] == "pre" and e["start_ts"] - now < 36 * 3600 and now - last >= 120:
                due_pre.append(io)
        batch = (due_live + due_pre)[:60]  # ≤ 6 llamadas por vuelta
        for i in range(0, len(batch), 10):
            ids = batch[i:i + 10]
            for io in ids:
                self.last_odds[io] = now
            data = await self._get(client, "/odds/multi", {"eventIds": ",".join(ids),
                                                           "bookmakers": ",".join(self.books)})
            for ev in (data if isinstance(data, list) else [data]):
                if not isinstance(ev, dict):
                    continue
                eid = self.rev.get(str(ev.get("id")))
                if not eid:
                    continue
                fam = self.events.get(str(ev.get("id")), {}).get("fam") or "football"
                books = parse_markets(fam, ev.get("bookmakers") or {})
                self.quotes[eid] = {"ts": time.time(), "books": books,
                                    "urls": {BOOKS.get(k, k): v for k, v in (ev.get("urls") or {}).items()}}
        n_q = sum(1 for q in self.quotes.values() if time.time() - q["ts"] < 900 and q["books"])
        names = ", ".join(self.books)
        self.status.update(ok=True, error=None, last=time.time(),
                           detail=f"{names} · {n_q} partidos con momio" if n_q else f"{names} · buscando partidos")

    def _match(self, raw: list[dict]) -> None:
        """Empata partidos de ESPN con los de odds-api.io (mismo deporte, ±3 h, ambos nombres)."""
        by_fam: dict[str, list[dict]] = {}
        for x in self.events.values():
            by_fam.setdefault(x["fam"], []).append(x)
        for e in raw:
            if e["status"] == "final" or e["id"] in self.match:
                continue
            names = e.get("_names") or {"home": [e["home"]["name"]], "away": [e["away"]["name"]]}
            nh = [norm_team(n) for n in names["home"] if n]
            na = [norm_team(n) for n in names["away"] if n]
            best, best_s = None, 0.0
            for x in by_fam.get(e["sport"], []):
                dt = abs(x["ts"] - e["start_ts"])
                if dt > 3 * 3600:
                    continue
                sh = max((team_sim(n, x["nh"]) for n in nh), default=0)
                sa = max((team_sim(n, x["na"]) for n in na), default=0)
                if min(sh, sa) < 0.62:
                    continue
                s = sh + sa - dt / (3 * 3600) * 0.3
                if s > best_s:
                    best, best_s = x, s
            if best and best["id"] not in self.rev:
                self.match[e["id"]] = best["id"]
                self.rev[best["id"]] = e["id"]

    # ------------------------------------------------------------------ salida
    def quotes_for(self, eid: str, live: bool) -> dict | None:
        q = self.quotes.get(eid)
        if not q:
            return None
        if time.time() - q["ts"] > (25 if live else 900):
            return None  # momio viejo: mejor no mostrarlo
        return q

    def source_status(self) -> dict:
        s = dict(self.status)
        s["books"] = [BOOKS.get(b, b) for b in self.books]
        s["calls_last_hour"] = len(self.calls_hour)
        return s
