"""Session compaction service for managing long conversations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import structlog

from agent.domain.ports import InferencePort
from conversation.domain.model import Conversation, Message

logger = structlog.get_logger()


@dataclass(frozen=True)
class CompactionConfig:
    """Configuration for session compaction behavior."""
    
    # Trigger compaction when message count exceeds this threshold
    max_messages: int = 50
    
    # Keep this many recent messages uncompacted
    preserve_recent: int = 20
    
    # Maximum tokens in the compressed summary (rough estimate)
    max_summary_tokens: int = 1000
    
    # Compress in chunks of this size for better context preservation
    compression_chunk_size: int = 10


class SessionCompactor(Protocol):
    """Protocol for session compaction implementations."""
    
    async def should_compact(self, conversation: Conversation) -> bool:
        """Check if conversation needs compaction."""
        ...
    
    async def compact(self, conversation: Conversation) -> Conversation:
        """Compact the conversation and return a new instance."""
        ...


class LLMSessionCompactor:
    """Compacts conversations using LLM summarization."""
    
    def __init__(self, inference: InferencePort, config: CompactionConfig = None):
        self._inference = inference
        self._config = config or CompactionConfig()
    
    async def should_compact(self, conversation: Conversation) -> bool:
        """Check if conversation exceeds the message threshold."""
        return len(conversation.messages) > self._config.max_messages
    
    async def compact(self, conversation: Conversation) -> Conversation:
        """Compact the conversation by summarizing older messages."""
        if not await self.should_compact(conversation):
            return conversation
        
        messages = list(conversation.messages)
        
        # Split into old messages to compress and recent messages to preserve
        split_point = len(messages) - self._config.preserve_recent
        if split_point <= 0:
            return conversation
        
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]
        compacted_at = self._get_timestamp()
        
        logger.info(
            "compacting_conversation",
            total_messages=len(messages),
            compacting=len(old_messages),
            preserving=len(recent_messages),
        )
        
        # Generate summary of old messages
        summary = await self._generate_summary(old_messages, compacted_at)
        
        # Create new conversation with summary + recent messages
        summary_message = Message(role="system", content=summary)
        new_messages = (summary_message,) + tuple(recent_messages)
        previous_chars = sum(len(message.content) for message in old_messages)
        replacement_chars = len(summary)
        estimated_tokens_saved = max(0, (previous_chars - replacement_chars) // 4)
        updated_stats = conversation.compaction_stats.record(
            messages_compacted=len(old_messages),
            estimated_tokens_saved=estimated_tokens_saved,
            compacted_at=compacted_at,
        )
        
        return Conversation(
            messages=new_messages,
            session_id=conversation.session_id,
            verbose=conversation.verbose,
            compaction_stats=updated_stats,
        )
    
    async def _generate_summary(self, messages: list[Message], compacted_at: str) -> str:
        """Generate a comprehensive summary of message history."""
        
        # Group messages into chunks for better context preservation
        chunks = self._chunk_messages(messages)
        chunk_summaries = []
        
        for i, chunk in enumerate(chunks):
            chunk_summary = await self._summarize_chunk(chunk, i + 1, len(chunks))
            chunk_summaries.append(chunk_summary)
        
        # Combine chunk summaries into final summary
        if len(chunk_summaries) == 1:
            final_summary = chunk_summaries[0]
        else:
            final_summary = await self._combine_summaries(chunk_summaries)
        
        return (
            "=== CONVERSATION SUMMARY ===\n"
            "This is a compressed summary of earlier conversation history.\n"
            f"Original message count: {len(messages)}\n"
            f"Compression timestamp: {compacted_at}\n\n"
            f"{final_summary}\n"
            "=== END SUMMARY ==="
        )
    
    def _chunk_messages(self, messages: list[Message]) -> list[list[Message]]:
        """Split messages into chunks for processing."""
        chunks = []
        chunk_size = self._config.compression_chunk_size
        
        for i in range(0, len(messages), chunk_size):
            chunk = messages[i:i + chunk_size]
            chunks.append(chunk)
        
        return chunks
    
    async def _summarize_chunk(self, messages: list[Message], chunk_num: int, total_chunks: int) -> str:
        """Summarize a chunk of messages."""
        context = self._format_messages_for_summary(messages)
        
        prompt = (
            f"Summarize this conversation chunk ({chunk_num}/{total_chunks}).\n\n"
            "Focus on:\n"
            "- Key topics and decisions\n"
            "- Important context and state changes\n" 
            "- User goals and preferences\n"
            "- Tool usage and results\n"
            "- Any ongoing threads or unresolved issues\n\n"
            "Be concise but preserve important details that might be referenced later.\n\n"
            f"Messages to summarize:\n{context}"
        )
        
        summary_conversation = Conversation(messages=(Message(role="user", content=prompt),))
        result = await self._inference.complete(summary_conversation, tools=[])
        
        return result.text.strip()
    
    async def _combine_summaries(self, summaries: list[str]) -> str:
        """Combine multiple chunk summaries into a coherent final summary."""
        combined_text = "\n\n".join(f"Chunk {i+1}:\n{summary}" for i, summary in enumerate(summaries))
        
        prompt = (
            "Combine these conversation chunk summaries into a single coherent summary.\n\n"
            "Merge related topics, eliminate redundancy, and maintain chronological flow.\n"
            "Preserve all important context, decisions, and ongoing threads.\n\n"
            f"Chunk summaries:\n{combined_text}"
        )
        
        summary_conversation = Conversation(messages=(Message(role="user", content=prompt),))
        result = await self._inference.complete(summary_conversation, tools=[])
        
        return result.text.strip()
    
    def _format_messages_for_summary(self, messages: list[Message]) -> str:
        """Format messages for LLM summarization."""
        lines = []
        
        for i, msg in enumerate(messages):
            content = msg.content.strip()
            if len(content) > 500:  # Truncate very long messages
                content = content[:497] + "..."
            
            line = f"{i+1}. [{msg.role}] {content}"
            
            if msg.tool_calls:
                tools = ", ".join(tc.name for tc in msg.tool_calls)
                line += f" (tool calls: {tools})"
            
            lines.append(line)
        
        return "\n".join(lines)
    
    def _get_timestamp(self) -> str:
        """Get current timestamp for summary metadata."""
        return datetime.now().isoformat()


class RollingWindowCompactor:
    """Simple rolling window compactor that keeps only recent messages."""
    
    def __init__(self, config: CompactionConfig = None):
        self._config = config or CompactionConfig()
    
    async def should_compact(self, conversation: Conversation) -> bool:
        return len(conversation.messages) > self._config.max_messages
    
    async def compact(self, conversation: Conversation) -> Conversation:
        """Keep only the most recent messages."""
        if not await self.should_compact(conversation):
            return conversation
        
        messages = conversation.messages
        keep_count = self._config.preserve_recent
        
        if len(messages) <= keep_count:
            return conversation
        
        logger.info(
            "rolling_window_compaction",
            total_messages=len(messages),
            keeping=keep_count,
            dropping=len(messages) - keep_count,
        )
        
        recent_messages = messages[-keep_count:]
        dropped_messages = messages[:-keep_count]
        compacted_at = datetime.now().isoformat()
        dropped_chars = sum(len(message.content) for message in dropped_messages)
        updated_stats = conversation.compaction_stats.record(
            messages_compacted=len(dropped_messages),
            estimated_tokens_saved=max(0, dropped_chars // 4),
            compacted_at=compacted_at,
        )
        
        return Conversation(
            messages=recent_messages,
            session_id=conversation.session_id,
            verbose=conversation.verbose,
            compaction_stats=updated_stats,
        )
