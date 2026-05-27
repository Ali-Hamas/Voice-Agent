"""Cron scheduler — enqueues reservation-reminder jobs N hours before each
booking for restaurants that have reminders enabled.

Reservation date/time fields are free-form strings (the inbound agent fills
them via the LLM), so we parse defensively and skip rows we can't interpret.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from . import outbound_jobs as outbound
from .config import OUTBOUND_SCHEDULER_INTERVAL_SEC
from .db import connect

log = logging.getLogger(__name__)


_TIME_PATTERNS = [
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %I:%M%p",
    "%Y-%m-%d %I:%M %p",
    "%Y-%m-%d %H:%M:%S",
]


def _normalize_time(t: str) -> str:
    t = t.strip().lower().replace(" ", "")
    # "7pm" -> "7:00pm"
    m = re.fullmatch(r"(\d{1,2})(am|pm)", t)
    if m:
        return f"{m.group(1)}:00{m.group(2)}"
    return t


def _parse_reservation_dt(date_str: str, time_str: str) -> datetime | None:
    if not date_str or not time_str:
        return None
    candidate = f"{date_str.strip()} {_normalize_time(time_str)}"
    for fmt in _TIME_PATTERNS:
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _restaurants_with_reminders_enabled() -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT * FROM restaurants
            WHERE reminder_enabled = 1
              AND twilio_number IS NOT NULL
              AND twilio_account_sid IS NOT NULL
              AND twilio_auth_token IS NOT NULL
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _upcoming_reservations(restaurant_id: int) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            """
            SELECT * FROM reservations
            WHERE restaurant_id = ?
            ORDER BY id DESC
            LIMIT 500
            """,
            (restaurant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def tick() -> int:
    """One scheduler iteration. Returns number of jobs enqueued."""
    enqueued = 0
    now = datetime.now()
    for restaurant in _restaurants_with_reminders_enabled():
        lead = int(restaurant.get("reminder_hours_before") or 4)
        target_lo = now + timedelta(hours=lead) - timedelta(seconds=30)
        target_hi = now + timedelta(hours=lead) + timedelta(seconds=OUTBOUND_SCHEDULER_INTERVAL_SEC + 30)

        for res in _upcoming_reservations(restaurant["id"]):
            res_dt = _parse_reservation_dt(res.get("date") or "", res.get("time") or "")
            if not res_dt:
                continue
            if not (target_lo <= res_dt <= target_hi):
                continue
            if outbound.job_exists_for_reservation(res["id"], "reservation_reminder"):
                continue
            phone = (res.get("phone") or "").strip()
            if not phone:
                continue
            outbound.enqueue_job(
                restaurant_id=restaurant["id"],
                job_type="reservation_reminder",
                reservation_id=res["id"],
                to_number=phone,
                guest_name=res.get("name") or "",
                context={
                    "guest_name": res.get("name") or "",
                    "party_size": res.get("party_size"),
                    "date": res.get("date"),
                    "time": res.get("time"),
                    "notes": res.get("notes") or "",
                },
                source="cron",
                scheduled_at=int(time.time()),
            )
            enqueued += 1
            log.info(
                "SCHEDULER: enqueued reminder for reservation %s (restaurant %s, %s)",
                res["id"], restaurant["slug"], res_dt.isoformat(),
            )
    return enqueued


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    log.info("SCHEDULER started (interval=%ss)", OUTBOUND_SCHEDULER_INTERVAL_SEC)
    while not stop_event.is_set():
        try:
            tick()
        except Exception:
            log.exception("SCHEDULER tick crashed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=OUTBOUND_SCHEDULER_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    log.info("SCHEDULER stopped")
