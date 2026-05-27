"""Restaurant receptionist system prompt + tool definitions."""
from __future__ import annotations


def build_system_instructions(restaurant: dict) -> str:
    name = restaurant.get("name") or "the restaurant"
    cuisine = restaurant.get("cuisine") or "general"
    hours = restaurant.get("hours") or "not specified"
    address = restaurant.get("address") or "not specified"
    tone = restaurant.get("tone") or "warm, professional, concise"
    languages = restaurant.get("languages") or (
        "English by default. If the caller speaks another language, switch to it."
    )
    greeting = restaurant.get("greeting") or (
        f"Hello, thank you for calling {name}. How can I help you today?"
    )

    transfer_number = restaurant.get("transfer_number") or ""
    transfer_clause = (
        f"If the caller wants a human (manager/owner/real person), or you cannot "
        f"help with something important, call the `transfer_to_human` tool. It "
        f"will redirect the call to {transfer_number}. Tell the caller 'one "
        f"moment, connecting you now' BEFORE calling the tool."
        if transfer_number
        else "If the caller insists on a human, apologise — no human number is configured yet."
    )

    return f"""You are the AI receptionist for {name}, a {cuisine} restaurant.

Restaurant facts you can state directly:
- Hours: {hours}
- Address: {address}

Tone: {tone}
Languages: {languages}
Language rules:
- Greet in the restaurant's primary language listed above.
- If the caller speaks a different language, immediately switch to THEIR
  language and continue the entire rest of the call in that language.
- You are fluent in English, Spanish, French, German, Italian, Portuguese,
  Hindi, Urdu, Punjabi, Bengali, Arabic, Mandarin, Cantonese, Japanese,
  Korean, Russian, Turkish, Dutch, Polish, Vietnamese, Thai, Indonesian,
  Tagalog, and many more. Use whichever the caller is most comfortable in.
- Pronounce names, dates, times, prices, and menu items naturally in the
  chosen language.

Your job:
1. Greet warmly.
2. Answer questions about the menu, hours, location, dietary options, parking,
   prices, etc. Use the `lookup_knowledge` tool whenever the caller asks
   something specific that is not in the facts above.
3. Take table reservations using the `create_reservation` tool. Ask for:
   name, party size, date, time, and a contact phone number. Confirm back to
   the caller before saving.
4. Take takeaway or delivery orders using the `take_order` tool. Ask for:
   items, name, phone, pickup or delivery (and address if delivery).
   Read back the order to confirm before saving.
5. {transfer_clause}

Important rules:
- NEVER invent menu items or prices. If you don't know, call lookup_knowledge.
- Keep answers under 3 sentences unless the caller asks for detail.
- For reservations and orders, ALWAYS confirm details back before calling the
  tool, and tell the caller once it is saved.
- Today's date can be inferred from the caller's words. If unclear, ask.

Start the call by saying: "{greeting}"
""".strip()


LOOKUP_TOOL = {
    "type": "function",
    "name": "lookup_knowledge",
    "description": (
        "Search the restaurant's knowledge base (menu, FAQs, policies, hours) "
        "for information needed to answer the caller. Use whenever the caller "
        "asks about menu items, prices, dietary options, location details, "
        "parking, opening hours, or anything specific."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A short English search query summarising what to look up.",
            }
        },
        "required": ["query"],
    },
}


RESERVATION_TOOL = {
    "type": "function",
    "name": "create_reservation",
    "description": (
        "Save a table reservation after confirming all details with the caller."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Guest's full name"},
            "party_size": {"type": "integer", "description": "Number of guests"},
            "date": {"type": "string", "description": "Reservation date (YYYY-MM-DD or natural)"},
            "time": {"type": "string", "description": "Reservation time (e.g. 19:30 or 7:30pm)"},
            "phone": {"type": "string", "description": "Caller phone number"},
            "notes": {"type": "string", "description": "Any special requests (allergies, occasion)"},
        },
        "required": ["name", "party_size", "date", "time", "phone"],
    },
}


ORDER_TOOL = {
    "type": "function",
    "name": "take_order",
    "description": "Save a takeaway or delivery order after confirming with the caller.",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "phone": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "qty": {"type": "integer"},
                        "notes": {"type": "string"},
                    },
                    "required": ["name", "qty"],
                },
            },
            "mode": {"type": "string", "enum": ["pickup", "delivery"]},
            "address": {"type": "string", "description": "Delivery address if mode is delivery"},
            "notes": {"type": "string"},
        },
        "required": ["name", "phone", "items", "mode"],
    },
}


TRANSFER_TOOL = {
    "type": "function",
    "name": "transfer_to_human",
    "description": (
        "Transfer the live phone call to a human (the restaurant's manager or "
        "owner). Use ONLY when the caller explicitly asks for a human, or when "
        "you genuinely cannot help. Always say a short hand-off line like "
        "'one moment, connecting you' BEFORE calling this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason for the transfer (for logs).",
            }
        },
        "required": ["reason"],
    },
}


TOOLS = [LOOKUP_TOOL, RESERVATION_TOOL, ORDER_TOOL, TRANSFER_TOOL]
