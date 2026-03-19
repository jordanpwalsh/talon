"""Agent orchestration loop — pure domain logic."""

import asyncio

import structlog

from agent.domain.model import CompletionResult
from agent.domain.ports import InferencePort
from agent.tools.registry import ToolRegistry
from conversation.domain.model import Conversation, Message

logger = structlog.get_logger()

MAX_TOOL_TURNS = 10


class AgentCancelled(Exception):
    pass


async def run_agent(
    inference: InferencePort,
    conversation: Conversation,
    tool_registry: ToolRegistry,
    cancel_event: asyncio.Event | None = None,
) -> tuple[str, Conversation]:
    """Run the agent loop for one user turn.

    Returns:
        (response_text, updated_conversation)

    Raises:
        AgentCancelled: if cancel_event is set during execution.
    """
    tools = tool_registry.definitions
    result = CompletionResult(text="(no response)", stop_reason="error")

    for turn in range(MAX_TOOL_TURNS):
        if cancel_event and cancel_event.is_set():
            raise AgentCancelled()

        logger.info("agent_turn", turn=turn + 1)
        result: CompletionResult = await inference.complete(conversation, tools)

        if not result.tool_calls:
            conversation = conversation.append(
                Message(role="assistant", content=result.text)
            )
            return result.text, conversation

        # Adapter returned tool calls — dispatch them
        conversation = conversation.append(
            Message(role="assistant", content=result.text, tool_calls=result.tool_calls)
        )
        for tc in result.tool_calls:
            if cancel_event and cancel_event.is_set():
                raise AgentCancelled()

            logger.info("tool_dispatch", tool=tc.name, args=tc.arguments)
            tr = tool_registry.dispatch(tc)
            logger.info("tool_result", tool=tc.name, is_error=tr.is_error, output=tr.output[:200])
            conversation = conversation.append(
                Message(role="tool", content=tr.output, tool_call_id=tr.tool_call_id)
            )

    logger.warning("max_tool_turns", limit=MAX_TOOL_TURNS)
    return result.text, conversation
