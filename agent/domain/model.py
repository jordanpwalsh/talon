from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    name: str
    output: str
    is_error: bool = False


@dataclass(frozen=True)
class CompletionUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class CompletionResult:
    text: str
    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: CompletionUsage | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["assistant_message", "tool_call", "tool_result"]
    payload: dict[str, Any] = field(default_factory=dict)
