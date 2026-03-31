import asyncio
import logging
import signal

import structlog
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from agent.services.input_handler import SlashSkill
from agent.tools import filesystem, shell
from agent.tools.registry import ToolRegistry
from gateways.telegram.handlers import make_handlers
from config import (
    build_inference,
    discover_skills,
    get_allowed_telegram_ids,
    get_heartbeat_config,
    get_inference_config,
    get_system_prompt,
    get_telegram_token,
)
from conversation.services.session import InMemorySessionStore

# Configure stdlib logging: root at INFO with console + file, noisy libs at WARNING
logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("talon.log"),
    ],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def main() -> None:
    system_prompt = get_system_prompt()
    inference = build_inference(system_prompt=system_prompt)
    inference_config = get_inference_config()

    tool_registry = ToolRegistry()
    tool_registry.register(shell.DEFINITION, shell.handle)
    tool_registry.register(filesystem.READ_DEFINITION, filesystem.handle_read)
    tool_registry.register(filesystem.WRITE_DEFINITION, filesystem.handle_write)
    tool_registry.register(filesystem.LIST_DEFINITION, filesystem.handle_list)

    session_store = InMemorySessionStore()
    allowed_ids = get_allowed_telegram_ids()

    skills = discover_skills()
    slash_skills = [
        SlashSkill(
            name=skill.name,
            description=skill.description,
            content=skill.content,
        )
        for skill in skills
    ]
    for skill in skills:
        logger.info("skill_discovered", name=skill.name, description=skill.description, dir=str(skill.dir))
    logger.info(
        "talon_started",
        provider=inference_config.provider,
        model=inference_config.model,
        system_prompt_tokens=_estimate_tokens(system_prompt),
        tools=len(tool_registry.definitions),
        tool_names=[t.name for t in tool_registry.definitions],
        skills=len(skills),
        allowed_users=len(allowed_ids) if allowed_ids else "all",
    )

    handle_start, handle_message = make_handlers(
        inference, tool_registry, session_store, allowed_ids, slash_skills
    )

    heartbeat_config = get_heartbeat_config()

    _heartbeat_task: asyncio.Task | None = None

    async def post_init(application: Application) -> None:
        nonlocal _heartbeat_task
        if heartbeat_config.enabled:
            from gateways.telegram.heartbeat_delivery import TelegramHeartbeatDelivery
            from heartbeat.services.scheduler import scheduler_loop

            delivery = TelegramHeartbeatDelivery(application.bot, heartbeat_config.delivery_chat_ids)
            _heartbeat_task = asyncio.create_task(
                scheduler_loop(heartbeat_config, inference, tool_registry, delivery),
                name="scheduler",
            )
            logger.info(
                "heartbeat_enabled",
                interval_minutes=heartbeat_config.interval_minutes,
                active_hours=heartbeat_config.active_hours,
                chat_ids=heartbeat_config.delivery_chat_ids,
            )

    async def post_shutdown(application: Application) -> None:
        if _heartbeat_task and not _heartbeat_task.done():
            _heartbeat_task.cancel()

    app = (
        Application.builder()
        .token(get_telegram_token())
        .get_updates_request(HTTPXRequest(connect_timeout=20.0, read_timeout=20.0))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(
        MessageHandler(
            filters.COMMAND & ~filters.Regex(r"^/start(?:@[\w_]+)?(?:\s|$)"),
            handle_message,
        )
    )

    def _reload_config(signum, frame):
        nonlocal heartbeat_config
        new_prompt = get_system_prompt()
        inference._system_prompt = new_prompt
        heartbeat_config = get_heartbeat_config()
        slash_skills[:] = [
            SlashSkill(
                name=skill.name,
                description=skill.description,
                content=skill.content,
            )
            for skill in discover_skills()
        ]
        logger.info(
            "config_reloaded",
            system_prompt_tokens=_estimate_tokens(new_prompt),
            skills=len(slash_skills),
        )

    signal.signal(signal.SIGHUP, _reload_config)

    logger.info("polling")
    app.run_polling()


if __name__ == "__main__":
    main()
