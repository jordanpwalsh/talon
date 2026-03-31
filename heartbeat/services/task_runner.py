"""Scheduled task execution — runs due cron/once tasks."""

import subprocess
from datetime import datetime

import structlog

from agent.domain.ports import InferencePort
from agent.services.orchestrator import run_agent
from agent.tools.registry import ToolRegistry
from conversation.domain.model import Conversation, Message
from heartbeat.domain.evaluate import (
    build_task_llm_prompt,
    is_task_due,
    load_task_state,
    mark_task_done,
    save_task_state,
)
from heartbeat.domain.model import HeartbeatConfig, ScheduledTask
from heartbeat.domain.ports import DeliveryPort
from config import build_inference

logger = structlog.get_logger()

TASK_SYSTEM_PROMPT = """\
You are Talon, a personal AI assistant. You are executing a scheduled task
for your owner. Complete the task described, be concise and actionable in your
response. You have access to shell commands and file operations if needed."""


def _task_key(task: ScheduledTask) -> str:
    """Identity key for tracking last runs."""
    return f"{task.schedule}|{task.command or task.description}"


class _TaskCommandFailed(Exception):
    def __init__(self, description: str, exit_code: int, output: str):
        self.description = description
        self.exit_code = exit_code
        self.output = output
        super().__init__(f"Task '{description}' failed with exit code {exit_code}")


def _load_state(config: HeartbeatConfig) -> dict[str, datetime]:
    """Load persisted task state from disk."""
    if not config.state_path.exists():
        return {}
    try:
        return load_task_state(config.state_path.read_text())
    except Exception:
        logger.warning("task_state_load_error", path=str(config.state_path))
        return {}


def _save_state(config: HeartbeatConfig, state: dict[str, datetime]) -> None:
    """Persist task state to disk."""
    try:
        config.state_path.parent.mkdir(parents=True, exist_ok=True)
        config.state_path.write_text(save_task_state(state))
    except Exception:
        logger.exception("task_state_save_error", path=str(config.state_path))


async def run_scheduled_tasks(
    tasks: list[ScheduledTask],
    now: datetime,
    config: HeartbeatConfig,
    inference: InferencePort,
    tool_registry: ToolRegistry,
    delivery: DeliveryPort,
) -> None:
    """Run all due scheduled tasks. State is persisted to disk."""
    state = _load_state(config)

    for task in tasks:
        key = _task_key(task)
        last_run = state.get(key)

        fire_time = is_task_due(task.schedule, last_run, now)
        if fire_time is None:
            continue

        logger.info(
            "task_due",
            schedule=task.schedule,
            description=task.description,
            needs_llm=task.needs_llm,
            has_command=task.command is not None,
        )

        try:
            await _execute_task(task, tool_registry, delivery)
            state[key] = fire_time
            _save_state(config, state)

            if task.is_once:
                _mark_done_in_file(config, task, now)
        except Exception:
            logger.exception("task_execution_error", description=task.description)


def _run_command(task: ScheduledTask) -> str:
    """Execute a task's shell command. Returns output.

    Raises _TaskCommandFailed for non-zero exit on fire-and-forget tasks.
    """
    logger.info("task_command_run", description=task.description, command=task.command)
    try:
        proc = subprocess.run(
            task.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.warning("task_command_timeout", description=task.description, timeout=120)
        raise

    output = proc.stdout + proc.stderr
    logger.info(
        "task_command_done",
        description=task.description,
        exit_code=proc.returncode,
        output=output.strip()[:500],
    )

    if proc.returncode != 0 and not task.needs_llm:
        raise _TaskCommandFailed(task.description, proc.returncode, output)

    return output


async def _execute_task(
    task: ScheduledTask,
    tool_registry: ToolRegistry,
    delivery: DeliveryPort,
) -> None:
    """Execute a single task according to behavior matrix."""
    shell_output = None

    if task.command is not None:
        try:
            shell_output = _run_command(task)
        except _TaskCommandFailed as e:
            await delivery.deliver(
                f"Task failed: {e.description}\nExit code: {e.exit_code}\n```\n{e.output.strip()[:1000]}\n```"
            )
            return

    if not task.needs_llm:
        return

    prompt = build_task_llm_prompt(task, shell_output)
    logger.info("task_llm_invoke", description=task.description, prompt_length=len(prompt))

    task_inference = build_inference(system_prompt=TASK_SYSTEM_PROMPT)
    conversation = Conversation().append(Message(role="user", content=prompt))
    response_text, _ = await run_agent(task_inference, conversation, tool_registry)

    logger.info("task_llm_response", description=task.description, response=response_text[:500])
    await delivery.deliver(response_text)


def _mark_done_in_file(config: HeartbeatConfig, task: ScheduledTask, now: datetime) -> None:
    """Rewrite @once → @done in HEARTBEAT.md."""
    try:
        text = config.checklist_path.read_text()
        new_text = mark_task_done(text, task, now)
        config.checklist_path.write_text(new_text)
        logger.info("task_marked_done", description=task.description)
    except Exception:
        logger.exception("task_mark_done_error", description=task.description)
