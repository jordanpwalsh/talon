# Nemoclaw Architecture

Nemoclaw is a personal AI assistant that runs locally and communicates via Telegram. It can execute shell commands, manage files, run periodic health checks, and schedule tasks — all orchestrated by an LLM (Claude Sonnet via OpenRouter).

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
| Service | `services/orchestrator.py` | `run_agent()` — loops: LLM call → tool dispatch → result → repeat (max 10 turns) |
| Tools | `tools/registry.py` | `ToolRegistry` — registers tool definitions + handler functions, dispatches by name |
| Tools | `tools/shell.py` | `run_command` tool — subprocess execution |
| Tools | `tools/filesystem.py` | `read_file`, `write_file`, `list_directory` tools |

### Conversation (`conversation/`)

Session and message management with immutable data structures.

| Layer | File | Responsibility |
|-------|------|----------------|
| Domain | `domain/model.py` | `Message`, `Conversation` (immutable tuple of messages, new instances via `.append()`) |
| Service | `services/session.py` | `InMemorySessionStore` — stores conversations keyed by chat ID |

### Inference (`inference/`)

LLM integration adapters that implement `InferencePort`.

| Layer | File | Responsibility |
|-------|------|----------------|
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

Extensible skill system discovered at startup from `./skills/` and `~/.config/nemoclaw/skills/`.

Each skill is a directory containing a `SKILL.md` file with frontmatter (name, description) and markdown content. Skills are injected into the system prompt so the LLM knows about them.

## Data Flows

### User Message Flow

```
Telegram Message
  → gateways/telegram/handlers.handle_message()
  → Load Conversation from InMemorySessionStore
  → agent/services/orchestrator.run_agent()
      ┌──────────────────────────────────────┐
      │  Agentic Loop (max 10 turns)         │
      │                                      │
      │  Conversation → InferencePort        │
      │       ↓                              │
      │  CompletionResult                    │
      │       ↓                              │
      │  If tool_calls:                      │
      │    ToolRegistry.dispatch() each      │
      │    Append ToolResults to Conversation │
      │    Continue loop                     │
      │  Else:                               │
      │    Return final text                 │
      └──────────────────────────────────────┘
  → Save Conversation to SessionStore
  → Format response (Markdown → Telegram HTML)
  → Send reply to Telegram
```

### Heartbeat Flow

```
scheduler_loop() [60s tick]
  → Check active_hours & interval_minutes
  → If heartbeat due:
      → Read ~/.config/nemoclaw/HEARTBEAT.md
      → Parse checks (managed + user)
      → Run each check's shell command
      → evaluate_results() — heuristic pass/fail
      → If failures or ambiguous: escalate to LLM
      → DeliveryPort.deliver() → Telegram
  → If scheduled tasks due:
      → Parse tasks (cron, @once, @at)
      → Run shell command (if present)
      → Optionally invoke LLM for summary
      → DeliveryPort.deliver() → Telegram
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
| OpenRouter | LLM inference (Claude Sonnet) | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| Local filesystem | Skill discovery, heartbeat config, state | `~/.config/nemoclaw/` |
| Local shell | Tool execution (subprocess) | N/A |

## Design Principles

1. **Functional core, imperative shell** — Pure business logic (evaluate, parse, model) separated from I/O (adapters, gateways, tool handlers)
2. **Immutability** — `Conversation` is an immutable tuple; new instances created via `.append()`
3. **Protocol-based ports** — `InferencePort`, `DeliveryPort`, `ActivityIndicator` defined as Python Protocols for loose coupling
4. **Extensible tools** — New tools added by registering definition + handler in `ToolRegistry`
5. **Extensible skills** — Drop a `SKILL.md` in the skills directory; auto-discovered at startup
