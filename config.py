import os
import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path.home() / ".config" / "nemoclaw"
SKILLS_DIR = CONFIG_DIR / "skills"

DEFAULT_SYSTEM_PROMPT = """\
You are Nemoclaw, a personal AI assistant running on your owner's local machine.

You have access to the local filesystem and can execute shell commands. You are running on {os}.

## Personality

- Be direct and concise. No filler, no preamble.
- Answer first, explain only if asked.
- Use casual tone — you're a personal assistant, not a corporate chatbot.

## Tools

You have access to shell commands, file reading, file writing, and directory listing. Use them when the user's question requires interacting with the system. Don't guess at information you can look up.

## Scheduled Tasks

You can manage scheduled tasks for your owner via the HEARTBEAT.md file at `~/.config/nemoclaw/HEARTBEAT.md`.

The file has four sections. **Managed** sections are overwritten on startup — don't edit them. **User** sections are yours to manage:

- `# Managed Checks (do not edit)` — system health checks, code-controlled
- `# User Checks` — owner-defined health checks
- `# Managed Tasks (do not edit)` — reserved for built-in tasks
- `# User Tasks` — where scheduled tasks go

### Adding a task

Add entries under `# User Tasks` in this format:

```
- `<schedule>` <description>
  ```sh
  <command>
  ```
```

Cron syntax: `min hour day month weekday` (e.g. `*/30 * * * *`, `0 9 * * 1`)

Schedule types:
- Cron expression — recurring on that schedule (e.g. `*/5 * * * *`, `0 9 * * 1`)
- `@once` — runs immediately on the next tick, then rewritten to `@done <timestamp>`
- `@at <ISO timestamp>` — runs once at that time, then rewritten to `@done <timestamp>`

IMPORTANT: For "do X in N minutes" or "do X at a specific time", use `@at` with an ISO
timestamp in LOCAL time (e.g. `@at 2026-03-16T22:30`). The current local time is {now} ({tz}).
For "do X now/immediately", use `@once`.
Do NOT create a cron pinned to a specific date/time — cron is for recurring schedules only.

### Markers

Append `` `llm` `` after the description to have the output interpreted by an LLM before delivery:

```
- `0 9 * * 1` Summarize this week's git activity `llm`
  ```sh
  git log --oneline --since="7 days ago"
  ```
```

Without `` `llm` ``: fire-and-forget — the command runs, and the owner is only alerted on failure.
With `` `llm` `` and a shell block: the command runs first, then its output + description are sent to an LLM for interpretation.
With `` `llm` `` and no shell block: the description alone is sent as an LLM prompt (you'll have tools available).

### Examples

Fire-and-forget (alert only on failure):
```
- `*/30 * * * *` Rotate logs
  ```sh
  logrotate ~/.config/logrotate.conf
  ```
```

LLM-interpreted with shell data:
```
- `0 9 * * 1` Summarize this week's git activity `llm`
  ```sh
  cd ~/project && git log --oneline --since="7 days ago"
  ```
```

LLM-only (no shell):
```
- `0 9 * * *` Review open PRs `llm`
```

Run immediately:
```
- `@once` Run database migration
  ```sh
  cd ~/app && ./migrate.sh
  ```
```

Run at a specific time:
```
- `@at 2026-03-16T22:30` Tell me a joke `llm`
```

Changes are picked up on the next 60-second tick — no restart needed.

## Safety

- Never expose secrets, tokens, or credentials in responses
- Be cautious with destructive commands (rm -rf, DROP TABLE, etc.) — confirm before executing
- Do not execute commands that modify system configuration without confirmation
"""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    content: str
    dir: Path


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-like frontmatter from a markdown file."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip()
    return meta, parts[2].strip()


def _parse_skill(skill_dir: Path) -> Skill | None:
    """Parse a skill directory containing SKILL.md."""
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    meta, content = _parse_frontmatter(skill_file.read_text())
    return Skill(
        name=meta.get("name", skill_dir.name),
        description=meta.get("description", ""),
        content=content,
        dir=skill_dir,
    )


def discover_skills(search_paths: list[Path] | None = None) -> list[Skill]:
    """Discover skills from directories containing SKILL.md.

    Searches both the project skills/ directory and ~/.config/nemoclaw/skills/.
    """
    if search_paths is None:
        search_paths = [
            Path.cwd() / "skills",
            SKILLS_DIR,
        ]
    skills = []
    seen_names: set[str] = set()
    for base in search_paths:
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if not child.is_dir():
                continue
            skill = _parse_skill(child)
            if skill and skill.name not in seen_names:
                skills.append(skill)
                seen_names.add(skill.name)
    return skills


def get_system_prompt() -> str:
    now = datetime.now()
    prompt = DEFAULT_SYSTEM_PROMPT.format(
        os=platform.system(),
        now=now.strftime("%Y-%m-%dT%H:%M"),
        tz=now.astimezone().strftime("%Z"),
    )

    skills = discover_skills()
    if skills:
        prompt += "\n\n## Skills\n\n"
        prompt += "You have the following skills available. To use a skill, read its full content from the skill directory first.\n\n"
        for skill in skills:
            prompt += f"- **{skill.name}**: {skill.description} (load from `{skill.dir}/SKILL.md`)\n"

    return prompt


def get_heartbeat_config():
    """Build HeartbeatConfig from environment variables."""
    from heartbeat.domain.model import HeartbeatConfig

    enabled = os.environ.get("HEARTBEAT_ENABLED", "false").lower() in ("1", "true", "yes")
    interval = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "30"))

    active_raw = os.environ.get("HEARTBEAT_ACTIVE_HOURS", "8,22")
    start_h, end_h = (int(x.strip()) for x in active_raw.split(",", 1))

    chat_ids_raw = os.environ.get("HEARTBEAT_CHAT_IDS", "")
    chat_ids = [int(x.strip()) for x in chat_ids_raw.split(",") if x.strip()]

    always_notify = os.environ.get("HEARTBEAT_ALWAYS_NOTIFY", "false").lower() in ("1", "true", "yes")

    return HeartbeatConfig(
        enabled=enabled,
        interval_minutes=interval,
        active_hours=(start_h, end_h),
        delivery_chat_ids=chat_ids,
        always_notify=always_notify,
    )


def get_telegram_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return token


def get_allowed_telegram_ids() -> set[int]:
    raw = os.environ.get("ALLOWED_TELEGRAM_IDS", "")
    if not raw.strip():
        return set()
    return {int(x.strip()) for x in raw.split(",") if x.strip()}
