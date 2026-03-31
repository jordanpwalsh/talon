"""Telegram handlers — imperative shell wiring I/O to the domain."""

import asyncio
import json

import structlog
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from agent.domain.model import AgentEvent
from agent.domain.ports import AgentEventSink, InferencePort
from agent.services.input_handler import SlashSkill, handle_input
from agent.services.orchestrator import AgentCancelled
from agent.tools.registry import ToolRegistry
from gateways.telegram.formatting import md_to_telegram_html
from gateways.telegram.typing_indicator import TelegramTypingIndicator
from conversation.domain.model import Conversation
from conversation.services.session import SessionStore

logger = structlog.get_logger()
EMPTY_REPLY_FALLBACK = "I don't have a reply to send."
TELEGRAM_MESSAGE_LIMIT = 4096
VERBOSE_EVENT_LIMIT = 3000


class TelegramVerboseEventSink:
    def __init__(self, update: Update) -> None:
        self._bot = update.get_bot()
        self._chat_id = update.effective_chat.id

    async def publish(self, event: AgentEvent) -> None:
        text = _truncate_verbose_event(_format_verbose_event(event))
        formatted = md_to_telegram_html(text)
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=formatted,
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning("verbose_send_failed", error=str(exc), event_kind=event.kind)
            await self._bot.send_message(chat_id=self._chat_id, text=text)


def make_handlers(
    inference: InferencePort,
    tool_registry: ToolRegistry,
    session_store: SessionStore,
    allowed_ids: set[int],
    skills: list[SlashSkill],
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
            "Hello! I'm ready. Ask me anything."
        )

    async def _run_and_reply(update: Update, user_id: int, user_text: str) -> None:
        cancel_event = asyncio.Event()
        _cancel_events[user_id] = cancel_event
        typing = TelegramTypingIndicator(update.get_bot(), update.effective_chat.id)
        await typing.start()

        try:
            session_id = str(user_id)
            conversation = session_store.get(session_id)
            if conversation is None:
                conversation = Conversation(session_id=session_id)
            event_sink: AgentEventSink | None = None
            if conversation.verbose:
                event_sink = TelegramVerboseEventSink(update)

            result = await handle_input(
                inference=inference,
                conversation=conversation,
                raw_input=user_text,
                tool_registry=tool_registry,
                skills=skills,
                cancel_event=cancel_event,
                event_sink=event_sink,
            )
            if result.persist:
                session_store.save(result.conversation)

            response_text = _truncate_reply_text(
                result.response_text.strip() or EMPTY_REPLY_FALLBACK
            )
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

        _running_tasks[user_id] = asyncio.create_task(
            _run_and_reply(update, user_id, user_text)
        )

    return handle_start, handle_message


def _format_verbose_event(event: AgentEvent) -> str:
    if event.kind == "assistant_message":
        lines = ["Assistant turn:"]
        text = event.payload.get("text", "").strip()
        if text:
            lines.extend(["", text])

        usage = event.payload.get("usage")
        if usage:
            lines.extend(
                [
                    "",
                    "Usage:",
                    "```json",
                    json.dumps(usage, indent=2, sort_keys=True),
                    "```",
                ]
            )

        reasoning = (event.payload.get("reasoning") or "").strip()
        if reasoning:
            lines.extend(
                [
                    "",
                    "Reasoning:",
                    "```",
                    reasoning,
                    "```",
                ]
            )

        return "\n".join(lines)

    if event.kind == "tool_call":
        arguments = json.dumps(event.payload["arguments"], indent=2, sort_keys=True)
        return (
            f"Tool call: `{event.payload['tool_name']}`\n\n"
            f"```json\n{arguments}\n```"
        )

    if event.kind == "tool_result":
        status = "error" if event.payload["is_error"] else "ok"
        output = event.payload["output"] or "(empty)"
        return (
            f"Tool result ({status}): `{event.payload['tool_name']}`\n\n"
            f"```\n{output}\n```"
        )

    return f"Agent event: {event.kind}"


def _truncate_verbose_event(text: str) -> str:
    if len(text) <= VERBOSE_EVENT_LIMIT:
        return text

    marker = "\n\n[truncated for Telegram]"
    budget = VERBOSE_EVENT_LIMIT - len(marker)
    if budget <= 0:
        return marker.strip()

    if "```\n" in text and text.endswith("\n```"):
        prefix, _, rest = text.partition("```\n")
        code_body = rest[:-4]
        code_budget = budget - len(prefix) - len("```\n\n```")
        if code_budget > 0:
            return f"{prefix}```\n{code_body[:code_budget]}\n```{marker}"

    return text[:budget] + marker


def _truncate_reply_text(text: str) -> str:
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return text
    marker = "\n\n[truncated for Telegram]"
    budget = TELEGRAM_MESSAGE_LIMIT - len(marker)
    if budget <= 0:
        return marker.strip()
    return text[:budget] + marker
