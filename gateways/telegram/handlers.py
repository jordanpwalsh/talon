"""Telegram handlers — imperative shell wiring I/O to the domain."""

import asyncio

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from agent.domain.ports import InferencePort
from agent.services.orchestrator import AgentCancelled, run_agent
from agent.tools.registry import ToolRegistry
from gateways.telegram.formatting import md_to_telegram_html
from gateways.telegram.typing_indicator import TelegramTypingIndicator
from conversation.domain.model import Conversation, Message
from conversation.services.session import SessionStore

logger = structlog.get_logger()


def make_handlers(
    inference: InferencePort,
    tool_registry: ToolRegistry,
    session_store: SessionStore,
    allowed_ids: set[int],
):
    """Create handler functions closed over the domain dependencies."""

    # Per-user cancel events and running tasks
    _cancel_events: dict[int, asyncio.Event] = {}
    _running_tasks: dict[int, asyncio.Task] = {}

    def _is_allowed(update: Update) -> bool:
        if not allowed_ids:
            return True
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        return user_id in allowed_ids or chat_id in allowed_ids

    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if not _is_allowed(update):
            logger.warning("unauthorized_start", user_id=user_id)
            return
        logger.info("start", user_id=user_id)
        # Clear session on /start
        session_store.save(Conversation(session_id=str(user_id)))
        await update.message.reply_text(
            "Hello! I'm powered by Claude. Ask me anything."
        )

    async def _run_and_reply(update: Update, user_id: int, conversation: Conversation) -> None:
        cancel_event = asyncio.Event()
        _cancel_events[user_id] = cancel_event
        typing = TelegramTypingIndicator(update.get_bot(), update.effective_chat.id)
        await typing.start()

        try:
            response_text, conversation = await run_agent(
                inference, conversation, tool_registry, cancel_event
            )
            session_store.save(conversation)

            logger.info("reply", user_id=user_id, text=response_text[:100])
            formatted = md_to_telegram_html(response_text)
            try:
                await update.message.reply_text(formatted, parse_mode=ParseMode.HTML)
            except Exception as exc:
                logger.warning("html_send_failed", error=str(exc))
                await update.message.reply_text(response_text)
        except AgentCancelled:
            logger.info("agent_cancelled", user_id=user_id)
            await update.message.reply_text("Stopped.")
        finally:
            await typing.stop()
            _cancel_events.pop(user_id, None)
            _running_tasks.pop(user_id, None)

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if not _is_allowed(update):
            logger.warning("unauthorized_message", user_id=user_id)
            return
        user_text = update.message.text
        logger.info("message", user_id=user_id, text=user_text)

        # Handle stop command
        if user_text.strip().lower() == "stop":
            cancel_event = _cancel_events.get(user_id)
            task = _running_tasks.get(user_id)
            if cancel_event and task and not task.done():
                logger.info("stop_requested", user_id=user_id)
                cancel_event.set()
                task.cancel()
                await update.message.reply_text("Stopping...")
            else:
                await update.message.reply_text("Nothing running.")
            return

        # Cancel any existing run for this user
        cancel_event = _cancel_events.get(user_id)
        task = _running_tasks.get(user_id)
        if cancel_event and task and not task.done():
            cancel_event.set()
            task.cancel()

        # Load or create conversation for this user
        session_id = str(user_id)
        conversation = session_store.get(session_id)
        if conversation is None:
            conversation = Conversation(session_id=session_id)

        conversation = conversation.append(Message(role="user", content=user_text))

        _running_tasks[user_id] = asyncio.create_task(
            _run_and_reply(update, user_id, conversation)
        )

    return handle_start, handle_message
