"""SQLite persistence: map lookup cache + quote history with booked/lost outcomes.

The quote history is what makes pricing adaptive over time: if a driver keeps winning
rides, the engine undercuts less; if they keep losing, it undercuts more.
"""
import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "solofare.db"
CACHE_TTL_S = 30 * 24 * 3600  # places and roads rarely change
HISTORY_WINDOW = 20  # outcomes considered when learning


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS quotes (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            pickup TEXT NOT NULL,
            drop_place TEXT NOT NULL,
            distance_km REAL,
            recommended_fare INTEGER NOT NULL,
            anchor_name TEXT,
            anchor_fare REAL,
            demand_pressure REAL,
            outcome TEXT CHECK (outcome IN ('won', 'lost')),
            final_fare INTEGER);
    """)
    return conn


# ---------- cache ----------

def cache_get(key: str):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value, created_at FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["created_at"] < CACHE_TTL_S:
        return json.loads(row["value"])
    return None


def cache_set(key: str, value) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                     (key, json.dumps(value), time.time()))


# ---------- quotes ----------

def save_quote(pickup: str, drop: str, distance_km: float, fare: int,
               anchor_name: str, anchor_fare: float, demand_pressure: float) -> str:
    quote_id = uuid.uuid4().hex[:12]
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO quotes (id, created_at, pickup, drop_place, distance_km, recommended_fare, anchor_name, anchor_fare, demand_pressure)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote_id, time.time(), pickup, drop, distance_km, fare, anchor_name, anchor_fare, demand_pressure))
    return quote_id


def set_outcome(quote_id: str, outcome: str, final_fare: int) -> bool:
    with closing(_connect()) as conn, conn:
        cur = conn.execute("UPDATE quotes SET outcome = ?, final_fare = ? WHERE id = ?", (outcome, final_fare, quote_id))
    return cur.rowcount == 1


def list_quotes(limit: int = 10) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM quotes ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def recent_stats(window: int = HISTORY_WINDOW) -> dict:
    """Win/loss counts over the last `window` quotes that have an outcome."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT outcome, final_fare, anchor_fare FROM quotes WHERE outcome IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (window,)).fetchall()
    won = sum(1 for r in rows if r["outcome"] == "won")
    lost = len(rows) - won
    return {
        "decided": len(rows),
        "won": won,
        "lost": lost,
        "win_rate": round(won / len(rows), 2) if rows else None,
        "earned": sum(r["final_fare"] or 0 for r in rows if r["outcome"] == "won"),
    }
