"""Agent orchestration loop — pure domain logic."""

import asyncio
import json

import structlog

from agent.domain.model import AgentEvent, CompletionResult, CompletionUsage, ToolCall, ToolResult
from agent.domain.ports import AgentEventSink, InferencePort
from agent.tools.registry import ToolRegistry
from conversation.domain.model import Conversation, Message

logger = structlog.get_logger()

MAX_TOOL_TURNS = 50


class AgentCancelled(Exception):
    pass


async def run_agent(
    inference: InferencePort,
    conversation: Conversation,
    tool_registry: ToolRegistry,
    cancel_event: asyncio.Event | None = None,
    verbose: bool = False,
    event_sink: AgentEventSink | None = None,
) -> tuple[str, Conversation]:
    """Run the agent loop for one user turn.

    Returns:
        (response_text, updated_conversation)

    Raises:
        AgentCancelled: if cancel_event is set during execution.
    """
    tools = tool_registry.definitions
    result = CompletionResult(text="(no response)", stop_reason="error")

    start_index = len(conversation.messages)

    for turn in range(MAX_TOOL_TURNS):
        if cancel_event and cancel_event.is_set():
            raise AgentCancelled()

        logger.info("agent_turn", turn=turn + 1)
        result: CompletionResult = await inference.complete(conversation, tools)

        if not result.tool_calls:
            conversation = conversation.append(
                Message(role="assistant", content=result.text)
            )
            await _publish_assistant_message(
                event_sink,
                result.text,
                usage=result.usage,
                reasoning=result.reasoning,
            )
            return _render_response_text(
                conversation=conversation,
                response_text=result.text,
                usage=result.usage,
                reasoning=result.reasoning,
                start_index=start_index,
                verbose=verbose,
                event_sink=event_sink,
            ), conversation

        # Adapter returned tool calls — dispatch them
        conversation = conversation.append(
            Message(role="assistant", content=result.text, tool_calls=result.tool_calls)
        )
        await _publish_assistant_message(
            event_sink,
            result.text,
            usage=result.usage,
            reasoning=result.reasoning,
        )
        for tc in result.tool_calls:
            if cancel_event and cancel_event.is_set():
                raise AgentCancelled()

            await _publish_tool_call(event_sink, tc)
            logger.info("tool_dispatch", tool=tc.name, args=tc.arguments)
            tr = tool_registry.dispatch(tc)
            logger.info("tool_result", tool=tc.name, is_error=tr.is_error, output=tr.output[:200])
            conversation = conversation.append(
                Message(role="tool", content=tr.output, tool_call_id=tr.tool_call_id)
            )
            await _publish_tool_result(event_sink, tc.name, tr)

    logger.warning("max_tool_turns", limit=MAX_TOOL_TURNS)
    response_text = result.text.strip() or build_tool_loop_fallback(conversation)
    conversation = conversation.append(
        Message(role="assistant", content=response_text)
    )
    await _publish_assistant_message(
        event_sink,
        response_text,
        usage=result.usage,
        reasoning=result.reasoning,
    )
    return _render_response_text(
        conversation=conversation,
        response_text=response_text,
        usage=result.usage,
        reasoning=result.reasoning,
        start_index=start_index,
        verbose=verbose,
        event_sink=event_sink,
    ), conversation


def _render_response_text(
    conversation: Conversation,
    response_text: str,
    usage: CompletionUsage | None,
    reasoning: str | None,
    start_index: int,
    verbose: bool,
    event_sink: AgentEventSink | None,
) -> str:
    if not verbose or event_sink is not None:
        return response_text
    return _format_verbose_transcript(
        conversation.messages[start_index:],
        usage=usage,
        reasoning=reasoning,
    )


def _format_verbose_transcript(
    messages: tuple[Message, ...],
    *,
    usage: CompletionUsage | None,
    reasoning: str | None,
) -> str:
    lines = ["Verbose transcript:"]

    for message in messages:
        if message.role == "assistant":
            if message.content.strip():
                lines.append("Assistant:")
                lines.append(message.content.strip())
            for tool_call in message.tool_calls:
                lines.append(f"Tool call: `{tool_call.name}`")
                lines.append(
                    "```json\n"
                    f"{json.dumps(tool_call.arguments, indent=2, sort_keys=True)}\n"
                    "```"
                )
        elif message.role == "tool":
            lines.append(f"Tool result [{message.tool_call_id or 'unknown'}]:")
            lines.append("```")
            lines.append(_truncate_tool_output(message.content.strip() or "(empty)"))
            lines.append("```")

    if len(lines) == 1:
        lines.append("(no agent turns recorded)")

    if usage is not None:
        lines.append("Usage:")
        lines.append(_format_usage_block(usage))

    if reasoning:
        lines.append("Reasoning:")
        lines.append("```")
        lines.append(_truncate_tool_output(reasoning.strip(), limit=2000))
        lines.append("```")

    return "\n\n".join(lines)


async def _publish_assistant_message(
    event_sink: AgentEventSink | None,
    text: str,
    usage: CompletionUsage | None,
    reasoning: str | None,
) -> None:
    if event_sink is None or (not text.strip() and usage is None and not reasoning):
        return
    await event_sink.publish(
        AgentEvent(
            kind="assistant_message",
            payload={
                "text": text,
                "usage": _usage_payload(usage),
                "reasoning": reasoning,
            },
        )
    )


async def _publish_tool_call(
    event_sink: AgentEventSink | None,
    tool_call: ToolCall,
) -> None:
    if event_sink is None:
        return
    await event_sink.publish(
        AgentEvent(
            kind="tool_call",
            payload={
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "arguments": tool_call.arguments,
            },
        )
    )


async def _publish_tool_result(
    event_sink: AgentEventSink | None,
    tool_name: str,
    tool_result: ToolResult,
) -> None:
    if event_sink is None or not tool_result.is_error:
        return
    await event_sink.publish(
        AgentEvent(
            kind="tool_result",
            payload={
                "tool_call_id": tool_result.tool_call_id,
                "tool_name": tool_name,
                "output": tool_result.output,
                "is_error": tool_result.is_error,
            },
        )
    )


def build_tool_loop_fallback(conversation: Conversation) -> str:
    recent_tool_calls: list[str] = []
    for message in reversed(conversation.messages):
        if message.role != "assistant":
            continue
        for tool_call in reversed(message.tool_calls):
            recent_tool_calls.append(tool_call.name)
            if len(recent_tool_calls) == 5:
                break
        if len(recent_tool_calls) == 5:
            break

    if recent_tool_calls:
        tool_summary = ", ".join(reversed(recent_tool_calls))
        return (
            f"I hit the tool-turn limit after {MAX_TOOL_TURNS} turns without reaching "
            f"a final answer. Recent tool calls: {tool_summary}. "
            "This usually means the model kept gathering context instead of answering. "
            "Try narrowing the request or specifying exactly what output you want."
        )

    return (
        f"I hit the tool-turn limit after {MAX_TOOL_TURNS} turns without reaching "
        "a final answer. Try narrowing the request or specifying exactly what "
        "output you want."
    )


def _truncate_tool_output(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len("\n[truncated]")] + "\n[truncated]"


def _usage_payload(usage: CompletionUsage | None) -> dict | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_prompt_tokens": usage.cached_prompt_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
    }


def _format_usage_block(usage: CompletionUsage) -> str:
    return (
        "```json\n"
        f"{json.dumps(_usage_payload(usage), indent=2, sort_keys=True)}\n"
        "```"
    )
