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


def send_forward_sms(
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_number: str,
    forward_target: str,
    restaurant_name: str,
) -> dict:
    """SMS a one-tap call-forwarding link to the manager's phone."""
    if not (account_sid and auth_token and from_number and to_number and forward_target):
        return {"ok": False, "error": "Missing Twilio credentials or numbers"}
    us_link = f"tel:*72{forward_target}"
    eu_link = f"tel:**21*{forward_target}#"
    body = (
        f"Hi! Set up your AI receptionist for {restaurant_name}.\n\n"
        f"Open this on the RESTAURANT phone and tap call:\n"
        f"• US/Canada: {us_link}\n"
        f"• UK/Europe: {eu_link}\n\n"
        f"Press call, wait for the beep, hang up. Every incoming call now goes to the AI agent."
    )
    try:
        client = Client(account_sid, auth_token)
        msg = client.messages.create(from_=from_number, to=to_number, body=body)
        log.info("Forwarding SMS sent sid=%s to=%s", msg.sid, to_number)
        return {"ok": True, "sid": msg.sid}
    except Exception as exc:
        log.exception("send_forward_sms failed")
        return {"ok": False, "error": str(exc)}
