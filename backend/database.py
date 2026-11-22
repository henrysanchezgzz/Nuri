"""
NURI · 누리 — database.py
=========================
Persistencia local en SQLite: bankroll, apuestas tomadas, historial, auditoría CLV
y caché de logos oficiales.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

def _default_db() -> Path:
    # En Windows la base vive en %LOCALAPPDATA%\NURI para que OneDrive no la sincronice a medio escribir.
    if os.name == "nt" and os.getenv("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "NURI" / "nuri.sqlite3"
    return Path(__file__).resolve().parent / "nuri.sqlite3"


DB_PATH = Path(os.getenv("NURI_DB") or _default_db())
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_id         TEXT,
    group_id        TEXT,
    created_at      REAL NOT NULL,
    event_id        TEXT NOT NULL,
    sport           TEXT,
    league          TEXT,
    event_name      TEXT,
    start_ts        REAL,
    status_at_take  TEXT,
    market_key      TEXT NOT NULL,
    market_label    TEXT,
    selection       TEXT NOT NULL,
    selection_label TEXT,
    book            TEXT NOT NULL,
    odds            REAL NOT NULL,
    stake           REAL NOT NULL,
    p_real          REAL,
    fair_odds       REAL,
    ev              REAL,
    source          TEXT,
    closing_odds    REAL,
    clv             REAL,
    clv_frozen      INTEGER DEFAULT 0,
    result          TEXT DEFAULT 'pending',
    profit          REAL DEFAULT 0,
    settled_at      REAL,
    mode            TEXT
);
CREATE INDEX IF NOT EXISTS idx_bets_result ON bets(result);
CREATE TABLE IF NOT EXISTS logos (
    key TEXT PRIMARY KEY,
    url TEXT,
    ts  REAL
);
CREATE TABLE IF NOT EXISTS nuri_picks (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    tier            TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    espn_id         TEXT,
    league_code     TEXT,
    sport           TEXT,
    league          TEXT,
    home            TEXT,
    away            TEXT,
    home_logo       TEXT,
    away_logo       TEXT,
    start_ts        REAL,
    market_key      TEXT NOT NULL,
    market_type     TEXT,
    market_label    TEXT,
    line            REAL,
    selection       TEXT NOT NULL,
    selection_label TEXT,
    book            TEXT,
    odds            REAL NOT NULL,
    min_odds        REAL,
    p               REAL NOT NULL,
    ev              REAL,
    score           REAL,
    stats_score     REAL,
    move            REAL,
    stake_pct       REAL,
    reason          TEXT,
    close_p         REAL,
    close_odds      REAL,
    close_ts        REAL,
    result          TEXT DEFAULT 'pending',
    units           REAL DEFAULT 0,
    final_home      INTEGER,
    final_away      INTEGER,
    settled_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_nuri_picks_result ON nuri_picks(result);
CREATE INDEX IF NOT EXISTS idx_nuri_picks_start ON nuri_picks(start_ts);
"""


@contextmanager
def _conn():
    with _lock:
        con = sqlite3.connect(DB_PATH, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(SCHEMA)
        cols = {r[1] for r in con.execute("PRAGMA table_info(nuri_picks)").fetchall()}
        for col, typ in (("conf", "TEXT"), ("conf_score", "REAL")):   # nivel de confianza del pick
            if col not in cols:
                con.execute(f"ALTER TABLE nuri_picks ADD COLUMN {col} {typ}")
        if con.execute("SELECT 1 FROM settings WHERE key='bankroll_initial'").fetchone() is None:
            con.execute("INSERT INTO settings(key, value) VALUES ('bankroll_initial', '500')")
            con.execute("INSERT INTO settings(key, value) VALUES ('bankroll_set_at', ?)", (str(time.time()),))


# ------------------------------------------------------------------ settings
def get_setting(key: str, default: str | None = None) -> str | None:
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as con:
        con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value))


def get_initial_bankroll() -> float:
    return float(get_setting("bankroll_initial", "500") or 500)


def set_initial_bankroll(amount: float) -> None:
    set_setting("bankroll_initial", f"{float(amount):.2f}")
    set_setting("bankroll_set_at", str(time.time()))


# ------------------------------------------------------------------ bets
def add_bet(data: dict) -> int:
    cols = ["pick_id", "group_id", "created_at", "event_id", "sport", "league", "event_name", "start_ts",
            "status_at_take", "market_key", "market_label", "selection", "selection_label", "book", "odds",
            "stake", "p_real", "fair_odds", "ev", "source", "closing_odds", "clv", "mode"]
    row = {c: data.get(c) for c in cols}
    row["created_at"] = row["created_at"] or time.time()
    with _conn() as con:
        cur = con.execute(f"INSERT INTO bets({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          [row[c] for c in cols])
        return int(cur.lastrowid)


def get_bet(bet_id: int) -> dict | None:
    with _conn() as con:
        r = con.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
        return dict(r) if r else None


def list_bets(limit: int = 300) -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM bets ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def pending_bets() -> list[dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM bets WHERE result='pending'").fetchall()]


def pending_pick_ids() -> set[str]:
    with _conn() as con:
        return {r["pick_id"] for r in con.execute(
            "SELECT pick_id FROM bets WHERE result='pending' AND pick_id IS NOT NULL").fetchall()}


def settle_bet(bet_id: int, result: str) -> dict | None:
    if result not in ("won", "lost", "push", "pending"):
        raise ValueError("Resultado inválido")
    bet = get_bet(bet_id)
    if not bet:
        return None
    if result == "won":
        profit = bet["stake"] * (bet["odds"] - 1.0)
    elif result == "lost":
        profit = -bet["stake"]
    else:
        profit = 0.0
    settled = None if result == "pending" else time.time()
    with _conn() as con:
        con.execute("UPDATE bets SET result=?, profit=?, settled_at=? WHERE id=?",
                    (result, round(profit, 2), settled, bet_id))
    return get_bet(bet_id)


def delete_bet(bet_id: int) -> bool:
    with _conn() as con:
        return con.execute("DELETE FROM bets WHERE id=?", (bet_id,)).rowcount > 0


def update_clv(bet_id: int, closing_odds: float, clv: float, freeze: bool = False) -> None:
    with _conn() as con:
        con.execute("UPDATE bets SET closing_odds=?, clv=?, clv_frozen=? WHERE id=? AND clv_frozen=0",
                    (round(closing_odds, 4), round(clv, 5), 1 if freeze else 0, bet_id))


# ------------------------------------------------------------------ bóveda
def bankroll_state() -> dict:
    initial = get_initial_bankroll()
    with _conn() as con:
        settled = con.execute(
            "SELECT COALESCE(SUM(profit),0) AS p, COALESCE(SUM(stake),0) AS s, COUNT(*) AS n, "
            "SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) AS w, "
            "SUM(CASE WHEN result='lost' THEN 1 ELSE 0 END) AS l, "
            "SUM(CASE WHEN result='push' THEN 1 ELSE 0 END) AS pu "
            "FROM bets WHERE result IN ('won','lost','push')").fetchone()
        pend = con.execute("SELECT COALESCE(SUM(stake),0) AS s, COUNT(*) AS n FROM bets WHERE result='pending'").fetchone()
        clv = con.execute("SELECT AVG(clv) AS c, COUNT(clv) AS n FROM bets WHERE clv IS NOT NULL").fetchone()
    profit = float(settled["p"])
    balance = initial + profit
    in_play = float(pend["s"])
    staked = float(settled["s"])
    return {
        "initial": round(initial, 2),
        "balance": round(balance, 2),
        "in_play": round(in_play, 2),
        "available": round(balance - in_play, 2),
        "profit": round(profit, 2),
        "roi": round(profit / staked, 4) if staked > 0 else 0.0,
        "clv_avg": round(float(clv["c"]), 4) if clv["c"] is not None else None,
        "clv_count": int(clv["n"] or 0),
        "settled": int(settled["n"] or 0),
        "won": int(settled["w"] or 0), "lost": int(settled["l"] or 0), "push": int(settled["pu"] or 0),
        "pending": int(pend["n"] or 0),
    }


def bankroll_history() -> list[dict]:
    initial = get_initial_bankroll()
    set_at = float(get_setting("bankroll_set_at", str(time.time())) or time.time())
    with _conn() as con:
        rows = con.execute("SELECT settled_at, profit FROM bets WHERE result IN ('won','lost','push') "
                           "AND settled_at IS NOT NULL ORDER BY settled_at ASC").fetchall()
    first_ts = min([set_at] + [r["settled_at"] for r in rows]) if rows else set_at
    series = [{"ts": first_ts - 1, "balance": round(initial, 2)}]
    bal = initial
    for r in rows:
        bal += r["profit"]
        series.append({"ts": r["settled_at"], "balance": round(bal, 2)})
    return series


# ------------------------------------------------------------------ logos
def get_logo(key: str) -> tuple[str, float] | None:
    with _conn() as con:
        r = con.execute("SELECT url, ts FROM logos WHERE key=?", (key,)).fetchone()
        return (r["url"] or "", float(r["ts"])) if r else None


def set_logo(key: str, url: str) -> None:
    with _conn() as con:
        con.execute("INSERT INTO logos(key, url, ts) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "url=excluded.url, ts=excluded.ts", (key, url, time.time()))


# ------------------------------------------------------------------ picks propios de NURI (autoevaluación)
PICK_FIELDS = ("id", "created_at", "tier", "event_id", "espn_id", "league_code", "sport", "league", "home", "away",
               "home_logo", "away_logo", "start_ts", "market_key", "market_type", "market_label", "line",
               "selection", "selection_label", "book", "odds", "min_odds", "p", "ev", "score", "stats_score",
               "move", "stake_pct", "reason", "conf", "conf_score")


def add_nuri_pick(rec: dict) -> bool:
    cols = [c for c in PICK_FIELDS if c in rec]
    with _conn() as con:
        cur = con.execute(f"INSERT OR IGNORE INTO nuri_picks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [rec[c] for c in cols])
        return cur.rowcount > 0


def update_nuri_pick(pick_id: str, **fields) -> None:
    if not fields:
        return
    with _conn() as con:
        con.execute(f"UPDATE nuri_picks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    [*fields.values(), pick_id])


def list_nuri_picks(since_ts: float | None = None) -> list[dict]:
    with _conn() as con:
        if since_ts is None:   # 'retired' = picks de "búscala" retirados: no cuentan ni se muestran
            rows = con.execute("SELECT * FROM nuri_picks WHERE result != 'retired' ORDER BY start_ts DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM nuri_picks WHERE start_ts >= ? AND result != 'retired' "
                               "ORDER BY start_ts DESC", (since_ts,)).fetchall()
        return [dict(r) for r in rows]


def pending_nuri_picks() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM nuri_picks WHERE result = 'pending' ORDER BY start_ts").fetchall()
        return [dict(r) for r in rows]


def delete_nuri_pick(pick_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM nuri_picks WHERE id = ? AND result = 'pending'", (pick_id,))
