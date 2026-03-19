from dataclasses import dataclass, field
from typing import Literal

from agent.domain.model import ToolCall


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class Conversation:
    messages: tuple[Message, ...] = ()
    session_id: str | None = None

    def append(self, message: Message) -> "Conversation":
        return Conversation(
            messages=self.messages + (message,),
            session_id=self.session_id,
        )

    def with_session_id(self, session_id: str) -> "Conversation":
        return Conversation(messages=self.messages, session_id=session_id)
