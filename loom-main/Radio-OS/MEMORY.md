# MEMORY.md

Record stable facts, conventions, and pitfalls here.

## Stable Facts

- OpenClaw should behave as a general-purpose coding and documentation agent.
- Telegram is the primary operator channel for status reports, uncertainty, and approval requests.
- Long-running work should continue through fresh isolated sessions, not one bloated conversation.
- `HANDOFF.md` is the canonical resume packet across sessions.
- Primary local model server: `llama.cpp` on `http://127.0.0.1:8080/v1`.
- Primary local coding model: `llamacpp/qwen3.6-27b`.

## Pitfalls

- Do not assume the current repo is always `Radio-OS`; confirm the active repo before editing.
- Do not continue uncertain work silently. Ask on Telegram when ambiguity would change implementation, validation, or touched files.

## Operating Defaults

- Prefer one focused slice of work per run.
- Leave the repo in a resumable state before stopping.
- Summarize meaningful progress in plain language the owner can scan quickly on Telegram.
- Studio multi-phase roadmaps live in `docs/`, not only in handoff or queue files.
