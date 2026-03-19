"""Pure functions for parsing heartbeat checklists and evaluating results."""

import json
import re
from datetime import datetime

from croniter import croniter

from heartbeat.domain.model import CheckItem, CheckResult, HeartbeatReport, ScheduledTask


def parse_sections(text: str) -> dict[str, str]:
    """Split HEARTBEAT.md into sections by heading. Returns dict keyed by heading name."""
    sections: dict[str, str] = {}
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in text.split("\n"):
        heading_match = re.match(r"^# (.+)$", line)
        if heading_match:
            if current_heading is not None:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = heading_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading is not None:
        sections[current_heading] = "\n".join(current_lines)

    return sections


def parse_checklist(section_text: str) -> list[CheckItem]:
    """Parse a checks section into CheckItems.

    Each markdown list item (- ...) is a check. An optional fenced sh block
    immediately following the item provides the cheap shell command.
    """
    items: list[CheckItem] = []
    lines = section_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^- (.+)$", line)
        if match:
            description = match.group(1).strip()
            command = None
            # Look ahead for a fenced sh block
            if i + 1 < len(lines) and re.match(r"^  ```sh\s*$", lines[i + 1]):
                i += 2  # skip the opening fence
                cmd_lines: list[str] = []
                while i < len(lines) and not re.match(r"^  ```\s*$", lines[i]):
                    cmd_lines.append(lines[i].strip())
                    i += 1
                command = "\n".join(cmd_lines) if cmd_lines else None
            items.append(CheckItem(description=description, command=command))
        i += 1
    return items


def parse_scheduled_tasks(text: str) -> list[ScheduledTask]:
    """Parse task sections (both managed and user) from full HEARTBEAT.md text.

    Task format:
    - `schedule` Description `llm`
      ```sh
      command
      ```

    Skips @done entries.
    """
    sections = parse_sections(text)
    tasks: list[ScheduledTask] = []

    for heading in ("Managed Tasks (do not edit)", "User Tasks"):
        section = sections.get(heading, "")
        tasks.extend(_parse_task_section(section))

    return tasks


def _parse_task_section(section_text: str) -> list[ScheduledTask]:
    """Parse a single task section into ScheduledTasks."""
    tasks: list[ScheduledTask] = []
    lines = section_text.split("\n")
    i = 0

    # Pattern: - `schedule` Description `llm`
    task_re = re.compile(r"^- `(@once|@at\b[^`]*|@done\b[^`]*|[^`]+)` (.+)$")

    while i < len(lines):
        line = lines[i]
        match = task_re.match(line)
        if match:
            schedule = match.group(1).strip()
            rest = match.group(2).strip()

            # Skip @done entries
            if schedule.startswith("@done"):
                i += 1
                continue

            # Detect trailing `llm` marker
            needs_llm = False
            llm_match = re.match(r"^(.+?)\s+`llm`$", rest)
            if llm_match:
                needs_llm = True
                description = llm_match.group(1).strip()
            else:
                description = rest

            # Look ahead for optional sh block
            command = None
            if i + 1 < len(lines) and re.match(r"^  ```sh\s*$", lines[i + 1]):
                i += 2
                cmd_lines: list[str] = []
                while i < len(lines) and not re.match(r"^  ```\s*$", lines[i]):
                    cmd_lines.append(lines[i].strip())
                    i += 1
                command = "\n".join(cmd_lines) if cmd_lines else None

            tasks.append(ScheduledTask(
                schedule=schedule,
                description=description,
                command=command,
                needs_llm=needs_llm,
                is_once=schedule == "@once" or schedule.startswith("@at "),
            ))
        i += 1

    return tasks


def is_task_due(schedule: str, last_run: datetime | None, now: datetime) -> datetime | None:
    """Check if a task should fire. Returns the matched cron fire time, or None.

    The caller should use the returned time as last_run to avoid re-firing
    for the same cron event. State is persisted to disk, so last_run=None
    means a genuinely new task — fire if a cron event fell within the last
    tick (60s).

    For cron: uses croniter to check if a cron event fell in the window.
    For @once: due if no last_run (returns now).
    """
    if schedule == "@once":
        return now if last_run is None else None

    if schedule.startswith("@at "):
        if last_run is not None:
            return None
        target = datetime.fromisoformat(schedule[4:].strip())
        return target if target <= now else None

    if not croniter.is_valid(schedule):
        return None

    if last_run is None:
        # New task, no prior state — fire if a cron event fell within the last tick
        cron = croniter(schedule, now)
        prev_time = cron.get_prev(datetime)
        if (now - prev_time).total_seconds() <= 60:
            return prev_time
        return None

    # Check if any cron fire time falls in (last_run, now]
    cron = croniter(schedule, last_run)
    next_time = cron.get_next(datetime)
    if next_time <= now:
        return next_time
    return None


def load_task_state(text: str) -> dict[str, datetime]:
    """Parse JSON state text into {task_key: last_run} dict."""
    if not text.strip():
        return {}
    raw = json.loads(text)
    return {k: datetime.fromisoformat(v) for k, v in raw.items()}


def save_task_state(state: dict[str, datetime]) -> str:
    """Serialize {task_key: last_run} dict to JSON text."""
    raw = {k: v.isoformat(timespec="seconds") for k, v in state.items()}
    return json.dumps(raw, indent=2) + "\n"


def build_task_llm_prompt(task: ScheduledTask, shell_output: str | None = None) -> str:
    """Build LLM prompt from task description + optional shell output."""
    lines = [
        f"Scheduled task: {task.description}",
        "",
    ]
    if shell_output is not None:
        lines.append("Shell command output:")
        lines.append(f"```\n{shell_output}\n```")
        lines.append("")
    lines.append("Please review and provide a brief, actionable summary.")
    return "\n".join(lines)


def mark_task_done(text: str, task: ScheduledTask, now: datetime) -> str:
    """Replace `@once` or `@at ...` with `@done <timestamp>` in HEARTBEAT.md text."""
    timestamp = now.isoformat(timespec="minutes")
    desc_escaped = re.escape(task.description)
    schedule_escaped = re.escape(task.schedule)
    if task.needs_llm:
        pattern = re.compile(
            rf"^(- )`{schedule_escaped}`( {desc_escaped} `llm`)$",
            re.MULTILINE,
        )
    else:
        pattern = re.compile(
            rf"^(- )`{schedule_escaped}`( {desc_escaped})$",
            re.MULTILINE,
        )
    replacement = rf"\1`@done {timestamp}`\2"
    return pattern.sub(replacement, text, count=1)


def rebuild_file(
    managed_checks: str,
    managed_tasks: str,
    user_sections: dict[str, str],
) -> str:
    """Combine managed defaults with preserved user sections into complete file text."""
    user_checks = user_sections.get("User Checks", "")
    user_tasks = user_sections.get("User Tasks", "")

    sections = [
        "# Managed Checks (do not edit)\n" + managed_checks.strip(),
        "# User Checks\n" + (user_checks.strip() if user_checks.strip() else ""),
        "# Managed Tasks (do not edit)\n" + managed_tasks.strip(),
        "# User Tasks\n" + (user_tasks.strip() if user_tasks.strip() else ""),
    ]
    return "\n\n".join(sections) + "\n"


def evaluate_results(results: list[CheckResult]) -> HeartbeatReport:
    """Heuristic evaluation of check results.

    all_ok when every command exited 0 and produced no suspicious output.
    needs_llm when any item lacks a command, exited non-zero, or has output
    that looks like it needs interpretation.
    """
    needs_llm = False
    failures: list[str] = []

    for r in results:
        if r.item.command is None:
            needs_llm = True
            continue
        if r.exit_code != 0:
            needs_llm = True
            failures.append(f"{r.item.description}: exit {r.exit_code}")
        elif r.output.strip():
            pass

    all_ok = not needs_llm and not failures
    summary = "all checks passed" if all_ok else "; ".join(failures) if failures else "needs review"

    return HeartbeatReport(
        all_ok=all_ok,
        summary=summary,
        results=tuple(results),
        needs_llm=needs_llm,
    )


def build_llm_prompt(report: HeartbeatReport) -> str:
    """Build a focused prompt for the LLM with just the ambiguous results."""
    lines = [
        "You are reviewing heartbeat check results. Some need interpretation.",
        "For each result below, determine if it indicates a problem that needs the user's attention.",
        "Reply with a brief, actionable summary. If everything is fine, say so.",
        "",
    ]
    for r in report.results:
        lines.append(f"## {r.item.description}")
        if r.item.command:
            lines.append(f"Command: `{r.item.command}`")
            lines.append(f"Exit code: {r.exit_code}")
            lines.append(f"Output:\n```\n{r.output}\n```")
        else:
            lines.append("(No shell command — requires your assessment)")
        lines.append("")

    return "\n".join(lines)
