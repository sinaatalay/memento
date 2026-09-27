"""Telegram transport. Only a paired private chat can control the demo."""
from __future__ import annotations

import secrets
import httpx

from .storage import Store


class Telegram:
    def __init__(self, token: str, store: Store, chat_id: str = ""):
        self.token = token
        self.store = store
        self.chat_id = chat_id or store.setting("telegram_chat_id", "")
        self.username: str | None = None
        self.offset = store.setting("telegram_offset", 0)
        self.pair_code = store.setting("telegram_pair_code") or secrets.token_hex(3)
        store.set_setting("telegram_pair_code", self.pair_code)
        self.client = httpx.AsyncClient(timeout=35)

    async def call(self, method: str, **payload):
        response = await self.client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        # Do not put the request URL (which contains the token) into an exception.
        if response.status_code != 200:
            raise RuntimeError(f"Telegram {method}: HTTP {response.status_code}")
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Telegram {method} rejected the request")
        return result["result"]

    async def connect(self):
        me = await self.call("getMe")
        self.username = me.get("username")

    async def send(self, text: str) -> str:
        if not self.chat_id:
            raise RuntimeError("Telegram is waiting for the owner to pair")
        message = await self.call("sendMessage", chat_id=self.chat_id, text=text[:4096])
        return str(message["message_id"])

    async def poll(self) -> list[dict]:
        updates = await self.call("getUpdates", offset=self.offset, timeout=20, allowed_updates=["message"])
        events = []
        for update in updates:
            message = update.get("message", {})
            chat = message.get("chat", {})
            text = message.get("text", "")
            incoming_id = str(chat.get("id", ""))
            if chat.get("type") == "private":
                if not self.chat_id and text.strip() == f"/start {self.pair_code}":
                    self.chat_id = incoming_id
                    self.store.set_setting("telegram_chat_id", self.chat_id)
                    await self.send("Memento is connected. Tell me something to remember; I'll keep track.")
                elif self.chat_id == incoming_id and text and not text.startswith("/start"):
                    event = {"id": f"telegram:{update['update_id']}", "source": "chat", "text": text, "chat_id": incoming_id}
                    # Persist before moving Telegram's update cursor.
                    self.store.add_event(event["id"], "chat", event)
                    events.append(event)
            self.offset = update["update_id"] + 1
            self.store.set_setting("telegram_offset", self.offset)
        return events

    async def close(self):
        await self.client.aclose()
