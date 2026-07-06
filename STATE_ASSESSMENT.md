# OpenCloset State Assessment â Day 2

> Written: 2026-05-03
> Purpose: Map existing codebase to user vision and identify gaps.

## What Exists Today

### Backend (FastAPI)
- **app.py** â Main FastAPI app with session/message/capture endpoints, HTML UI shell
- **provider.py** â Model provider abstraction (OpenAI-compatible, llama.cpp, Ollama)
- **storage.py** â SQLite store for sessions, messages, agents, providers, captures
- **api/agent/** â Agent loop, engine, guard, input pipeline, prompt building, runner
- **api/api/** â Routes, streaming, events, planning, scheduling, maintenance, rollover, watchdog, transcript manager, memory, reflective pastimes, workspaces
- **api/tools/** â Tool registry, built-in tools (read, write, edit, exec, process, plan_*, memory_search)
- **tests/** â Test suite for providers, scheduler, streaming, watchdog, etc.

### Frontend (SvelteKit)
- **ui/** â SvelteKit app with chat UI, Vite + Bun tooling
- Chat interface with message rendering
- Token pressure indicators
- Session management

### Workspaces
- **hockey/** â Hockey Lab workspace with pipeline, data, models
- **f1/** â F1 workspace (partial)
- **opencloset/** â Meta-workspace for building OpenCloset itself

### Config & Docs
- VISION.md, IDENTITY.md, MEMORY.md, SOUL.md, TOOLS.md, USER.md
- AGENTS.md, HEARTBEAT.md
- plan-v6.md, PROGRESS.md, README.md
- USER_BRIEF.md (saved today for context recovery)

## User Vision vs Current State

### Already Built or Partially Built
| Vision Item | Status | Notes |
|---|---|---|
| Local model harness (llama.cpp/Ollama) | Done | provider.py supports multiple backends |
| OpenAI API integration | Done | OpenAICompatibleProvider exists |
| Chat-native UI | Done | SvelteKit chat interface |
| Session management & persistence | Done | SQLite store, sessions, messages |
| Token pressure tracking | Done | Watchdog, thresholds, rollover |
| Context compaction/rollover | Done | summarize-rollover endpoint, compaction |
| Tool system (read/write/edit/exec) | Done | Tool registry with built-in tools |
| Planning system | Done | plan_*, planning routes |
| Memory/search | Done | memory_search tool, diary |
| Workspaces | Done | Workspace routes, hockey/f1 examples |
| Reflective pastimes | Partial | reflective_pastimes.py exists |
| Idle-time scheduler | Partial | scheduler.py, scheduler_producers.py |
| Transcript export (JSONL) | Done | transcript_manager.py |

### â Not Yet Built (Gaps)
| Vision Item | Status | Notes |
|---|---|---|
| **Satellite Array System** | â Missing | Multi-device orchestration, model routing by capability |
| **Transient Windows (Visual Boards)** | â Missing | Inline applets in chat, HTML/CSS windows, pin/save/dispose |
| **Opportunistic Context Maintenance** | â ï¸ Partial | Rollover exists but is reactive (threshold-based), not opportunistic (constant shrinking) |
| **Idle Time Utilization** | â ï¸ Partial | Scheduler exists but needs pastime orchestration, session updates during idle |
| **Browser Window (Transient)** | â Missing | In-chat browser viewer |
| **Terminal Window (Transient)** | â Missing | In-chat terminal viewer |
| **File Writer Window** | â Missing | In-chat code editor / file viewer |
| **Notes Window** | â Missing | In-chat notepad |
| **Calendar Window** | â Missing | In-chat calendar viewer |
| **Artifact System** | â Missing | Save transient windows as artifacts, GitHub Pages publish |
| **Multi-model routing** | â Missing | Route tasks to appropriate model (3B for chat, 27B for code, etc.) |

## Priority Recommendations

### Phase 1: Transient Windows Foundation
1. Define Transient Window data model (type, content, metadata, lifecycle)
2. Add Transient Window endpoints to API
3. Build chat message renderer that detects & displays Transient Windows
4. Implement basic window types: Notes, File Writer, Report

### Phase 2: Opportunistic Context Maintenance
1. Add background context shrinking (not just threshold-based rollover)
2. Implement periodic compaction during idle periods
3. Add config for compaction frequency/aggressiveness

### Phase 3: Satellite Array System
1. Define device/model registry schema
2. Build model router (task â best available model)
3. Implement cross-device communication protocol
4. Start with 2 devices (5060 Ti + 1080 Ti)

### Phase 4: Advanced Transient Windows
1. Browser window
2. Terminal window
3. Calendar window
4. Artifact save/publish system

## Key Questions for User
1. Which gap do you want to tackle first? (Transient Windows, Opportunistic Context, Satellite Array?)
2. Do you want to push to GitHub before continuing? (handoff_next_session.md suggests this was a goal)
3. Any existing code in the workspace that partially implements Transient Windows or Satellite Array?
4. What's your preferred approach: build incrementally and test, or design architecture first?

---
*This file is a living document. Update as we progress.*
