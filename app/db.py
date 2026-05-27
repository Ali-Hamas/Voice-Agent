"""SQLite layer: restaurants, phone numbers, reservations, orders."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DATA_DIR, DB_PATH, RESTAURANTS_DIR


SCHEMA = """
CREATE TABLE IF NOT EXISTS restaurants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    cuisine TEXT,
    address TEXT,
    hours TEXT,
    greeting TEXT,
    tone TEXT,
    languages TEXT,
    transfer_number TEXT,
    twilio_account_sid TEXT,
    twilio_auth_token TEXT,
    twilio_number TEXT UNIQUE,
    active INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL,
    name TEXT,
    party_size INTEGER,
    date TEXT,
    time TEXT,
    phone TEXT,
    notes TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL,
    name TEXT,
    phone TEXT,
    items_json TEXT,
    mode TEXT,
    address TEXT,
    notes TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER,
    twilio_call_sid TEXT,
    from_number TEXT,
    to_number TEXT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    restaurant_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
);

CREATE TABLE IF NOT EXISTS outbound_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    restaurant_id INTEGER NOT NULL,
    job_type TEXT NOT NULL,
    reservation_id INTEGER,
    to_number TEXT NOT NULL,
    guest_name TEXT,
    context_json TEXT,
    scheduled_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    twilio_call_sid TEXT,
    outcome TEXT,
    outcome_notes TEXT,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (restaurant_id) REFERENCES restaurants(id),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);

CREATE INDEX IF NOT EXISTS idx_outbound_due
    ON outbound_jobs(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_outbound_restaurant
    ON outbound_jobs(restaurant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_calls_restaurant_started
    ON calls(restaurant_id, started_at DESC);
"""


_RESTAURANT_ADD_COLUMNS = [
    ("reminder_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("reminder_hours_before", "INTEGER NOT NULL DEFAULT 4"),
    ("webhook_secret", "TEXT"),
]


def create_user(email: str, password_hash: str, restaurant_id: int) -> int:
    with connect() as cx:
        cur = cx.execute(
            "INSERT INTO users (email, password_hash, restaurant_id, created_at) VALUES (?, ?, ?, ?)",
            (email.lower().strip(), password_hash, restaurant_id, int(time.time())),
        )
        return cur.lastrowid


def get_user_by_email(email: str) -> dict | None:
    with connect() as cx:
        row = cx.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    return dict(row) if row else None


def get_user(uid: int) -> dict | None:
    with connect() as cx:
        row = cx.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESTAURANTS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as cx:
        cx.executescript(SCHEMA)
        existing = {row["name"] for row in cx.execute("PRAGMA table_info(restaurants)")}
        for col, ddl in _RESTAURANT_ADD_COLUMNS:
            if col not in existing:
                cx.execute(f"ALTER TABLE restaurants ADD COLUMN {col} {ddl}")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    cx = sqlite3.connect(DB_PATH)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def restaurant_dir(slug: str) -> Path:
    d = RESTAURANTS_DIR / slug
    (d / "knowledge").mkdir(parents=True, exist_ok=True)
    (d / "chroma").mkdir(parents=True, exist_ok=True)
    return d


def create_restaurant(**fields: Any) -> int:
    fields = dict(fields)
    fields.setdefault("created_at", int(time.time()))
    keys = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with connect() as cx:
        cur = cx.execute(
            f"INSERT INTO restaurants ({keys}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        rid = cur.lastrowid
    restaurant_dir(fields["slug"])
    return rid


def update_restaurant(rid: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as cx:
        cx.execute(f"UPDATE restaurants SET {sets} WHERE id = ?", (*fields.values(), rid))


def get_restaurant(rid: int) -> dict | None:
    with connect() as cx:
        row = cx.execute("SELECT * FROM restaurants WHERE id = ?", (rid,)).fetchone()
    return dict(row) if row else None


def get_restaurant_by_slug(slug: str) -> dict | None:
    with connect() as cx:
        row = cx.execute("SELECT * FROM restaurants WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def get_restaurant_by_number(number: str) -> dict | None:
    with connect() as cx:
        row = cx.execute(
            "SELECT * FROM restaurants WHERE twilio_number = ?", (number,)
        ).fetchone()
    return dict(row) if row else None


def list_restaurants() -> list[dict]:
    with connect() as cx:
        rows = cx.execute("SELECT * FROM restaurants ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def add_reservation(restaurant_id: int, **fields: Any) -> int:
    fields = dict(fields)
    fields["restaurant_id"] = restaurant_id
    fields.setdefault("created_at", int(time.time()))
    keys = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with connect() as cx:
        cur = cx.execute(
            f"INSERT INTO reservations ({keys}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def list_reservations(restaurant_id: int) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT * FROM reservations WHERE restaurant_id = ? ORDER BY id DESC",
            (restaurant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_order(restaurant_id: int, items: list[dict], **fields: Any) -> int:
    fields = dict(fields)
    fields["restaurant_id"] = restaurant_id
    fields["items_json"] = json.dumps(items)
    fields.setdefault("created_at", int(time.time()))
    keys = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    with connect() as cx:
        cur = cx.execute(
            f"INSERT INTO orders ({keys}) VALUES ({placeholders})",
            tuple(fields.values()),
        )
        return cur.lastrowid


def list_orders(restaurant_id: int) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT * FROM orders WHERE restaurant_id = ? ORDER BY id DESC",
            (restaurant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def start_call(
    restaurant_id: int,
    twilio_call_sid: str | None,
    from_number: str | None,
    to_number: str | None,
) -> int:
    with connect() as cx:
        cur = cx.execute(
            """
            INSERT INTO calls (restaurant_id, twilio_call_sid, from_number, to_number, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (restaurant_id, twilio_call_sid, from_number, to_number, int(time.time())),
        )
        return cur.lastrowid


def end_call(call_id: int) -> None:
    with connect() as cx:
        cx.execute(
            "UPDATE calls SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
            (int(time.time()), call_id),
        )


def list_recent_calls(restaurant_id: int, limit: int = 20) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT * FROM calls
            WHERE restaurant_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (restaurant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_calls_since(restaurant_id: int, since_ts: int) -> int:
    with connect() as cx:
        row = cx.execute(
            "SELECT COUNT(*) AS c FROM calls WHERE restaurant_id = ? AND started_at >= ?",
            (restaurant_id, since_ts),
        ).fetchone()
    return int(row["c"]) if row else 0
