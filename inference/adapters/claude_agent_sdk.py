"""Smart adapter — wraps the Claude Agent SDK (authenticates via Max subscription)."""

import logging

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, query

from agent.domain.model import CompletionResult, ToolDefinition
from conversation.domain.model import Conversation

logger = logging.getLogger(__name__)


class ClaudeAgentSDKAdapter:
    """Anti-corruption layer around the Agent SDK.

    The SDK manages its own session state and tool loop internally.
    This adapter maps domain Conversations to SDK sessions and always
    returns a CompletionResult with no tool_calls (the SDK resolves them).
    """

    def __init__(self) -> None:
        # Maps domain session_id → SDK session_id
        self._sdk_sessions: dict[str, str] = {}

    async def complete(
        self,
        conversation: Conversation,
        tools: list[ToolDefinition],
    ) -> CompletionResult:
        # Build the user prompt from the latest user message
        user_messages = [m for m in conversation.messages if m.role == "user"]
        prompt = user_messages[-1].content if user_messages else ""

        # Resume existing SDK session or start new
        sdk_session_id = None
        if conversation.session_id:
            sdk_session_id = self._sdk_sessions.get(conversation.session_id)

        if sdk_session_id:
            options = ClaudeAgentOptions(resume=sdk_session_id, max_turns=3)
        else:
            options = ClaudeAgentOptions(allowed_tools=[], max_turns=3)

        logger.info(
            "Agent SDK call (sdk_session=%s, domain_session=%s)",
            sdk_session_id,
            conversation.session_id,
        )

        result_text = "(no response)"
        stop_reason = "end_turn"

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                new_sdk_id = message.data.get("session_id")
                if new_sdk_id and conversation.session_id:
                    self._sdk_sessions[conversation.session_id] = new_sdk_id
                    logger.info("SDK session mapped: %s → %s", conversation.session_id, new_sdk_id)
            elif isinstance(message, ResultMessage):
                result_text = message.result
                stop_reason = message.stop_reason or "end_turn"
                logger.info("Agent SDK finished (stop_reason=%s)", stop_reason)

        return CompletionResult(text=result_text, stop_reason=stop_reason)
