"""Telegram typing indicator — sends 'typing...' action while agent is working."""

import asyncio

from telegram import Bot
from telegram.constants import ChatAction


class TelegramTypingIndicator:
    """Sends ChatAction.TYPING every 4s until stopped."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._done = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._done.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._done.set()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._done.is_set():
            try:
                await self._bot.send_chat_action(
                    chat_id=self._chat_id, action=ChatAction.TYPING
                )
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._done.wait(), timeout=4)
            except asyncio.TimeoutError:
                pass
