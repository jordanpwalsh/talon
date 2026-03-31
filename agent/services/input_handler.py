"""User input handling for normal turns and slash commands."""

from dataclasses import dataclass
import json
import re

import structlog

from agent.domain.model import ToolDefinition
from agent.domain.ports import AgentEventSink, InferencePort
from agent.services.orchestrator import run_agent
from agent.tools.registry import ToolRegistry
from conversation.domain.model import CompactionStats, Conversation, Message

logger = structlog.get_logger()
SUMMARY_HEADER = "=== CONVERSATION SUMMARY ==="
ORIGINAL_MESSAGE_COUNT_RE = re.compile(r"^Original message count:\s*(\d+)\s*$", re.MULTILINE)
COMPRESSION_TIMESTAMP_RE = re.compile(r"^Compression timestamp:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SlashSkill:
    name: str
    description: str
    content: str


@dataclass(frozen=True)
class InputResult:
    response_text: str
    conversation: Conversation
    persist: bool = True


async def handle_input(
    inference: InferencePort,
    conversation: Conversation,
    raw_input: str,
    tool_registry: ToolRegistry,
    skills: list[SlashSkill] | None = None,
    cancel_event=None,
    event_sink: AgentEventSink | None = None,
) -> InputResult:
    """Handle one raw user input, including slash commands."""
    if raw_input.startswith("/"):
        command_result = await _handle_slash_command(
            inference=inference,
            conversation=conversation,
            raw_input=raw_input,
            skills=skills or [],
            tool_registry=tool_registry,
            cancel_event=cancel_event,
            event_sink=event_sink,
        )
        if command_result is not None:
            return command_result

    next_conversation = conversation.append(Message(role="user", content=raw_input))
    response_text, next_conversation = await run_agent(
        inference,
        next_conversation,
        tool_registry,
        cancel_event,
        verbose=conversation.verbose,
        event_sink=event_sink,
    )
    return InputResult(response_text=response_text, conversation=next_conversation)


async def _handle_slash_command(
    inference: InferencePort,
    conversation: Conversation,
    raw_input: str,
    skills: list[SlashSkill],
    tool_registry: ToolRegistry,
    cancel_event=None,
    event_sink: AgentEventSink | None = None,
) -> InputResult | None:
    command, _, args = raw_input[1:].partition(" ")
    command = command.strip().lower()
    args = args.strip()

    if not command:
        return InputResult(
            response_text=_available_commands_text(skills),
            conversation=conversation,
            persist=False,
        )

    if command == "reset":
        return InputResult(
            response_text="Context reset.",
            conversation=Conversation(
                session_id=conversation.session_id,
                verbose=conversation.verbose,
            ),
        )

    if command == "context":
        return await _run_context_command(inference, conversation, tool_registry)

    if command == "help":
        return InputResult(
            response_text=_available_commands_text(skills),
            conversation=conversation,
            persist=False,
        )

    if command == "debug":
        return _run_debug_command(inference, args, conversation)

    if command == "verbose":
        return _run_verbose_command(conversation, args)

    for skill in skills:
        if skill.name.lower() == command:
            return await _run_skill_command(
                inference,
                conversation,
                skill,
                args,
                tool_registry,
                cancel_event,
                event_sink,
            )

    return InputResult(
        response_text=f"Unknown slash command: /{command}\n\n{_available_commands_text(skills)}",
        conversation=conversation,
        persist=False,
    )


async def _run_context_command(
    inference: InferencePort,
    conversation: Conversation,
    tool_registry: ToolRegistry,
) -> InputResult:
    return InputResult(
        response_text=_conversation_stats(
            conversation,
            system_prompt=_resolve_system_prompt(inference),
            tools=tool_registry.definitions,
        ),
        conversation=conversation,
        persist=False,
    )


async def _run_skill_command(
    inference: InferencePort,
    conversation: Conversation,
    skill: SlashSkill,
    args: str,
    tool_registry: ToolRegistry,
    cancel_event=None,
    event_sink: AgentEventSink | None = None,
) -> InputResult:
    if not args:
        return InputResult(
            response_text=_skill_usage_text(skill),
            conversation=conversation,
            persist=False,
        )

    prompt = (
        f"You are manually invoking the skill `{skill.name}`.\n\n"
        f"Skill description: {skill.description or '(none)'}\n\n"
        "Follow the skill instructions below for this one response.\n"
        "Treat this as ephemeral command execution, not a continuation of the live chat history.\n\n"
        f"Skill contents:\n{skill.content}\n\n"
        f"User request:\n{args}"
    )
    response_text, _ = await run_agent(
        inference,
        Conversation(messages=(Message(role="user", content=prompt),)),
        tool_registry,
        cancel_event,
        verbose=conversation.verbose,
        event_sink=event_sink,
    )
    return InputResult(
        response_text=response_text,
        conversation=conversation,
        persist=False,
    )


def _available_commands_text(skills: list[SlashSkill]) -> str:
    lines = [
        "Available slash commands:",
        "/reset - clear the current conversation context",
        "/context - inspect the current conversation and summarize it",
        "/debug - inspect the last raw inference request/response snapshot",
        "/verbose [on|off] - toggle full agent-turn output for debugging",
    ]
    if skills:
        lines.append("")
        lines.append("Skill commands:")
        for skill in skills:
            description = skill.description or "manual skill invocation"
            lines.append(f"/{skill.name} - {description}")
    return "\n".join(lines)


def _skill_usage_text(skill: SlashSkill) -> str:
    lines = [f"/{skill.name}"]
    if skill.description:
        lines.append(skill.description)
    lines.extend(
        [
            "",
            "Usage:",
            f"/{skill.name} <instructions>",
        ]
    )
    return "\n".join(lines)


def _run_verbose_command(conversation: Conversation, args: str) -> InputResult:
    normalized = args.lower()
    if normalized in {"", "on"}:
        return InputResult(
            response_text=(
                "Verbose mode enabled. Future responses will include all assistant "
                "turns, tool calls, and tool results."
            ),
            conversation=conversation.with_verbose(True),
        )

    if normalized == "off":
        return InputResult(
            response_text="Verbose mode disabled.",
            conversation=conversation.with_verbose(False),
        )

    return InputResult(
        response_text="Usage: /verbose [on|off]",
        conversation=conversation,
        persist=False,
    )


def _run_debug_command(
    inference: InferencePort,
    args: str,
    conversation: Conversation,
) -> InputResult:
    mode = args.strip().lower()
    getter = getattr(inference, "get_last_debug_snapshot", None)
    if getter is None:
        return InputResult(
            response_text="Debug snapshot is not available for the current inference adapter.",
            conversation=conversation,
            persist=False,
        )

    snapshot = getter()
    if not snapshot:
        return InputResult(
            response_text="No inference snapshot has been captured yet. Send a normal message first.",
            conversation=conversation,
            persist=False,
        )

    if mode == "raw":
        response_text = (
            "Last inference snapshot:\n\n"
            "```json\n"
            f"{json.dumps(snapshot, indent=2, sort_keys=True)}\n"
            "```"
        )
    else:
        response_text = _format_debug_snapshot(snapshot)
    return InputResult(
        response_text=response_text,
        conversation=conversation,
        persist=False,
    )


def _conversation_stats(
    conversation: Conversation,
    *,
    system_prompt: str,
    tools: list[ToolDefinition],
) -> str:
    messages = conversation.messages
    counts = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    chars_by_role = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    message_content_chars = 0
    total_tool_calls = 0
    tool_call_args_chars = 0
    compaction_stats = _resolve_compaction_stats(conversation)
    system_prompt_chars = len(system_prompt)
    tool_schema_chars = sum(len(_tool_schema_payload(tool)) for tool in tools)

    sections: list[tuple[str, int]] = []
    if system_prompt_chars:
        sections.append(("System prompt", system_prompt_chars))

    summary_lines = [
        "Context stats",
        f"- Session ID: {conversation.session_id or '(none)'}",
        f"- Message count: {len(messages)}",
    ]

    for message in messages:
        counts[message.role] += 1
        message_chars = len(message.content)
        chars_by_role[message.role] += message_chars
        message_content_chars += message_chars
        total_tool_calls += len(message.tool_calls)
        for tool_call in message.tool_calls:
            tool_call_args_chars += len(_tool_call_arguments_payload(tool_call.arguments))

    for role in ("system", "user", "assistant", "tool"):
        role_chars = chars_by_role[role]
        if role_chars:
            sections.append((f"{role} message content", role_chars))

    if tool_schema_chars:
        sections.append(("Tool schemas", tool_schema_chars))
    if tool_call_args_chars:
        sections.append(("Tool-call argument JSON", tool_call_args_chars))

    total_prompt_chars = sum(chars for _, chars in sections)
    total_prompt_tokens = _estimate_tokens_from_chars(total_prompt_chars)

    summary_lines.extend(
        [
            f"- Estimated prompt chars tracked: {total_prompt_chars}",
            f"- Estimated prompt tokens tracked (chars/4): {total_prompt_tokens}",
            f"- System prompt chars: {system_prompt_chars}",
            f"- Conversation message chars: {message_content_chars}",
            f"- Tool schema chars: {tool_schema_chars}",
            f"- Tool-call argument chars: {tool_call_args_chars}",
            f"- Total tools available: {len(tools)}",
            f"- Tool calls recorded: {total_tool_calls}",
            f"- User messages: {counts['user']}",
            f"- Assistant messages: {counts['assistant']}",
            f"- System messages: {counts['system']}",
            f"- Tool messages: {counts['tool']}",
            "- Rough token breakdown by role:",
            f"  - User: chars={chars_by_role['user']} rough_tokens={_estimate_tokens_from_chars(chars_by_role['user'])}",
            f"  - Assistant: chars={chars_by_role['assistant']} rough_tokens={_estimate_tokens_from_chars(chars_by_role['assistant'])}",
            f"  - System: chars={chars_by_role['system']} rough_tokens={_estimate_tokens_from_chars(chars_by_role['system'])}",
            f"  - Tool: chars={chars_by_role['tool']} rough_tokens={_estimate_tokens_from_chars(chars_by_role['tool'])}",
            "- Estimated prompt composition:",
        ]
    )

    if sections:
        for label, chars in sections:
            summary_lines.append(
                f"  - {label}: chars={chars} rough_tokens={_estimate_tokens_from_chars(chars)} "
                f"pct={_format_percent(chars, total_prompt_chars)}"
            )
    else:
        summary_lines.append("  - (empty)")

    summary_lines.extend(
        [
            "- Prompt accounting notes:",
            "  - Includes the configured system prompt, all conversation message content, tool schemas, and assistant tool-call argument JSON",
            "  - Percentages are based on tracked prompt chars, so they reflect relative prompt footprint",
            "  - Provider-side framing and any hidden server formatting are still excluded",
            "- Compaction stats:",
            f"  - Count: {compaction_stats.count}",
            f"  - Messages compacted: {compaction_stats.messages_compacted}",
            f"  - Rough tokens saved: {compaction_stats.estimated_tokens_saved}",
            f"  - Last compaction time: {compaction_stats.last_compaction_at or '(never)'}",
            "",
            "Context synopsis:",
        ]
    )

    if not messages:
        summary_lines.append("- The conversation is empty.")
    else:
        summary_lines.extend(_conversation_synopsis_lines(messages))

    summary_lines.extend(
        [
            "",
            "Messages:",
        ]
    )

    if not messages:
        summary_lines.append("- (empty)")
        return "\n".join(summary_lines)

    for index, message in enumerate(messages, start=1):
        summary_lines.append(
            f"- #{index} role={message.role} chars={len(message.content)} "
            f"tool_calls={len(message.tool_calls)} content={_message_preview(message)}"
        )

    return "\n".join(summary_lines)


def _resolve_system_prompt(inference: InferencePort) -> str:
    prompt = getattr(inference, "_system_prompt", "")
    return prompt if isinstance(prompt, str) else ""


def _tool_schema_payload(tool: ToolDefinition) -> str:
    return json.dumps(
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        },
        sort_keys=True,
    )


def _tool_call_arguments_payload(arguments: dict) -> str:
    return json.dumps(arguments, sort_keys=True)


def _estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, chars // 4)


def _format_percent(part: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(part / total) * 100:.1f}%"


def _conversation_synopsis_lines(messages: tuple[Message, ...]) -> list[str]:
    user_previews = [_message_preview(message) for message in messages if message.role == "user"]
    assistant_previews = [_message_preview(message) for message in messages if message.role == "assistant"]
    tool_messages = [message for message in messages if message.role == "tool"]

    lines: list[str] = []
    if user_previews:
        lines.append(f"- Latest user request: {user_previews[-1]}")
    if len(user_previews) > 1:
        lines.append(f"- Earlier user context: {user_previews[-2]}")
    if assistant_previews:
        lines.append(f"- Latest assistant output: {assistant_previews[-1]}")
    if tool_messages:
        lines.append(
            f"- Tool activity: {len(tool_messages)} tool result message(s) present, "
            "which may dominate prompt size."
        )
    if not lines:
        lines.append("- No user or assistant turns are present yet.")
    return lines


def _message_preview(message: Message, limit: int = 160) -> str:
    content = " ".join(message.content.split())
    if not content:
        return "(empty)"
    if len(content) <= limit:
        return content
    return f"{content[: limit - 3]}..."


def _format_debug_snapshot(snapshot: dict) -> str:
    request = snapshot.get("request", {})
    response = snapshot.get("response", {})

    request_messages = request.get("messages", [])
    response_choices = response.get("choices", [])
    choice0 = response_choices[0] if response_choices else {}
    message = choice0.get("message", {}) if isinstance(choice0, dict) else {}
    usage = response.get("usage", {})

    lines = [
        "Last inference snapshot (compact)",
        f"- Provider: {snapshot.get('provider', '(unknown)')}",
        f"- Model: {request.get('model', '(unknown)')}",
        f"- Request message count: {len(request_messages)}",
        f"- Request tools count: {len(request.get('tools', []))}",
        f"- Finish reason: {choice0.get('finish_reason', '(unknown)')}",
    ]

    if usage:
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        prompt_details = usage.get("prompt_tokens_details", {}) or {}
        completion_details = usage.get("completion_tokens_details", {}) or {}
        lines.extend(
            [
                "- Usage:",
                f"  - prompt_tokens={prompt_tokens}",
                f"  - completion_tokens={completion_tokens}",
                f"  - total_tokens={total_tokens}",
                f"  - cached_prompt_tokens={prompt_details.get('cached_tokens', 0)}",
                f"  - reasoning_tokens={completion_details.get('reasoning_tokens', 0)}",
            ]
        )

    if request_messages:
        lines.extend(
            [
                "- Last request messages:",
            ]
        )
        for index, item in enumerate(request_messages[-3:], start=max(1, len(request_messages) - 2)):
            role = item.get("role", "(unknown)")
            content = _preview_text(_extract_message_content(item))
            lines.append(f"  - #{index} role={role} content={content}")

    tool_calls = message.get("tool_calls", []) if isinstance(message, dict) else []
    if tool_calls:
        lines.append("- Tool calls returned:")
        for tool_call in tool_calls[:5]:
            function = tool_call.get("function", {})
            lines.append(
                f"  - name={function.get('name', '(unknown)')} "
                f"arguments={_preview_text(function.get('arguments', ''))}"
            )
    else:
        lines.append(f"- Assistant content: {_preview_text(message.get('content', ''))}")

    reasoning = _extract_debug_reasoning(message)
    if reasoning:
        lines.append(f"- Reasoning: {_preview_text(reasoning, limit=600)}")

    response_extra_keys = sorted(
        key for key in response.keys() if key not in {"choices", "usage"}
    )
    if response_extra_keys:
        lines.append(f"- Response top-level keys: {', '.join(response_extra_keys[:12])}")

    lines.append("")
    lines.append("Use `/debug raw` for the full JSON snapshot.")
    return "\n".join(lines)


def _extract_message_content(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, sort_keys=True)


def _extract_debug_reasoning(message: dict) -> str:
    for key in ("reasoning", "reasoning_content", "thinking"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value:
            return json.dumps(value, sort_keys=True)

    for container_key in ("model_extra",):
        container = message.get(container_key, {})
        if not isinstance(container, dict):
            continue
        for key in ("reasoning", "reasoning_details", "reasoning_content", "thinking"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value:
                return json.dumps(value, sort_keys=True)

    return ""


def _preview_text(value: str, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    if not text:
        return "(empty)"
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _resolve_compaction_stats(conversation: Conversation) -> CompactionStats:
    if conversation.compaction_stats.count > 0:
        return conversation.compaction_stats
    return _infer_legacy_compaction_stats(conversation.messages)


def _infer_legacy_compaction_stats(messages: tuple[Message, ...]) -> CompactionStats:
    count = 0
    messages_compacted = 0
    last_compaction_at: str | None = None

    for message in messages:
        if message.role != "system" or SUMMARY_HEADER not in message.content:
            continue

        count += 1

        original_match = ORIGINAL_MESSAGE_COUNT_RE.search(message.content)
        if original_match is not None:
            messages_compacted += int(original_match.group(1))

        timestamp_match = COMPRESSION_TIMESTAMP_RE.search(message.content)
        if timestamp_match is not None:
            last_compaction_at = timestamp_match.group(1).strip()

    return CompactionStats(
        count=count,
        messages_compacted=messages_compacted,
        estimated_tokens_saved=0,
        last_compaction_at=last_compaction_at,
    )
