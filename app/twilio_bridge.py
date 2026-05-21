"""Twilio Media Stream <-> OpenAI Realtime relay, per restaurant."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
from twilio.rest import Client as TwilioClient

from .db import add_order, add_reservation, get_restaurant
from .rag import format_context, retrieve
from .realtime_client import RealtimeSession

log = logging.getLogger(__name__)


async def run_bridge(twilio_ws: WebSocket, restaurant: dict) -> None:
    stream_sid: str | None = None
    call_sid: str | None = None
    realtime = RealtimeSession(restaurant=restaurant)
    await realtime.connect()
    done = asyncio.Event()

    async def from_twilio() -> None:
        nonlocal stream_sid, call_sid
        try:
            while True:
                msg = await twilio_ws.receive_text()
                data = json.loads(msg)
                event = data.get("event")
                if event == "start":
                    start = data["start"]
                    stream_sid = start["streamSid"]
                    call_sid = start.get("callSid")
                    log.info("Twilio stream sid=%s call_sid=%s restaurant=%s",
                             stream_sid, call_sid, restaurant["slug"])
                    await realtime.trigger_greeting()
                elif event == "media":
                    await realtime.send_audio_chunk(data["media"]["payload"])
                elif event == "stop":
                    log.info("Twilio stream stopped sid=%s", stream_sid)
                    break
        except WebSocketDisconnect:
            log.info("Twilio disconnected")
        except Exception:
            log.exception("from_twilio error")
        finally:
            done.set()

    async def from_openai() -> None:
        try:
            async for evt in realtime.events():
                if done.is_set():
                    break
                etype = evt.get("type", "")
                if etype == "response.audio.delta":
                    if stream_sid and twilio_ws.client_state == WebSocketState.CONNECTED:
                        await twilio_ws.send_text(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": evt["delta"]},
                        }))
                elif etype == "input_audio_buffer.speech_started":
                    if stream_sid and twilio_ws.client_state == WebSocketState.CONNECTED:
                        await twilio_ws.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": stream_sid,
                        }))
                elif etype == "response.function_call_arguments.done":
                    await _handle_function_call(realtime, restaurant, evt, call_sid)
                elif etype == "conversation.item.input_audio_transcription.completed":
                    log.info("Caller: %s", evt.get("transcript", "").strip())
                elif etype == "response.audio_transcript.done":
                    log.info("Agent: %s", evt.get("transcript", "").strip())
                elif etype == "error":
                    log.error("OpenAI error: %s", evt)
        except Exception:
            log.exception("from_openai error")
        finally:
            done.set()

    t1 = asyncio.create_task(from_twilio())
    t2 = asyncio.create_task(from_openai())
    await done.wait()
    for t in (t1, t2):
        t.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    await realtime.close()


async def _handle_function_call(
    realtime: RealtimeSession,
    restaurant: dict,
    evt: dict,
    call_sid: str | None = None,
) -> None:
    name = evt.get("name")
    call_id = evt.get("call_id")
    raw_args = evt.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError:
        args = {}
    if not call_id:
        return
    rid = restaurant["id"]
    slug = restaurant["slug"]

    if name == "lookup_knowledge":
        q = (args.get("query") or "").strip()
        chunks = retrieve(slug, q, k=4) if q else []
        out = format_context(chunks)
        log.info("[%s] RAG %r -> %d chunks", slug, q, len(chunks))
        await realtime.send_function_result(call_id, out)
        return

    if name == "create_reservation":
        try:
            res_id = add_reservation(
                rid,
                name=args.get("name", ""),
                party_size=int(args.get("party_size") or 0),
                date=args.get("date", ""),
                time=args.get("time", ""),
                phone=args.get("phone", ""),
                notes=args.get("notes", ""),
            )
            out = json.dumps({"ok": True, "reservation_id": res_id})
            log.info("[%s] Reservation #%d saved", slug, res_id)
        except Exception as exc:
            out = json.dumps({"ok": False, "error": str(exc)})
            log.exception("reservation failed")
        await realtime.send_function_result(call_id, out)
        return

    if name == "take_order":
        try:
            order_id = add_order(
                rid,
                items=args.get("items") or [],
                name=args.get("name", ""),
                phone=args.get("phone", ""),
                mode=args.get("mode", "pickup"),
                address=args.get("address", ""),
                notes=args.get("notes", ""),
            )
            out = json.dumps({"ok": True, "order_id": order_id})
            log.info("[%s] Order #%d saved", slug, order_id)
        except Exception as exc:
            out = json.dumps({"ok": False, "error": str(exc)})
            log.exception("order failed")
        await realtime.send_function_result(call_id, out)
        return

    if name == "transfer_to_human":
        target = (restaurant.get("transfer_number") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not target:
            out = json.dumps({"ok": False, "error": "no transfer_number configured"})
            log.warning("[%s] transfer requested but no number set", slug)
            await realtime.send_function_result(call_id, out)
            return
        if not call_sid:
            out = json.dumps({"ok": False, "error": "no active call_sid"})
            log.warning("[%s] transfer requested but no call_sid", slug)
            await realtime.send_function_result(call_id, out)
            return
        sid = restaurant.get("twilio_account_sid") or ""
        tok = restaurant.get("twilio_auth_token") or ""
        if not sid or not tok:
            out = json.dumps({"ok": False, "error": "twilio credentials missing"})
            await realtime.send_function_result(call_id, out)
            return
        twiml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response><Say voice="alice">Connecting you now.</Say>'
            f'<Dial timeout="25" callerId="{restaurant.get("twilio_number") or ""}">{target}</Dial>'
            f'</Response>'
        )
        try:
            client = TwilioClient(sid, tok)
            await asyncio.to_thread(
                lambda: client.calls(call_sid).update(twiml=twiml)
            )
            log.info("[%s] transferred call %s -> %s (reason=%s)", slug, call_sid, target, reason)
            out = json.dumps({"ok": True, "transferred_to": target})
        except Exception as exc:
            log.exception("transfer failed")
            out = json.dumps({"ok": False, "error": str(exc)})
        await realtime.send_function_result(call_id, out)
        return

    log.warning("Unhandled function call: %s", name)
    await realtime.send_function_result(call_id, json.dumps({"ok": False, "error": "unknown tool"}))
