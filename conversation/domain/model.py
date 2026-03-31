from dataclasses import dataclass, field
from typing import Literal

from agent.domain.model import ToolCall


@dataclass(frozen=True)
class CompactionStats:
    count: int = 0
    messages_compacted: int = 0
    estimated_tokens_saved: int = 0
    last_compaction_at: str | None = None

    def record(
        self,
        *,
        messages_compacted: int,
        estimated_tokens_saved: int,
        compacted_at: str,
    ) -> "CompactionStats":
        return CompactionStats(
            count=self.count + 1,
            messages_compacted=self.messages_compacted + messages_compacted,
            estimated_tokens_saved=self.estimated_tokens_saved + estimated_tokens_saved,
            last_compaction_at=compacted_at,
        )


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
    verbose: bool = True
    compaction_stats: CompactionStats = field(default_factory=CompactionStats)

    def append(self, message: Message) -> "Conversation":
        return Conversation(
            messages=self.messages + (message,),
            session_id=self.session_id,
            verbose=self.verbose,
            compaction_stats=self.compaction_stats,
        )

    def with_session_id(self, session_id: str) -> "Conversation":
        return Conversation(
            messages=self.messages,
            session_id=session_id,
            verbose=self.verbose,
            compaction_stats=self.compaction_stats,
        )

    def with_verbose(self, verbose: bool) -> "Conversation":
        return Conversation(
            messages=self.messages,
            session_id=self.session_id,
            verbose=verbose,
            compaction_stats=self.compaction_stats,
        )

    def with_compaction_stats(self, compaction_stats: CompactionStats) -> "Conversation":
        return Conversation(
            messages=self.messages,
            session_id=self.session_id,
            verbose=self.verbose,
            compaction_stats=compaction_stats,
        )
