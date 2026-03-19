"""Telegram delivery adapter for heartbeat alerts."""

import structlog
from telegram import Bot
from telegram.constants import ParseMode

from gateways.telegram.formatting import md_to_telegram_html

logger = structlog.get_logger()


class TelegramHeartbeatDelivery:
    def __init__(self, bot: Bot, chat_ids: list[int]) -> None:
        self._bot = bot
        self._chat_ids = chat_ids

    async def deliver(self, message: str) -> None:
        formatted = md_to_telegram_html(message)
        for chat_id in self._chat_ids:
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=formatted,
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                logger.warning("heartbeat_html_failed", chat_id=chat_id)
                try:
                    await self._bot.send_message(chat_id=chat_id, text=message)
                except Exception:
                    logger.exception("heartbeat_delivery_failed", chat_id=chat_id)
