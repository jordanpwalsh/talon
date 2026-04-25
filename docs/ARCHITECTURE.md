# Talon Architecture

Talon is a personal AI assistant that runs locally and communicates via Telegram. It can execute shell commands, manage files, run periodic health checks, and schedule tasks — all orchestrated by an LLM selected through the inference adapter layer.

For a ports-and-adapters view, see [HEXAGONAL_ARCHITECTURE.md](/Users/jordan/devel/personal/talon/docs/HEXAGONAL_ARCHITECTURE.md).

## High-Level Architecture

The project follows **hexagonal architecture** (ports & adapters) with a **functional core, imperative shell** pattern. Each bounded context has:

- `domain/model.py` — Immutable data structures (frozen dataclasses)
- `domain/ports.py` — Protocol definitions (interfaces)
- `domain/evaluate.py` — Pure business logic functions (where applicable)
- `services/` — Orchestration layer that wires domain logic to I/O
- `adapters/` or `gateways/` — External system integrations

## Bounded Contexts

### Agent (`agent/`)

The agentic loop that powers LLM-driven tool use.

| Layer | File | Responsibility |
|-------|------|----------------|
| Domain | `domain/model.py` | `ToolDefinition`, `ToolCall`, `ToolResult`, `CompletionResult` |
| Domain | `domain/ports.py` | `InferencePort`, `ActivityIndicator` protocols |
| Service | `services/input_handler.py` | `handle_input()` — turn execution entrypoint, including slash-command routing |
| Service | `services/orchestrator.py` | `run_agent()` — loops: LLM call → tool dispatch → result → repeat (max 10 turns) |
| Tools | `tools/registry.py` | `ToolRegistry` — registers tool definitions + handler functions, dispatches by name |
| Tools | `tools/shell.py` | `run_command` tool — subprocess execution |
| Tools | `tools/filesystem.py` | `read_file`, `write_file`, `list_directory` tools |

### Conversation (`conversation/`)

Session and message management with immutable data structures.

| Layer | File | Responsibility |
|-------|------|----------------|
| Domain | `domain/model.py` | `Message`, `Conversation` (immutable tuple of messages plus session flags such as `verbose`) |
| Service | `services/session.py` | `InMemorySessionStore` — stores conversations keyed by chat ID |

### Inference (`inference/`)

LLM integration adapters that implement `InferencePort`.

| Layer | File | Responsibility |
|-------|------|----------------|
| Adapter | `adapters/openai_compatible.py` | `OpenAICompatibleAdapter` — generic adapter for OpenAI-compatible endpoints |
| Adapter | `adapters/openrouter.py` | `OpenRouterAdapter` — primary adapter, calls OpenRouter API (OpenAI-compatible) |
| Adapter | `adapters/claude_agent_sdk.py` | `ClaudeAgentSDKAdapter` — alternative adapter |

### Telegram Gateway (`gateways/telegram/`)

All Telegram I/O lives here — the only user-facing interface.

| File | Responsibility |
|------|----------------|
| `handlers.py` | `handle_start`, `handle_message` — entry points for Telegram events |
| `typing_indicator.py` | `TelegramTypingIndicator` — shows "typing..." in chat during processing |
| `heartbeat_delivery.py` | `TelegramHeartbeatDelivery` — implements `DeliveryPort` for heartbeat alerts |
| `formatting.py` | `md_to_telegram_html` — converts Markdown to Telegram-compatible HTML |

### Heartbeat (`heartbeat/`)

Periodic health checks and scheduled task execution.

| Layer | File | Responsibility |
|-------|------|----------------|
| Domain | `domain/model.py` | `CheckItem`, `HeartbeatReport`, `ScheduledTask`, `HeartbeatConfig` |
| Domain | `domain/ports.py` | `DeliveryPort` protocol |
| Domain | `domain/evaluate.py` | Pure functions: parse checks/tasks from HEARTBEAT.md, evaluate results |
| Service | `services/scheduler.py` | `scheduler_loop()` — 60s tick loop, checks active hours & intervals |
| Service | `services/runner.py` | `run_heartbeat()` — executes one check cycle (shell + optional LLM escalation) |
| Service | `services/task_runner.py` | `run_scheduled_tasks()` — runs due cron/once tasks, persists state |

### Skills (`skills/`)

Extensible skill system discovered at startup from `./skills/` and `~/.config/talon/skills/`.

Each skill is a directory containing a `SKILL.md` file with frontmatter (name, description) and markdown content. Skills are injected into the system prompt so the LLM knows about them.

## Data Flows

### User Message Flow

```
Telegram Message
  → gateways/telegram/handlers.handle_message()
  → Load Conversation from InMemorySessionStore
  → agent/services/input_handler.handle_input()
      ┌──────────────────────────────────────┐
      │  Turn Execution                      │
      │                                      │
      │  If slash command:                   │
      │    deterministic action or           │
      │    ephemeral model-backed command    │
      │  Else:                               │
      │    agent/services/orchestrator.run_agent()
      │      Conversation → InferencePort    │
      │           ↓                          │
      │      CompletionResult                │
      │           ↓                          │
      │      If tool_calls:                  │
      │        ToolRegistry.dispatch() each  │
      │        Append ToolResults            │
      │        Continue loop                 │
      │      Else:                           │
      │        Return final text             │
      └──────────────────────────────────────┘
  → Save Conversation to SessionStore when the turn mutates session state
  → Format response (Markdown → Telegram HTML)
  → Send reply to Telegram
```

### Heartbeat Flow

```
scheduler_loop() [60s tick]
  → Check active_hours & interval_minutes
  → If heartbeat due:
      → Read ~/.config/talon/HEARTBEAT.md
      → Parse checks (managed + user)
      → Run each check's shell command
      → evaluate_results() — heuristic pass/fail
      → If failures or ambiguous: agent/services/orchestrator.run_agent()
      → DeliveryPort.deliver() → Telegram gateway
  → If scheduled tasks due:
      → Parse tasks (cron, @once, @at)
      → Run shell command (if present)
      → Optionally invoke agent/services/orchestrator.run_agent()
      → DeliveryPort.deliver() → Telegram gateway
      → Persist state to heartbeat_state.json
```

## Entry Points

| File | Purpose |
|------|---------|
| `main.py` | Production startup — initializes all components, runs Telegram polling |
| `dev.py` | Development mode — watches `*.py` files and auto-restarts on changes |

`SIGHUP` triggers config reload (system prompt, heartbeat config) without restart.

## External Integrations

| System | Purpose | Config |
|--------|---------|--------|
| Telegram Bot API | User interface & alert delivery | `TELEGRAM_BOT_TOKEN`, `ALLOWED_TELEGRAM_IDS` |
| OpenRouter | Default LLM inference | `INFERENCE_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| OpenAI-compatible endpoint | Self-hosted or third-party chat completions API | `INFERENCE_PROVIDER=openai_compatible`, `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_MODEL` |
| Local filesystem | Skill discovery, heartbeat config, state | `~/.config/talon/` |
| Local shell | Tool execution (subprocess) | N/A |

## Design Principles

1. **Functional core, imperative shell** — Pure business logic (evaluate, parse, model) separated from I/O (adapters, gateways, tool handlers)
2. **Immutability** — `Conversation` is an immutable tuple; new instances created via `.append()`
3. **Protocol-based ports** — `InferencePort`, `DeliveryPort`, `ActivityIndicator` defined as Python Protocols for loose coupling
4. **Extensible tools** — New tools added by registering definition + handler in `ToolRegistry`
5. **Extensible skills** — Drop a `SKILL.md` in the skills directory; auto-discovered at startup
