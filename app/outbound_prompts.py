"""Outbound reservation-reminder agent: system prompt + tool schemas."""
from __future__ import annotations

from .prompts import LOOKUP_TOOL, TRANSFER_TOOL


def build_outbound_reminder_instructions(
    restaurant: dict,
    job: dict,
    *,
    leave_voicemail: bool = False,
) -> str:
    name = restaurant.get("name") or "the restaurant"
    ctx = job.get("context") or {}
    guest = (job.get("guest_name") or ctx.get("guest_name") or "the guest").strip()
    party = ctx.get("party_size") or ctx.get("party") or "your party"
    date = ctx.get("date") or "the booked date"
    time = ctx.get("time") or "the booked time"
    notes = ctx.get("notes") or ""
    reservation_id = job.get("reservation_id")
    languages = restaurant.get("languages") or (
        "English by default. If the guest answers in another language, "
        "switch to that language for the rest of the call."
    )

    transfer_number = restaurant.get("transfer_number") or ""
    transfer_clause = (
        "If the guest asks to speak to a human, call `transfer_to_human`."
        if transfer_number
        else "If the guest insists on a human, apologise — no human number is configured."
    )

    voicemail_clause = (
        "This is a RETRY attempt. If you reach voicemail again, leave a short "
        "message: 'Hi {guest}, this is the assistant for {name} confirming your "
        "reservation for {party} on {date} at {time}. Please call us back to "
        "confirm. Thank you.' Then end the call. Do NOT call any tool when "
        "leaving a voicemail."
        if leave_voicemail
        else "If you reach voicemail or get 5+ seconds of silence after your "
        "greeting, end the call quietly without leaving a message and without "
        "calling any tool."
    ).format(guest=guest, name=name, party=party, date=date, time=time)

    return f"""You are the AI assistant for {name}, placing an OUTBOUND call to a
guest to confirm their reservation. You are NOT a receptionist taking new
bookings — you initiated this call.

Guest: {guest}
Reservation id: {reservation_id}
Party size: {party}
Date: {date}
Time: {time}
Notes: {notes or "none"}

Languages: {languages}
Language rules:
- Start the call in the restaurant's primary language as listed above.
- If the guest answers in a different language, immediately switch to THEIR
  language and continue the entire rest of the call in that language.
- You are fluent in English, Spanish, French, German, Italian, Portuguese,
  Hindi, Urdu, Punjabi, Bengali, Arabic, Mandarin, Cantonese, Japanese,
  Korean, Russian, Turkish, Dutch, Polish, Vietnamese, Thai, Indonesian,
  Tagalog, and many more. Use whichever the guest is most comfortable in.
- Names, dates, and times should be pronounced naturally in the chosen
  language.

Your job (keep the entire call under 60 seconds):

1. Open with: "Hi, this is the AI assistant for {name}, calling for {guest} —
   is now a good time?"

2. Confirm the booking:
   "I'm calling to confirm your reservation for {party} on {date} at {time}.
   Are you still planning to come?"

3. Based on the answer:
   - CONFIRMED ("yes", "we'll be there", "see you then"):
       Call `confirm_reservation` with reservation_id={reservation_id}.
       Optionally include `eta_minutes` if they say they'll be late.
       Then thank them and end the call.
   - CANCEL ("no", "we can't make it", "cancel"):
       Call `cancel_reservation` with reservation_id={reservation_id} and a
       short `reason`. Apologise, offer to rebook another time if they want.
   - RESCHEDULE ("can we change it to ..."):
       Confirm the new date and time aloud, then call
       `reschedule_reservation` with reservation_id={reservation_id},
       new_date, and new_time.

4. If they ask a quick factual question about the restaurant (hours, address,
   parking, menu), call `lookup_knowledge` and answer briefly. Do NOT take new
   orders or new reservations on this call.

5. {transfer_clause}

Voicemail handling: {voicemail_clause}

Style:
- Warm, polite, never robotic. Use the guest's first name once.
- Short sentences. Do not over-explain.
- Always confirm date / time / party size back before calling any tool.
""".strip()


CONFIRM_RESERVATION_TOOL = {
    "type": "function",
    "name": "confirm_reservation",
    "description": (
        "Mark the reservation as confirmed by the guest after they have "
        "explicitly agreed they are coming. Optionally record an ETA if they "
        "said they will be late."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reservation_id": {
                "type": "integer",
                "description": "The reservation id provided in the system prompt.",
            },
            "eta_minutes": {
                "type": "integer",
                "description": "Minutes late, if they said they would be late. Omit if on time.",
            },
            "notes": {
                "type": "string",
                "description": "Any free-text notes (special requests, head-count change).",
            },
        },
        "required": ["reservation_id"],
    },
}


CANCEL_RESERVATION_TOOL = {
    "type": "function",
    "name": "cancel_reservation",
    "description": (
        "Cancel the reservation after the guest explicitly says they cannot "
        "make it. Always confirm with the guest before calling this."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reservation_id": {"type": "integer"},
            "reason": {
                "type": "string",
                "description": "Short reason the guest gave for cancelling.",
            },
        },
        "required": ["reservation_id"],
    },
}


RESCHEDULE_RESERVATION_TOOL = {
    "type": "function",
    "name": "reschedule_reservation",
    "description": (
        "Move the reservation to a new date and/or time after the guest has "
        "confirmed the new slot back to you."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reservation_id": {"type": "integer"},
            "new_date": {
                "type": "string",
                "description": "New reservation date (YYYY-MM-DD or natural).",
            },
            "new_time": {
                "type": "string",
                "description": "New reservation time (e.g. 19:30 or 7:30pm).",
            },
            "party_size": {
                "type": "integer",
                "description": "Updated party size, if they changed it.",
            },
        },
        "required": ["reservation_id", "new_date", "new_time"],
    },
}


OUTBOUND_TOOLS = [
    CONFIRM_RESERVATION_TOOL,
    CANCEL_RESERVATION_TOOL,
    RESCHEDULE_RESERVATION_TOOL,
    LOOKUP_TOOL,
    TRANSFER_TOOL,
]
