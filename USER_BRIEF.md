# User Brief â Saved for Context Recovery

> Write this file whenever context is lost. Read it first to re-orient.

## Hardware
- GPU: RTX 5060 Ti + RTX 1080 Ti
- RAM: 64 GB DDR5
- CPU: Ryzen 9 7900X
- Mobile: Samsung Galaxy Z Fold 5
- Laptop: MacBook Pro

## Core Philosophy
- **Harness > Model size.** A strong harness on consumer hardware can out-engineer frontier models that have constraints (many users, no idle time utilization).
- **Solo creative projects** â long-term, open-source, solo developer focus.
- **Opportunistic context maintenance** â shrink context constantly in the backend rather than waiting for it to fill up and compact. Results in lean token usage with high relevance.
- **Idle time utilization** â update sessions, action tasks, orchestrate pastimes, maintain context during idle periods.

## Satellite Array System
Multiple devices of different compute calibers working in harmony:
- 27B model on 5060 Ti
- 8B model on 1080 Ti
- 3B model on Samsung Galaxy Z Fold 5
- 14B model on MacBook Pro
- OpenAI API model integrated

The AI ("Clo") is abstract â it inhabits the system and routes to whichever model is accessible and appropriate for the task.

## Chat-Native UI â Transient Windows (Visual Boards)
Disposable applets/websites rendered as special chat messages inside the conversation:
- Can be pinned, saved as artifacts, or let slip naturally
- Inline reports (e.g., top 10 NHL prospects)
- Navigable windows with tabs
- Predefined panels: chips, plan, inspector, overview
- **Goal: Keep chat primary while letting structure emerge inline when it materially helps**

### Usage Doctrine
- Chat is 1A. Transient windows are 1B.
- Windows augment the conversation; they do not replace synthesis, reasoning, tone, or persona.
- Clo should be willing but not eager to create windows.
- Windows should feel discovered, not sprayed.
- Errors are special: deterministic transient error windows should appear whenever possible.

### Judgment Policy
- Use a transient window when it adds legibility, comparison, interaction, persistence value, or cognitive relief.
- Stay text-first when the answer is simple, conversational, emotionally sensitive, or already easy to parse as prose.
- If the case is borderline, answer in chat and optionally offer a window.

### Planned Window Types
- **Notes/Files** â file writer (like pure VS Code), also simple .txt
- **Calendar** â surfaced as a window
- **Browser** â real-time collaborative browsing for research
- **Terminal** â eyes on executed processes (on request)
- **Code Editor** â see and edit code files inline
- **Research Artifacts** â save inline applets as saved artifacts or let them archive naturally

### Workflow Example
User: "Hey, open a browser and search for Connor McDavid's advanced stats" â Browse together â Form conclusions â Save as new inline Transient Window artifact â Optionally publish via GitHub Pages.

## Key Principles
1. Conversation is the interface
2. Chat thinks and explains; windows organize and visualize
3. Windows can become artifacts or disappear into history
4. Multi-device, multi-model orchestration
5. Opportunistic context maintenance
6. Idle time is productive time

---
*Last saved: Day 2 of build*
