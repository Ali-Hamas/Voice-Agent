# Restaurant Voice Agent (SaaS MVP)

A restaurant owner signs up at `/admin`, fills in business details, uploads menu
PDFs, plugs in their Twilio number, clicks **Activate** — the AI receptionist
starts answering that number, taking reservations, and taking orders.

Customers keep calling the restaurant's existing landline; the landline simply
forwards to the Twilio number we activated.

## Architecture (per call)

```
Customer → Landline → (call-forwarding) → Twilio number
                                          │
                                          ▼
Twilio  POST /voice/incoming  ──► look up restaurant by "To" number
                                          │
                                          ▼
                          TwiML <Connect><Stream/></Connect>
                                          │
                                          ▼
                    WS /voice/stream/<slug>  ◄──►  OpenAI Realtime
                                          │           (STT + LLM + TTS)
                                          ▼
                          Tools: lookup_knowledge (per-restaurant ChromaDB)
                                  create_reservation (SQLite)
                                  take_order (SQLite)
```

## Setup

### 1. Python env
```
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Configure `.env`
```
OPENAI_API_KEY=sk-...
PUBLIC_HOST=your-tunnel-host.trycloudflare.com   # no https://, no slash
PORT=8000
```

### 3. Run the server
```
python -m app.main
```

### 4. Expose to the internet
Pick one:
```
ngrok http 8000
# or
cloudflared tunnel --url http://localhost:8000 --protocol http2
```
Copy the public host into `.env` as `PUBLIC_HOST` and **restart the server**.

### 5. Open the admin UI
http://localhost:8000/admin

For each restaurant:
1. **Add restaurant** (name + cuisine).
2. **Business profile** — fill hours, address, greeting, tone, languages.
3. **Knowledge** — drag-and-drop the menu PDF + any FAQ/policy files.
   Re-ingest happens automatically.
4. **Connect Twilio** — paste Account SID, Auth Token, and the Twilio phone
   number (E.164 format like `+16814056546`). Click **Activate agent**. We
   point the number's webhook at `https://<PUBLIC_HOST>/voice/incoming`.
5. **Tell the restaurant** to forward their existing landline to the Twilio
   number. (Or hand the Twilio number to customers directly.)

### 6. Test
Call the Twilio number from a verified phone. You should hear the configured
greeting and be able to:
- Ask about menu items, hours, location ("Do you do gluten-free pasta?")
- Book a table ("Can I reserve a table for 4 on Saturday at 8?")
- Place a takeaway order ("I'd like two margherita pizzas for pickup")

Reservations and orders appear immediately in the admin panel.

## Files

```
app/
├── main.py             FastAPI app: admin UI + voice routes + WS
├── db.py               SQLite schema + helpers
├── rag.py              Per-restaurant ChromaDB
├── prompts.py          Restaurant system prompt + tools
├── realtime_client.py  OpenAI Realtime WS wrapper
├── twilio_bridge.py    Twilio<->OpenAI relay + tool dispatch
├── twilio_provision.py Set Twilio number's voice webhook via API
├── config.py
├── templates/          Jinja2 admin pages
└── static/style.css
data/
├── voiceagent.sqlite   restaurants, reservations, orders
└── restaurants/<slug>/
    ├── knowledge/      uploaded menus, FAQs
    └── chroma/         per-restaurant vector store
```

## What's NOT in this MVP
- No auth — anyone reaching `/admin` can edit. Add SSO before exposing.
- No website-URL crawler yet (only file upload).
- No outbound calls, no SMS confirmations, no Stripe billing.
- No call transcripts persistence (logs only).
- Cloudflare quick tunnels change URL on restart — Twilio webhook must be
  re-applied. Use a paid tunnel or a real domain for production.

## Swap to self-hosted later
Only `app/realtime_client.py` talks to OpenAI. Replacing it with a Faster-Whisper
+ local LLM + Piper TTS stack does not require changes anywhere else.
