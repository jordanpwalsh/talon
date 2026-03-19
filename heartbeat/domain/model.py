from dataclasses import dataclass, field
from pathlib import Path

from config import CONFIG_DIR


@dataclass(frozen=True)
class CheckItem:
    description: str
    command: str | None = None  # shell command, None = always needs LLM


@dataclass(frozen=True)
class CheckResult:
    item: CheckItem
    output: str
    exit_code: int


@dataclass(frozen=True)
class HeartbeatReport:
    all_ok: bool
    summary: str
    results: tuple[CheckResult, ...]
    needs_llm: bool


@dataclass(frozen=True)
class ScheduledTask:
    schedule: str         # cron expr, "@once", or "@at <ISO timestamp>"
    description: str
    command: str | None   # shell command, optional
    needs_llm: bool       # explicit `llm` marker
    is_once: bool         # @once or @at task (mark done after running)


@dataclass(frozen=True)
class HeartbeatConfig:
    enabled: bool = True
    interval_minutes: int = 30
    active_hours: tuple[int, int] = (8, 22)
    checklist_path: Path = CONFIG_DIR / "HEARTBEAT.md"
    state_path: Path = CONFIG_DIR / "heartbeat_state.json"
    delivery_chat_ids: list[int] = field(default_factory=list)
    always_notify: bool = False
