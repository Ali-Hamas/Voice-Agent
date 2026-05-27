"""Outbound call dispatcher.

Single background asyncio loop. Pulls due jobs off the `outbound_jobs` queue,
respects the global concurrency cap, and originates calls via the restaurant's
own Twilio credentials.
"""
from __future__ import annotations

import asyncio
import logging

from twilio.rest import Client as TwilioClient

from . import outbound_jobs as outbound
from .config import (
    OUTBOUND_DISPATCH_INTERVAL_SEC,
    OUTBOUND_GLOBAL_CONCURRENCY,
    PUBLIC_HOST,
)
from .db import get_restaurant

log = logging.getLogger(__name__)


async def _place_call(job: dict, restaurant: dict) -> None:
    if not PUBLIC_HOST:
        log.error("OUTBOUND: PUBLIC_HOST not set; cannot place call for job %s", job["id"])
        outbound.mark_failed(job["id"], "config_error", "PUBLIC_HOST not set")
        return

    sid = restaurant.get("twilio_account_sid") or ""
    tok = restaurant.get("twilio_auth_token") or ""
    frm = restaurant.get("twilio_number") or ""
    if not (sid and tok and frm):
        log.error("OUTBOUND: restaurant %s missing Twilio creds", restaurant.get("slug"))
        outbound.mark_failed(job["id"], "config_error", "twilio credentials missing")
        return

    twiml_url = f"https://{PUBLIC_HOST}/voice/outbound/{job['id']}"
    status_url = f"https://{PUBLIC_HOST}/voice/outbound/status/{job['id']}"

    try:
        client = TwilioClient(sid, tok)
        call = await asyncio.to_thread(
            lambda: client.calls.create(
                to=job["to_number"],
                from_=frm,
                url=twiml_url,
                status_callback=status_url,
                status_callback_method="POST",
                status_callback_event=["initiated", "ringing", "answered", "completed"],
                machine_detection="Enable",
            )
        )
    except Exception as exc:
        log.exception("OUTBOUND: Twilio calls.create failed for job %s", job["id"])
        outbound.mark_failed(job["id"], "twilio_error", str(exc)[:300])
        return

    outbound.mark_dialing(job["id"], call.sid)
    log.info("OUTBOUND: job %s -> Twilio call %s (to=%s)",
             job["id"], call.sid, job["to_number"])


async def _tick() -> None:
    in_flight = outbound.count_in_flight()
    capacity = max(0, OUTBOUND_GLOBAL_CONCURRENCY - in_flight)
    if capacity <= 0:
        return
    jobs = outbound.next_due_jobs(limit=capacity)
    if not jobs:
        return
    for job in jobs:
        restaurant = get_restaurant(job["restaurant_id"])
        if not restaurant:
            outbound.mark_failed(job["id"], "config_error", "restaurant missing")
            continue
        await _place_call(job, restaurant)


async def dispatcher_loop(stop_event: asyncio.Event) -> None:
    log.info(
        "OUTBOUND dispatcher started (interval=%ss, cap=%s)",
        OUTBOUND_DISPATCH_INTERVAL_SEC,
        OUTBOUND_GLOBAL_CONCURRENCY,
    )
    while not stop_event.is_set():
        try:
            await _tick()
        except Exception:
            log.exception("OUTBOUND dispatcher tick crashed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=OUTBOUND_DISPATCH_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass
    log.info("OUTBOUND dispatcher stopped")
