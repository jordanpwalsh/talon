# Hexagonal Architecture View

This is the text-editable ports-and-adapters view of Talon.

```mermaid
flowchart TB
    main{{main.py<br/>composition root}}

    telegram_api([Telegram Bot API])
    openrouter([OpenRouter / LLM APIs])
    shell([Local Shell])
    fs([Local Filesystem])

    telegram["Telegram Gateway<br/>inbound entrypoints<br/>handlers.py / formatting.py / heartbeat_delivery.py"]
    agent["Agent Orchestrator<br/>use case: run_agent()<br/>ports: InferencePort, ActivityIndicator, ToolRegistry"]
    heartbeat["Heartbeat<br/>use cases: scheduler_loop(), run_heartbeat(), run_scheduled_tasks()<br/>port: DeliveryPort<br/>dependency: run_agent()"]
    inference["Inference Adapters<br/>OpenRouterAdapter / ClaudeAgentSDKAdapter"]
    tools["Tool Handlers<br/>shell.py / filesystem.py"]
    conversation[Conversation / Session Store]
    config[Config / Skills Discovery]

    main --> telegram
    main --> agent
    main --> heartbeat
    main --> config

    telegram_api <--> telegram
    telegram --> agent
    telegram --> conversation

    heartbeat -->|DeliveryPort| telegram
    heartbeat -->|run_agent use case| agent

    agent -->|InferencePort| inference
    agent -->|ToolRegistry| tools
    agent --> conversation

    inference --> openrouter
    tools --> shell
    tools --> fs

    classDef composition fill:#f8f9fa,stroke:#495057,stroke-width:2px,color:#1e1e1e;
    classDef telegram fill:#dbe4ff,stroke:#1971c2,stroke-width:2px,color:#1e1e1e;
    classDef agent fill:#d8f5a2,stroke:#2f9e44,stroke-width:2px,color:#1e1e1e;
    classDef heartbeat fill:#fff3bf,stroke:#f08c00,stroke-width:2px,color:#1e1e1e;
    classDef inference fill:#fff4e6,stroke:#e8590c,stroke-width:2px,color:#1e1e1e;
    classDef tools fill:#ffe3e3,stroke:#c92a2a,stroke-width:2px,color:#1e1e1e;
    classDef skills fill:#f3d9fa,stroke:#862e9c,stroke-width:2px,color:#1e1e1e;
    classDef conversation fill:#e5dbff,stroke:#6741d9,stroke-width:2px,color:#1e1e1e;
    classDef config fill:#e9ecef,stroke:#495057,stroke-width:2px,color:#1e1e1e;

    class main composition;
    class telegram_api,telegram telegram;
    class agent agent;
    class heartbeat heartbeat;
    class openrouter,inference inference;
    class shell,tools tools;
    class conversation conversation;
    class config config;
    class fs config;
```

## Shape Meanings

- Blue: Telegram boundary
- Green: agent orchestrator
- Amber: heartbeat
- Orange: inference / OpenRouter
- Red: shell tools
- Purple: conversation state
- Gray: config, filesystem, and composition root
- Arrow labels: ports or use-case boundaries
