from dataclasses import dataclass, field


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
class CompletionResult:
    text: str
    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
