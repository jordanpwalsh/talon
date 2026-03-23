"""One heartbeat cycle — imperative shell that runs checks and escalates."""

import subprocess

import structlog

from agent.domain.ports import InferencePort
from agent.services.orchestrator import run_agent
from agent.tools.registry import ToolRegistry
from conversation.domain.model import Conversation, Message
from heartbeat.domain.evaluate import (
    build_llm_prompt,
    evaluate_results,
    parse_checklist,
    parse_sections,
    rebuild_file,
)
from heartbeat.domain.model import CheckItem, CheckResult, HeartbeatConfig
from heartbeat.domain.ports import DeliveryPort
from inference.adapters.openrouter import OpenRouterAdapter

logger = structlog.get_logger()

DEFAULT_MANAGED_CHECKS = """\

- Disk usage on / below 90%
  ```sh
  df -h / | awk 'NR==2 {print $5}'
  ```

- No uncommitted changes in talon
  ```sh
  cd ~/devel/personal/talon && git status --porcelain
  ```"""

DEFAULT_MANAGED_TASKS = """\

(reserved for future talon-shipped tasks)"""

HEARTBEAT_SYSTEM_PROMPT = """\
You are Talon's heartbeat monitor. You review periodic system check results
and produce brief, actionable summaries. Only flag things that genuinely need
the user's attention. Be concise — one or two sentences per issue."""


def ensure_heartbeat_file(config: HeartbeatConfig) -> str:
    """Ensure HEARTBEAT.md exists with current managed sections, preserving user sections.

    Returns the file text.
    """
    config.checklist_path.parent.mkdir(parents=True, exist_ok=True)

    if config.checklist_path.exists():
        text = config.checklist_path.read_text()
        sections = parse_sections(text)
        user_sections = {
            k: v for k, v in sections.items()
            if k in ("User Checks", "User Tasks")
        }
    else:
        user_sections = {}
        logger.info("heartbeat_file_creating", path=str(config.checklist_path))

    new_text = rebuild_file(DEFAULT_MANAGED_CHECKS, DEFAULT_MANAGED_TASKS, user_sections)
    config.checklist_path.write_text(new_text)
    logger.info("heartbeat_file_synced", path=str(config.checklist_path))
    return new_text


def _run_check(item: CheckItem) -> CheckResult:
    """Execute a single check's shell command."""
    if item.command is None:
        logger.info("heartbeat_check_skip", description=item.description, reason="no command, needs LLM")
        return CheckResult(item=item, output="", exit_code=-1)
    try:
        logger.info("heartbeat_check_run", description=item.description, command=item.command)
        proc = subprocess.run(
            item.command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = proc.stdout + proc.stderr
        logger.info(
            "heartbeat_check_done",
            description=item.description,
            exit_code=proc.returncode,
            output=output.strip()[:500],
        )
        return CheckResult(item=item, output=output, exit_code=proc.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("heartbeat_check_timeout", description=item.description, timeout=30)
        return CheckResult(item=item, output="command timed out", exit_code=-1)
    except Exception as e:
        logger.error("heartbeat_check_error", description=item.description, error=str(e))
        return CheckResult(item=item, output=str(e), exit_code=-1)


async def run_heartbeat(
    config: HeartbeatConfig,
    inference: InferencePort,
    tool_registry: ToolRegistry,
    delivery: DeliveryPort,
) -> None:
    """Execute one heartbeat cycle."""
    logger.info("heartbeat_cycle_start", checklist_path=str(config.checklist_path))

    text = config.checklist_path.read_text()
    sections = parse_sections(text)

    # Collect checks from both managed and user sections
    items: list[CheckItem] = []
    for heading in ("Managed Checks (do not edit)", "User Checks"):
        section = sections.get(heading, "")
        items.extend(parse_checklist(section))

    if not items:
        logger.warning("heartbeat_skip", reason="empty checklist")
        return

    logger.info("heartbeat_checks_parsed", count=len(items), descriptions=[i.description for i in items])

    results = [_run_check(item) for item in items]
    report = evaluate_results(results)

    logger.info(
        "heartbeat_evaluation",
        all_ok=report.all_ok,
        needs_llm=report.needs_llm,
        summary=report.summary,
        checks=len(results),
    )

    if report.all_ok and not report.needs_llm:
        if config.always_notify:
            logger.info("heartbeat_notify_ok", reason="always_notify enabled")
            await delivery.deliver(f"Heartbeat OK: {report.summary}")
        else:
            logger.info("heartbeat_ok_silent", reason="all checks passed, no notification needed")
        return

    if report.needs_llm:
        llm_prompt = build_llm_prompt(report)
        logger.info("heartbeat_llm_escalate", prompt_length=len(llm_prompt))
        heartbeat_inference = OpenRouterAdapter(system_prompt=HEARTBEAT_SYSTEM_PROMPT)
        conversation = Conversation().append(Message(role="user", content=llm_prompt))
        response_text, _ = await run_agent(heartbeat_inference, conversation, tool_registry)
        logger.info("heartbeat_llm_response", summary=response_text[:500])
        await delivery.deliver(response_text)
    else:
        logger.info("heartbeat_alert_plain", summary=report.summary)
        await delivery.deliver(f"Heartbeat alert:\n{report.summary}")

    logger.info("heartbeat_cycle_done")
