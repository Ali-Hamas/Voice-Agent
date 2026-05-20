"""Use Twilio API to auto-configure the voice webhook on a restaurant's number."""
from __future__ import annotations

import logging

from twilio.rest import Client

from .config import PUBLIC_HOST

log = logging.getLogger(__name__)


def activate_number(account_sid: str, auth_token: str, phone_number: str) -> dict:
    """Point the given Twilio phone number's voice webhook at our /voice/incoming.

    Returns {"ok": True, "sid": ..., "url": ...} or {"ok": False, "error": ...}.
    """
    if not PUBLIC_HOST:
        return {"ok": False, "error": "PUBLIC_HOST not set in .env"}
    webhook_url = f"https://{PUBLIC_HOST}/voice/incoming"
    try:
        client = Client(account_sid, auth_token)
        numbers = client.incoming_phone_numbers.list(phone_number=phone_number, limit=1)
        if not numbers:
            return {"ok": False, "error": f"Number {phone_number} not found in this Twilio account"}
        num = numbers[0]
        updated = client.incoming_phone_numbers(num.sid).update(
            voice_url=webhook_url,
            voice_method="POST",
            voice_fallback_url="",
        )
        log.info("Activated %s -> %s", phone_number, webhook_url)
        return {"ok": True, "sid": updated.sid, "url": webhook_url}
    except Exception as exc:
        log.exception("Twilio activation failed")
        return {"ok": False, "error": str(exc)}
