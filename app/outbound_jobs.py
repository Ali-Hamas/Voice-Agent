"""CRUD for the outbound_jobs queue."""
from __future__ import annotations

import json
import time
from typing import Any

from .db import connect


def _now() -> int:
    return int(time.time())


def enqueue_job(
    *,
    restaurant_id: int,
    job_type: str,
    to_number: str,
    source: str,
    scheduled_at: int | None = None,
    reservation_id: int | None = None,
    guest_name: str = "",
    context: dict[str, Any] | None = None,
    attempts: int = 0,
) -> int:
    ts = _now()
    with connect() as cx:
        cur = cx.execute(
            """
            INSERT INTO outbound_jobs (
                restaurant_id, job_type, reservation_id, to_number, guest_name,
                context_json, scheduled_at, status, attempts, source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                restaurant_id,
                job_type,
                reservation_id,
                to_number,
                guest_name,
                json.dumps(context or {}),
                scheduled_at if scheduled_at is not None else ts,
                attempts,
                source,
                ts,
                ts,
            ),
        )
        return cur.lastrowid


def get_job(job_id: int) -> dict | None:
    with connect() as cx:
        row = cx.execute(
            "SELECT * FROM outbound_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["context"] = json.loads(d.get("context_json") or "{}")
    except json.JSONDecodeError:
        d["context"] = {}
    return d


def next_due_jobs(limit: int) -> list[dict]:
    """Queued jobs whose scheduled_at has passed, oldest first."""
    if limit <= 0:
        return []
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT * FROM outbound_jobs
            WHERE status = 'queued' AND scheduled_at <= ?
            ORDER BY scheduled_at ASC, id ASC
            LIMIT ?
            """,
            (_now(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_in_flight() -> int:
    """Jobs currently dialing or in-call (toward the global concurrency cap)."""
    with connect() as cx:
        row = cx.execute(
            "SELECT COUNT(*) AS n FROM outbound_jobs WHERE status IN ('dialing', 'in_call')"
        ).fetchone()
    return int(row["n"]) if row else 0


def _set_status(job_id: int, status: str, **extra: Any) -> None:
    fields = {"status": status, "updated_at": _now(), **extra}
    sets = ", ".join(f"{k} = ?" for k in fields)
    with connect() as cx:
        cx.execute(
            f"UPDATE outbound_jobs SET {sets} WHERE id = ?",
            (*fields.values(), job_id),
        )


def mark_dialing(job_id: int, twilio_call_sid: str) -> None:
    with connect() as cx:
        cx.execute(
            """
            UPDATE outbound_jobs
            SET status = 'dialing',
                twilio_call_sid = ?,
                attempts = attempts + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (twilio_call_sid, _now(), job_id),
        )


def mark_in_call(job_id: int) -> None:
    _set_status(job_id, "in_call")


def mark_done(job_id: int, outcome: str, notes: str = "") -> None:
    _set_status(job_id, "done", outcome=outcome, outcome_notes=notes)


def mark_failed(job_id: int, outcome: str, notes: str = "") -> None:
    _set_status(job_id, "failed", outcome=outcome, outcome_notes=notes)


def set_outcome(job_id: int, outcome: str, notes: str = "") -> None:
    """Set outcome WITHOUT changing status (used by LLM tools mid-call)."""
    with connect() as cx:
        cx.execute(
            """
            UPDATE outbound_jobs
            SET outcome = ?, outcome_notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (outcome, notes, _now(), job_id),
        )


def cancel_job(job_id: int) -> None:
    _set_status(job_id, "cancelled", outcome="cancelled")


def list_jobs_for_restaurant(restaurant_id: int, limit: int = 200) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT * FROM outbound_jobs
            WHERE restaurant_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (restaurant_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def job_exists_for_reservation(reservation_id: int, job_type: str) -> bool:
    """True if any non-cancelled job already exists for this reservation+type.

    Prevents the cron from enqueuing duplicates on every tick.
    """
    with connect() as cx:
        row = cx.execute(
            """
            SELECT 1 FROM outbound_jobs
            WHERE reservation_id = ?
              AND job_type = ?
              AND status != 'cancelled'
            LIMIT 1
            """,
            (reservation_id, job_type),
        ).fetchone()
    return row is not None
