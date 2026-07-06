# DECISIONS.md

Record durable decisions here so fresh sessions do not re-litigate them.

## Format

- Date:
- Scope:
- Decision:
- Why:
- Follow-up:

## Decisions

- 2026-04-27 | OpenClaw runtime | Default coding model is `llamacpp/qwen3.6-27b`. | Keeps primary coding local on llama.cpp. | Watch server health on `127.0.0.1:8080`.
- 2026-04-27 | Long-running work | Use fresh isolated sessions plus file-based handoff (`WORK_QUEUE.md`, `HANDOFF.md`, `MEMORY.md`). | Prevents context window bloat and makes continuation explicit. | Keep handoffs updated before every stop.
- 2026-06-12 | Product boundary | The Loom is the single app and `.oradio` is the single artifact; Radio OS, ForkUniverse, ATL, Oracle Kingdom, FTB, and related systems are capability layers, not separate product identities. | Owner pivot unified the stack around one authoring environment and one standalone export contract. | Start future product and architecture work from `docs/LOOM_ORADIO_ARCHITECTURE.md`.
