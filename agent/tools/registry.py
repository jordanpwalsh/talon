from typing import Callable

from agent.domain.model import ToolCall, ToolDefinition, ToolResult

ToolHandler = Callable[[dict], str]


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: list[ToolDefinition] = []
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self._definitions.append(definition)
        self._handlers[definition.name] = handler

    @property
    def definitions(self) -> list[ToolDefinition]:
        return list(self._definitions)

    def dispatch(self, tool_call: ToolCall) -> ToolResult:
        handler = self._handlers.get(tool_call.name)
        if handler is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=f"Unknown tool: {tool_call.name}",
                is_error=True,
            )
        try:
            output = handler(tool_call.arguments)
        except Exception as e:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                output=str(e),
                is_error=True,
            )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            output=output,
        )
