"""Async scheduler loop — 60s tick for cron tasks + heartbeat checks."""

import asyncio
from datetime import datetime

import structlog

from agent.domain.ports import InferencePort
from agent.tools.registry import ToolRegistry
from config import get_local_now
from heartbeat.domain.evaluate import parse_scheduled_tasks
from heartbeat.domain.model import HeartbeatConfig
from heartbeat.domain.ports import DeliveryPort
from heartbeat.services.runner import ensure_heartbeat_file, run_heartbeat
from heartbeat.services.task_runner import run_scheduled_tasks

logger = structlog.get_logger()


async def scheduler_loop(
    config: HeartbeatConfig,
    inference: InferencePort,
    tool_registry: ToolRegistry,
    delivery: DeliveryPort,
) -> None:
    """Tick every 60 seconds: run due cron tasks, check if heartbeat interval elapsed."""
    logger.info(
        "scheduler_loop_started",
        interval_minutes=config.interval_minutes,
        active_hours=config.active_hours,
        checklist_path=str(config.checklist_path),
        always_notify=config.always_notify,
    )

    ensure_heartbeat_file(config)

    last_heartbeat: datetime | None = None

    while True:
        await asyncio.sleep(60)
        try:
            now = get_local_now()

            # Read fresh file each tick (user may have edited it)
            text = config.checklist_path.read_text()

            # Run due scheduled tasks (unconditional — no active_hours check)
            tasks = parse_scheduled_tasks(text)
            if tasks:
                await run_scheduled_tasks(
                    tasks, now, config, inference, tool_registry, delivery,
                )

            # Heartbeat checks respect active_hours and interval
            start, end = config.active_hours
            if not (start <= now.hour < end):
                continue

            interval_seconds = config.interval_minutes * 60
            if last_heartbeat is not None and (now - last_heartbeat).total_seconds() < interval_seconds:
                continue

            logger.info("heartbeat_cycle_trigger", hour=now.hour)
            await run_heartbeat(config, inference, tool_registry, delivery)
            last_heartbeat = now

        except Exception:
            logger.exception("scheduler_error")
