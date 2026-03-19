from typing import Protocol

from agent.domain.model import CompletionResult, ToolDefinition
from conversation.domain.model import Conversation


class InferencePort(Protocol):
    async def complete(
        self,
        conversation: Conversation,
        tools: list[ToolDefinition],
    ) -> CompletionResult: ...


class ActivityIndicator(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
