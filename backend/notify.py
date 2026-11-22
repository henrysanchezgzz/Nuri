"""
NURI · 누리 — notify.py
=======================
Manda los picks a tu celular con ntfy (https://ntfy.sh): app gratis para iPhone y Android, sin cuenta.
En el cel te suscribes a tu "tema" (una clave larga que sólo tú conoces) y NURI publica ahí:

    · cada pick nuevo, en cuanto se confirma
    · el resultado de cada pick cuando termina el partido
    · el resumen de la semana (lunes en la mañana)

Si no hay internet, los mensajes esperan en una cola y se reintentan.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime

import httpx

SERVER = "https://ntfy.sh"
DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
RES_TXT = {"won": "Ganó", "half_won": "Medio ganado", "lost": "Perdió", "half_lost": "Medio perdido",
           "push": "Devuelta", "void": "Anulada"}


def _when(ts: float, tz) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return f"{DIAS[d.weekday()]} {d.day} · {d:%H:%M}"


class Notifier:
    def __init__(self, topic: str, server: str = SERVER):
        self.topic = topic.strip()
        self.server = server.rstrip("/")
        self._q: list[dict] = []
        self._lock = threading.Lock()
        self.sent = 0
        self.last_error: str | None = None
        self.last_ok: float | None = None
        self.click: str | None = None   # al tocar el aviso se abre esta dirección (la app en la nube)

    # ------------------------------------------------------------------ cola (se puede llamar desde hilos)
    def queue(self, title: str, message: str, tags: list[str] | None = None, priority: int = 3,
              click: str | None = None) -> None:
        msg = {"topic": self.topic, "title": title, "message": message, "priority": priority}
        if tags:
            msg["tags"] = tags
        if click or self.click:
            msg["click"] = click or self.click
        with self._lock:
            self._q.append({"msg": msg, "tries": 0, "next": 0.0})

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                await self.flush(client)
                await asyncio.sleep(2.0)

    async def flush(self, client: httpx.AsyncClient) -> None:
        now = time.time()
        with self._lock:
            due = [x for x in self._q if x["next"] <= now]
        for item in due:
            try:
                r = await client.post(self.server, json=item["msg"])
                r.raise_for_status()
                with self._lock:
                    if item in self._q:
                        self._q.remove(item)
                self.sent += 1
                self.last_ok = time.time()
                self.last_error = None
            except Exception as exc:  # sin internet / ntfy caído: reintenta con espera creciente
                item["tries"] += 1
                item["next"] = time.time() + min(30 * 2 ** item["tries"], 1800)
                self.last_error = type(exc).__name__
                if item["tries"] > 12:
                    with self._lock:
                        if item in self._q:
                            self._q.remove(item)

    # ------------------------------------------------------------------ persistencia (versión en la nube)
    def dump(self) -> list[dict]:
        with self._lock:
            return [{k: v for k, v in x["msg"].items() if k != "topic"} for x in self._q]

    def load(self, msgs: list[dict]) -> None:
        for m in msgs or []:
            m = dict(m)
            m["topic"] = self.topic
            with self._lock:
                self._q.append({"msg": m, "tries": 0, "next": 0.0})

    def status(self) -> dict:
        return {"enabled": True, "topic": self.topic, "sent": self.sent, "pending": len(self._q),
                "error": self.last_error, "last_ok": self.last_ok}

    # ------------------------------------------------------------------ mensajes
    @staticmethod
    def _stake_word(p: dict) -> str:
        st = p.get("stake_pct") or 0.01
        return "apuesta chica" if st < 0.012 else ("apuesta normal" if st < 0.022 else "apuesta fuerte")

    def pick(self, p: dict, tz) -> None:
        book = {"Caliente.mx": "Caliente"}.get(p.get("book") or "", p.get("book"))
        when = f"{p['home']} vs {p['away']} · {_when(p['start_ts'], tz)}"
        lvl = {"lock": "LOCK", "alta": "Confianza alta", "media": "Confianza media", "baja": "Confianza baja"}.get(
            p.get("conf") or "", "")
        pre = f"{lvl} · " if lvl else ""
        if p["tier"] == "P":
            msg = (f"{p['market_label']}: {p['selection_label']}\n{when}\n"
                   f"Búscala en tu casa: conviene si la línea sigue en {p['line']:g} y paga {p['odds']:.2f} o más "
                   f"({self._stake_word(p)}).")
            self.queue(f"{pre}Prop · {p['league']}", msg, priority=5 if p.get("conf") == "lock" else 4)
            return
        if p["tier"] == "A":
            price = f"{book} la paga {p['odds']:.2f}. Si ya bajó de {p['min_odds']:.2f}, no la tomes."
        else:
            price = f"Búscala en tu casa: conviene si paga {p['odds']:.2f} o más."
        msg = f"{p['selection_label']} · {p['market_label']}\n{when}\n{price} ({self._stake_word(p)})"
        self.queue(f"{pre}{p['league']}", msg,
                   priority=5 if p.get("conf") == "lock" else (4 if p["tier"] == "A" else 3))

    def result(self, p: dict, result: str, units: float, h, a, week_line: str) -> None:
        txt = RES_TXT.get(result, result)
        if p.get("market_type") == "scorer":
            msg = f"{p['market_label']}: {p['selection_label']}\n{txt}: {units:+.2f} u\n{week_line}"
            self.queue(f"{txt} · {p['market_label']}", msg, priority=2)
            return
        if p.get("market_type") == "prop":
            fin = f"Terminó con {h:g}" if isinstance(h, (int, float)) else "Sin estadística (no jugó)"
            msg = f"{p['market_label']}: {p['selection_label']}\n{fin} · {txt}: {units:+.2f} u\n{week_line}"
            self.queue(f"{txt} · {p['market_label']}", msg, priority=2)
            return
        msg = (f"{p['home']} {h}–{a} {p['away']}\n"
               f"{txt}: {units:+.2f} u\n{week_line}")
        self.queue(f"{txt} · {p['selection_label']}", msg, priority=2)

    def weekly(self, label: str, s: dict, verdict: str) -> None:
        if not s.get("picks"):
            msg = f"Semana del {label}: ningún pick pasó el filtro. Mejor nada que apostar sin ventaja."
        else:
            msg = verdict
        self.queue(f"Resumen NURI · semana del {label}", msg, priority=3)

    def test(self, active: list[dict], tz) -> None:
        if active:
            lines = []
            for p in active[:6]:
                price = (f"{p.get('book')} {p['odds']:.2f}" if p["tier"] == "A" else f"desde {p['odds']:.2f}")
                lines.append(f"• {p['selection_label']} ({p['home']} vs {p['away']}, {_when(p['start_ts'], tz)}) · {price}")
            body = "Ya quedó. Picks activos ahora:\n" + "\n".join(lines)
        else:
            body = "Ya quedó. Aquí te van a llegar los picks en cuanto aparezcan."
        self.queue("NURI conectado a tu cel", body, priority=3)
