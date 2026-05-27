"""OpenAI Realtime WebSocket wrapper, configured per-restaurant."""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from .config import OPENAI_API_KEY, OPENAI_REALTIME_MODEL
from .prompts import TOOLS, build_system_instructions

log = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime?model={model}"


class RealtimeSession:
    def __init__(
        self,
        restaurant: dict,
        *,
        instructions: str | None = None,
        tools: list | None = None,
    ) -> None:
        self.restaurant = restaurant
        self.voice = "alloy"
        self.ws: ClientConnection | None = None
        self._instructions_override = instructions
        self._tools_override = tools

    async def connect(self) -> None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        url = REALTIME_URL.format(model=OPENAI_REALTIME_MODEL)
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }
        self.ws = await websockets.connect(url, additional_headers=headers, max_size=None)
        await self._configure_session()

    async def _configure_session(self) -> None:
        instructions = (
            self._instructions_override
            if self._instructions_override is not None
            else build_system_instructions(self.restaurant)
        )
        tools = self._tools_override if self._tools_override is not None else TOOLS
        await self.send({
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": instructions,
                "voice": self.voice,
                "input_audio_format": "g711_ulaw",
                "output_audio_format": "g711_ulaw",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                },
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.7,
            },
        })

    async def trigger_greeting(self) -> None:
        await self.send({
            "type": "response.create",
            "response": {"modalities": ["audio", "text"]},
        })

    async def send(self, payload: dict) -> None:
        assert self.ws is not None
        await self.ws.send(json.dumps(payload))

    async def send_audio_chunk(self, base64_pcm_ulaw: str) -> None:
        await self.send({
            "type": "input_audio_buffer.append",
            "audio": base64_pcm_ulaw,
        })

    async def send_function_result(self, call_id: str, output: str) -> None:
        await self.send({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            },
        })
        await self.send({"type": "response.create"})

    async def events(self) -> AsyncIterator[dict]:
        assert self.ws is not None
        async for raw in self.ws:
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Non-JSON event: %r", raw[:200])

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
