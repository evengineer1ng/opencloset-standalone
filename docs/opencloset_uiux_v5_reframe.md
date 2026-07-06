# OpenCloset UI/UX Reframe — v5

_Status: Draft · Created: 2026-05-02 · Targets v5 product foundation_
_Goal: Re-audit the current desktop shell against the new worldview and produce concrete specs for the next iteration_

---

## 1. Findings Memo

### What to Preserve

- **Left nav → workspaces, build projects, sessions, workspace views**: This hierarchy is structurally sound. Workspaces as top-level domains with nested build projects and sessions matches the v5 data model exactly.
- **Right panel → Plan, Queue, Memory, Captures, Settings tabs**: The tab structure works. Plan is the strongest current feature. Memory and Captures are valid surfaces.
- **Runtime dock**: The expandable dock concept is good — a compact strip with expandable detail. The current pill layout (Run, Workspace, Pastime, Context, Model) is a solid starting point.
- **WorkspaceHeader breadcrumb trail**: workspace → project → session is the right context stack. Provider and model in the subline is useful.
- **Session action buttons** (Resume, Interrupt, Rerun Last Turn): These are essential operational controls for build work.
- **Chat composer with textarea + send button**: Standard pattern, works fine.

### What to Reframe

- **"Clo" identity is invisible**: The UI says "OpenCloset" everywhere. Clo is the persona — the user-facing identity. Assistant messages show a 🤖 emoji, not Clo. The brand header says "OpenCloset" not "Clo in OpenCloset." This is the single biggest identity mismatch.
- **Builder/Workspace mode split**: These are not really different modes — they're different center-pane views of the same session context. The explicit toggle creates confusion about whether you're in two different products. Should be unified into session-first context.
- **Right panel tab logic depends on mode**: `defaultTab = mode === "builder" ? "plan" : "queue"` — this couples the panel to a mode concept that shouldn't exist. Plan should be the default during any session with an active plan, regardless of mode.
- **Tool activity is buried**: Tool results render as plain text inside system messages (`formatPersistedToolMessage`). During a real coding session with 4-6 tool calls per turn, this is a wall of truncated text that's hard to scan. Needs structured, collapsible treatment.
- **Runtime dock is debug-heavy**: The current expanded view has 7 sections (Session Snapshot, Workspace State, Context Guard, Workspace Inbox, Candidate Queue, Worker Registry, Recent Events). That's overwhelming for a dock. Should be tighter — prioritize what needs attention now, collapse what can wait.
- **Settings are buried in right panel**: Tool policy and session config live in a right panel tab. For controls that affect how Clo operates, they deserve a more accessible home.

### What to Remove

- **Mode indicator in header**: The builder/workspace mode badge and toggle encode the old "two products" assumption. Remove it.
- **"Runtime" section in left nav**: The current left nav has a `Runtime` section with a static "Session-driven runtime · live" entry that does nothing. Remove it — runtime state belongs in the dock, not the nav.
- **"Workspace Views" section in left nav**: Dashboard, inbox, briefing, evidence are workspace-scoped views that replace the center pane. They shouldn't be nav items alongside sessions — they're alternative views of the same workspace. Better as workspace-level tabs or a workspace-level menu.
- **Generic `phonecloset` capture source**: The capture intake form has `phonecloset` as a source option. Under v5, this should be `phone` or `mobile` — the product identity changed.
- **Hardcoded `Qwen3.6-27b` default model**: `DEFAULT_MODEL = "qwen3.6-27b"` in DesktopShell.tsx bakes in a specific model. Under v5, model/substrate is a runtime concept, not a UI constant.

### What Is Actively Misleading

- **"OpenCloset" brand without Clo**: Every brand reference says "OpenCloset." The user interacts with Clo, not with the runtime. This creates a disconnect between who they're talking to and what the product calls itself.
- **Builder mode implies a separate product**: Users might think "builder" is a different app or workflow, not just a session focused on a build project.
- **System messages are indistinguishable from assistant messages**: `tool` role messages render as `system` in the chat view (`role: "system", content: formatPersistedToolMessage(...)`). The user can't tell at a glance whether a message is Clo speaking or a tool result.
- **Dock Model pill shows session model, not substrate**: It reads `session?.model || "no-model"` — this shows the current session's configured model, but under v5, the model is a substrate choice that can change per-turn or per-delegation. The pill should reflect substrate routing, not just session config.

---

## 2. Revised Information Architecture

### Operating Domains

| Domain | Description | Priority |
|---|---|---|
| **Chat** | Primary interaction surface with Clo | Core — always present |
| **Plan** | Active plan, items, proposals, revisions | Core — always accessible during sessions |
| **Runtime** | Run state, tool activity, context pressure, workspace signals | Core — always visible |
| **Workspace** | Domain context, build projects, evidence, captures | Core — structural anchor |
| **Memory** | Session diary, daily log, semantic retrieval | Important — right panel tab |
| **Settings** | Tool policy, session config, safety controls | Important — dedicated surface |
| **Closet/Array** | Node registry, pairing, authority, routing | Future — reserved |
| **Substrate** | Model selection, capability profiles | Future — reserved |

### Layout: Four-Zone Shell (Preserved, Refined)

```
┌─────────────────────────────────────────────────────────────────────┐
│  HEADER: Clo · Workspace · Session context · Run state · Actions   │
├──────────┬──────────────────────────────────┬──────────────────────┤
│          │                                  │                      │
│ LEFT NAV │         CENTER PANE              │   RIGHT PANEL        │
│ (320px)  │    (flex, primary surface)       │   (280px)            │
│          │                                  │                      │
│ Workspace│  Chat / Workspace Dashboard      │  Plan | Memory       │
│ hierarchy│  / Workspace Views               │  | Captures          │
│ Sessions │                                  │  | Settings          │
│          │                                  │                      │
├──────────┴──────────────────────────────────┴──────────────────────┤
│  DOCK: Run state · Context · Signals · Model · (future: Array)     │
└─────────────────────────────────────────────────────────────────────┘
```

### Left Nav (Refined)

**What stays**: Workspace hierarchy → Build Projects → Sessions. This is the structural backbone.

**What changes**:

1. **Remove "Workspace Views" section** — Dashboard, Inbox, Briefing, Evidence are workspace-level views accessed via the workspace name header or a workspace-level menu, not nav items alongside sessions.
2. **Remove "Runtime" section** — Runtime state lives in the dock.
3. **Add future "Closet" section** (placeholder) — Reserved for future array/node list. Hidden or greyed out when no nodes are paired.

**Revised left nav sections**:
- Workspaces (+ new button)
- Build Projects (+ new button)
- Sessions (+ new button)
- Closet (future — reserved slot, hidden until paired nodes exist)

### Center Pane (Refined)

The center pane has two primary states:

1. **Session active** → Chat surface (primary). This is the default and most common state.
2. **No session / workspace level** → Workspace Dashboard or workspace-level views (inbox, briefing, evidence).

**Key change**: Remove the Builder/Workspace mode toggle. The center pane renders whatever is contextually relevant:
- If a session is selected → Chat surface
- If only a workspace is selected → Workspace Dashboard
- Workspace-level views (inbox, briefing, evidence) are accessed via the workspace header or a workspace menu

### Right Panel (Refined)

Tabs remain but are re-ordered and contextualized:

1. **Plan** — Default when session has active plan
2. **Memory** — Session memory search, diary, captures
3. **Captures** — Capture intake and workspace evidence
4. **Settings** — Tool policy, session config, safety

**What changes**:
- Remove "Queue" tab — Queue visibility moves to the runtime dock
- Settings becomes a more prominent surface (see §7)

### Bottom Dock (Refined)

The dock is the operational surface. See §5 for full redesign.

### Builder/Workspace Mode: Replace with Context-Driven Views

**Recommendation**: Remove the explicit mode toggle. Replace with context-driven behavior:

- Session selected → Chat surface
- No session, workspace selected → Workspace Dashboard
- Workspace-level menu provides access to Inbox, Briefing, Evidence views

The "builder" concept isn't wrong — it's a session focused on a build project with an active plan. But it shouldn't be a UI mode switch. It's a natural consequence of which session/workspace/project you've navigated to.

---

## 3. Center Pane Redesign — "Clo Builds From Inside OpenCloset"

### Identity Surface

The chat surface should feel like you're working with Clo, not chatting with a bot:

- **Brand header**: `Clo · OpenCloset` (Clo first, runtime second)
- **Assistant messages**: Label with "Clo" instead of 🤖. Use a Clo-specific avatar or initial.
- **User messages**: "You" instead of 👤. Or just use the name pattern consistently.

### Message Treatments

Current issue: tool results render as plain truncated text inside system messages, making long coding sessions hard to scan.

**Proposed message types and treatments**:

| Message Type | Treatment |
|---|---|
| **Clo speaks** | Full-width bubble, left-aligned, subtle background. Clo label. |
| **You speak** | Full-width bubble, right-aligned, distinct background. |
| **Tool call (start)** | Collapsible inline block within Clo's turn. Tool name + icon. Input truncated to 2 lines, expandable. |
| **Tool call (result)** | Inline block within Clo's turn. Status badge (success/error/interrupted). Output truncated to 3 lines, expandable. |
| **Run completed** | Status bar after Clo's turn: ✅ Completed · X tools · Y tokens · Zs |
| **Run failed** | Status bar after Clo's turn: ❌ Failed · reason · [Retry] · [Continue] |
| **Run interrupted** | Status bar: ⏸ Interrupted · [Resume] · [Continue] |
| **Streaming** | Clo bubble with animated cursor. Tool events appear as they happen inline. |
| **Plan reference** | When Clo references a plan item, show a small plan-item badge linking to the right panel. |

**Implementation pattern**: Group messages by turn. Each turn shows Clo's streaming text interleaved with tool call/result blocks. After the turn completes, show a status bar with summary metadata.

```
┌─────────────────────────────────────────────┐
│ Clo · 2:15 PM                               │
│ I've added the node registry schema.        │
│                                             │
│ ┌─ Tool: write ─────────────────────────┐   │
│ │ Writing opencloset/api/db/array_schema │   │
│ │ ✅ Success · 127 bytes · 0.3s          │   │
│ └───────────────────────────────────────┘   │
│                                             │
│ ┌─ Tool: exec ──────────────────────────┐   │
│ │ Running pytest tests/array_model...    │   │
│ │ ✅ Success · 12 tests passed · 2.1s    │   │
│ └───────────────────────────────────────┘   │
│                                             │
│ Next, I need to wire the pairing endpoint.  │
│                                             │
│ ✅ Turn 3 · 2 tools · 340 tokens · 4.2s    │
└─────────────────────────────────────────────┘
```

### Readability During Coding Work

- **Tool blocks are collapsed by default**, expandable on click. During a turn with 4-6 tool calls, the user sees tool names and status badges without reading every input/output.
- **Failed tools are expanded by default** — the user needs to see what went wrong immediately.
- **Run status bars are always visible** — the last turn's outcome is immediately scannable.
- **"What happened last turn?"** is answered by the status bar + collapsed tool blocks at the bottom of the most recent turn.

### Re-Entry After Failure/Pause

- **Interrupted runs** show `[Resume]` and `[Continue]` buttons in the status bar.
- **Failed runs** show `[Retry Turn]`, `[Continue]`, and `[Adjust Plan]` buttons.
- These buttons feed into existing `onResumeRun`, `onInterruptRun`, `onRerunLastTurn` handlers.

---

## 4. Plan/Runtime Coupling Redesign

### What Should Always Be Visible During Build Work

1. **Current plan name and active goal** — In the header or a persistent strip above the chat surface.
2. **Next plan item** — What Clo is working on right now. Shown in the header subline or a small badge above the chat input.
3. **Run state** — Running/Queued/Idle/Failed — In the dock strip and the header.

### What Is Currently Buried But Should Be Surfaced

- **Plan item status** during active runs: The workspace header doesn't show which plan item is currently being worked on. This should appear in the header subline or above the chat surface.
- **Tool failure context**: When a run fails due to a tool error, the user needs to see which tool failed and why, without scrolling through the transcript. The run status bar should summarize this.
- **Plan progress**: How far through the plan are we? The right panel shows this, but during active build work, a small progress indicator (3/8 items · 37%) in the header would be valuable.

### What Is Currently Too Noisy

- **Right panel plan tab**: The full plan item list with add/edit/status controls is comprehensive but overwhelming during active coding. During a running session, the right panel should show a condensed "execution view": next item, recent changes, pending proposals. Full plan editing can be a toggle or a dedicated plan view.
- **Workspace subline in header**: `workspace active · project none · provider · model · msgs` — too many dots. Condense to: `provider · model · run state`.
- **Dock expanded view**: 7 sections is too much. See §5.

### Recommended Plan/Runtime Integration

```
┌──────────────────────────────────────────────────────┐
│ Clo · OpenCloset                                     │
│ workspace: OpenCloset → project: v5-array → session  │
│ 📋 Plan: "Array Foundation" · Item 3/8: "Node registry schema" │
│ ⚡ Running · turn 4 · qwen3.6-27b · 42% context     │
├──────────────────────────────────────────────────────┤
│                                                      │
│  [Chat surface — turns, tool blocks, status bars]   │
│                                                      │
├──────────────────────────────────────────────────────┤
│  Run · Workspace · Pastime · Context · Model         │
│  running · 3 inbox · idle · 42% · qwen3.6-27b       │
└──────────────────────────────────────────────────────┘
```

The header becomes the "operational context strip" — workspace, plan, run state, context pressure all visible at a glance.

---

## 5. Runtime Dock Redesign

### Current Problems

- 7 expanded sections (Session Snapshot, Workspace State, Context Guard, Workspace Inbox, Candidate Queue, Worker Registry, Recent Events) — too many for a dock
- Collapsed strip has 5 pills — reasonable but some are low-priority (Pastime is rarely actionable)
- Signal actions (Start, Defer, Buddy, CLO) are buried in the expanded view
- Model pill shows session config, not routing/substrate

### Revised Dock Hierarchy

**Collapsed strip** (always visible, 3-4 pills):

| Pill | Shows | Priority |
|---|---|---|
| **Run** | Status + turn number | Always — what's happening now |
| **Context** | Token % + threshold | Always — safety boundary |
| **Signals** | Count of open inbox items | Always — what needs attention |
| **Model** | Current substrate/model | Always — what's powering Clo |

Remove "Workspace" and "Pastime" from the collapsed strip. These are expandable-only details.

**Expanded view** (3 sections, not 7):

1. **Run Detail** — Turn status, recent tool calls (last 3), failure reason if applicable, retry/resume actions
2. **Signals & Inbox** — Open signals with inline actions (promote from dock directly)
3. **Context & Resources** — Token usage, rollover threshold, model info, substrate state (future: node/authority)

```
Collapsed:
┌───────────────────────────────────────────────────┐
│ Run: running T4  │  Context: 42%  │  Signals: 3  │  Model: qwen3.6-27b │  ▴ │
└───────────────────────────────────────────────────┘

Expanded:
┌─────────────────────────────────────────────────────────────┐
│ Run Detail                        │ Signals & Inbox         │
│ Turn 4 · Running                 │ 📨 Backlog review       │
│ Last tools:                      │   [Start] [Defer]       │
│   ✅ write · 0.3s                │ 📨 Context review        │
│   ✅ exec · 2.1s                 │   [Pause]                │
│                                  │ 📨 Handoff ready         │
│ Context & Resources               │   [Resume]               │
│ Tokens: 28K/66K (42%)            │                         │
│ Rollover: 58K                    │                         │
│ Substrate: qwen3.6-27b (local)   │                         │
│ Authority: this node             │                         │
└─────────────────────────────────────────────────────────────┘
```

### Reserved Future Dock Slots

- **Array/Closet**: When nodes are paired, add a pill showing online node count and status
- **Authority**: When multi-node, show which node holds session authority
- **Delegation**: When cross-node delegation is active, show delegation state

These slots remain hidden or greyed out when the features don't exist yet.

---

## 6. Future Substrate and Array Visibility

### Reserved UI Patterns (Not Implemented Yet)

**Substrate attribution in chat**:
When Clo uses a different substrate/model for a turn (future multi-substrate routing), the message bubble should show which substrate was used:

```
┌─────────────────────────────────────┐
│ Clo · qwen3.6-27b (local) · 2:15 PM │
│ I've added the node registry...      │
└─────────────────────────────────────┘
```

When delegation happens (future), show the target:

```
┌─────────────────────────────────────┐
│ Clo → delegated to Buddy (4B)       │
│ Review complete: 3 issues found     │
│ ← returned from Buddy               │
└─────────────────────────────────────┘
```

**Future Closet/Array section in left nav**:
A section that appears when paired nodes exist:

```
Closet
  ● Desktop (authority · 27B)
  ○ MacBook (online · planning)
  ● Phone (online · capture)
```

Hidden when no nodes are paired. Single-node OpenCloset works identically without it.

**Future Authority display in header**:
When multi-node, the header subline shows authority:

```
workspace → project → session · authority: Desktop · qwen3.6-27b
```

**Future Node status in dock**:
When nodes are paired, replace or augment the Model pill with substrate/node info.

### Single-Node Completeness

All of the above is additive. Single-node OpenCloset should feel complete without any array features:
- Chat with Clo works
- Plans work
- Runtime visibility works
- Memory works
- Settings work
- Dock shows run state, context, signals, model

No greyed-out "coming soon" features. The array surfaces simply don't appear until they exist.

---

## 7. Settings and Control Surfaces

### Current State

Settings live in a right panel tab with two tool policy groups (Files + Process, Planning + Memory) and checkbox allowlists. This is functional but feels like a config form, not an operational control surface.

### Recommended Changes

1. **Elevate tool policy visibility**: Tool policy affects what Clo can do — it's a safety/trust control, not just a config. During a session, the user should be able to quickly see and adjust what Clo can touch without navigating to a settings tab.

2. **Consolidate into a Settings surface**: Move settings from a right panel tab into a dedicated Settings view (like Evidence/Briefing views). This gives it enough space for proper layout.

3. **Current priority**: Tool policy per session, allowed paths, destructive tool approval. These work as-is but need better presentation.

4. **Future additions** (reserved, not implemented yet):
   - Substrate selection (which model/runtime to use)
   - Budget/remote-use visibility (when OpenAI/OpenRouter is an option)
   - Node pairing controls (add/remove/pair devices)
   - Authority preferences (which node should hold authority by default)

### Settings Surface Layout

```
┌──────────────────────────────────────────────┐
│ Settings                                     │
│                                              │
│ Session Config                               │
│   Model: qwen3.6-27b                         │
│   Provider: llamacpp                         │
│   Context window: 32K                        │
│                                              │
│ Tool Policy                                  │
│   [x] read      [x] write     [x] edit      │
│   [x] exec      [x] process   [ ] web_fetch │
│   [ ] message   [ ] cron      [ ] sessions  │
│                                              │
│ Allowed Paths                                │
│   D:\openclaw\opencloset\                    │
│   D:\openclaw\memory\                        │
│                                              │
│ Safety Controls                              │
│   Destructive tools: require approval        │
│   External actions: require approval         │
│                                              │
│ ──────────────────────────────────────────   │
│ Future (when available):                     │
│   Substrate selection                        │
│   Node pairing                               │
│   Authority preferences                      │
└──────────────────────────────────────────────┘
```

---

## 8. Key Screen/State Specs

### Spec 1: Main Screen — Active Build Session

**Context**: Clo is actively building OpenCloset, running a plan, executing tool calls.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Clo · OpenCloset                                                      │
│ workspace: OpenCloset → project: v5-array → session: Array Foundation │
│ 📋 Item 4/8: "Pairing endpoint"  │  ⚡ Running T5  │  45%  │  qwen3.6 │
├───────────┬───────────────────────────────────────┬───────────────────┤
│           │                                       │                   │
│ Workspaces│  [Chat surface]                       │  Plan             │
│ OpenCloset│                                       │                   │
│           │  Clo · qwen3.6-27b · 2:30 PM          │  Array Foundation │
│  Projects │  Now I'll add the pairing endpoint     │  4/8 · 50%        │
│  v5-array │                                       │                   │
│           │  ┌─ Tool: read ─────────────────────┐ │  Next: Pairing ep │
│  Sessions │  │ Reading api/api/pairing.py       │ │  After: Auth      │
│  Array... │  │ ✅ Success · 0.1s                │ │                   │
│           │  └──────────────────────────────────┘ │  Proposals (0)    │
│           │                                       │                   │
│           │  ┌─ Tool: write ────────────────────┐ │                   │
│           │  │ Writing api/api/pairing.py       │ │                   │
│           │  │ ⏳ Running...                     │ │                   │
│           │  └──────────────────────────────────┘ │ │                   │
│           │                                       │                   │
│           │  [Message input · "↑"]                │                   │
├───────────┴───────────────────────────────────────┴───────────────────┤
│ Run: running T5 │ Context: 45% │ Signals: 2 │ Model: qwen3.6-27b │ ▴ │
└───────────────────────────────────────────────────────────────────────┘
```

**Key properties**:
- Header shows workspace, plan progress, run state, context pressure, model
- Chat surface shows Clo's messages with inline tool blocks
- Right panel shows condensed plan view (next item, progress, proposals)
- Dock shows operational strip — always visible

### Spec 2: Session Paused / Failed Run

**Context**: Run failed due to tool error. User needs to decide: retry, continue, or adjust plan.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Clo · OpenCloset                                                      │
│ workspace: OpenCloset → project: v5-array → session: Array Foundation │
│ 📋 Item 4/8: "Pairing endpoint"  │  ❌ Failed T5  │  47%  │  qwen3.6 │
├───────────┬───────────────────────────────────────┬───────────────────┤
│           │                                       │                   │
│  [nav]   │  Clo · 2:32 PM                        │  [plan panel]     │
│           │  Adding the pairing endpoint...        │                   │
│           │                                       │                   │
│           │  ┌─ Tool: exec ─────────────────────┐ │                   │
│           │  │ Running pytest tests/pairing...  │ │                   │
│           │  │ ❌ Error · 3 failures · 1.2s     │ │                   │
│           │  │ ──────────────────────────────── │ │                   │
│           │  │ test_pair_handshake FAILED       │ │                   │
│           │  │ Assertion error: expected 200    │ │                   │
│           │  │ ──────────────────────────────── │ │                   │
│           │  └──────────────────────────────────┘ │                   │
│           │                                       │                   │
│           │  ❌ Turn 5 · 1 tool failed · 480 tok │                   │
│           │  [Retry Turn] [Continue] [Adjust Plan] │                   │
│           │                                       │                   │
│           │  [Message input · "↑"]                │                   │
├───────────┴───────────────────────────────────────┴───────────────────┤
│ Run: failed T5 │ Context: 47% │ Signals: 2 │ Model: qwen3.6-27b │ ▴ │
└───────────────────────────────────────────────────────────────────────┘
```

**Key properties**:
- Failed tool is expanded by default (user needs to see the error)
- Status bar shows failure reason and action buttons
- Action buttons feed into existing handlers (rerun, resume, continue)

### Spec 3: Workspace Dashboard (No Session)

**Context**: User is at workspace level, no active session. Needs overview of workspace state.

```
┌───────────────────────────────────────────────────────────────────────┐
│ Clo · OpenCloset                                                      │
│ workspace: OpenCloset                                                  │
│ ───────────────────────────────────────────────────────────────────   │
├───────────┬───────────────────────────────────────┬───────────────────┤
│           │  OpenCloset Dashboard                 │  [right panel]    │
│  [nav]   │                                       │                   │
│           │  Build Projects                       │                   │
│           │  ┌──────────────┬──────────────┐     │                   │
│           │  │ v5-array     │ v4-legacy    │     │                   │
│           │  │ 3/8 items    │ archived     │     │                   │
│           │  │ 2 sessions   │ 1 session    │     │                   │
│           │  └──────────────┴──────────────┘     │                   │
│           │                                       │                   │
│           │  Runtime Overview                      │                   │
│           │  Signals: 2 open · Workers: 3 active  │                   │
│           │  Pastime: idle · Context: n/a         │                   │
│           │                                       │                   │
│           │  Recent Activity                      │                   │
│           │  2:30 PM — Turn completed (T5)        │                   │
│           │  2:28 PM — Tool: write executed       │                   │
│           │  2:25 PM — Run started (T4)           │                   │
│           │                                       │                   │
│           │  [New Session] [New Build Project]    │                   │
├───────────┴───────────────────────────────────────┴───────────────────┤
│ Idle │ Context: — │ Signals: 2 │ Model: — │ ▴ │
└───────────────────────────────────────────────────────────────────────┘
```

**Key properties**:
- No session → workspace dashboard shows projects, runtime overview, recent activity
- Right panel can show workspace-level tabs (captures, memory, settings)
- Dock still shows workspace signals and state

### Spec 4: Settings View

**Context**: User needs to adjust tool policy or session configuration.

Accessed via workspace-level menu or a settings button in the header. Replaces the center pane.

```
┌───────────┬───────────────────────────────────────┬───────────────────┤
│           │  Settings                             │                   │
│  [nav]   │                                       │                   │
│           │  Session Configuration                │                   │
│           │  Model: [qwen3.6-27b ▼]              │                   │
│           │  Provider: [llamacpp ▼]              │                   │
│           │  Context window: 32K                  │                   │
│           │                                       │                   │
│           │  Tool Policy                          │                   │
│           │  Files + Process                      │                   │
│           │    [✓] read   [✓] write  [✓] edit    │                   │
│           │    [✓] exec   [✓] process            │                   │
│           │                                       │                   │
│           │  Planning + Memory                    │                   │
│           │    [✓] memory_search  [✓] plan_*      │                   │
│           │                                       │                   │
│           │  External (restricted)                │                   │
│           │    [ ] web_fetch   [ ] cron           │                   │
│           │    [ ] message     [ ] sessions_*      │                   │
│           │                                       │                   │
│           │  Allowed Paths                        │                   │
│           │    D:\openclaw\opencloset\            │                   │
│           │    D:\openclaw\memory\                │                   │
│           │    [+ Add path]                       │                   │
│           │                                       │                   │
│           │  Safety                               │                   │
│           │  Destructive tools: [require approval]│                   │
│           │  External actions: [require approval] │                   │
│           │                                       │                   │
│           │  [Save]                               │                   │
└───────────┴───────────────────────────────────────┴───────────────────┘
```

### Spec 5: Inbox / Signals View

**Context**: Clo has accumulated workspace signals that need user attention.

```
┌───────────┬───────────────────────────────────────┬───────────────────┤
│           │  Inbox — 3 Signals                    │                   │
│  [nav]   │                                       │                   │
│           │  ┌─────────────────────────────────┐  │                   │
│           │  │ 📨 Backlog Review Needed        │  │                   │
│           │  │ Plan: Array Foundation          │  │                   │
│           │  │ 3 items stuck >24h              │  │                   │
│           │  │ [Start Lead Item] [Defer]       │  │                   │
│           │  └─────────────────────────────────┘  │                   │
│           │                                       │                   │
│           │  ┌─────────────────────────────────┐  │                   │
│           │  │ 📨 Context Review Needed        │  │                   │
│           │  │ Session: Array Foundation       │  │                   │
│           │  │ Token pressure approaching 75%  │  │                   │
│           │  │ [Pause Plan] [Dismiss]           │  │                   │
│           │  └─────────────────────────────────┘  │                   │
│           │                                       │                   │
│           │  ┌─────────────────────────────────┐  │                   │
│           │  │ 📨 Handoff Ready                │  │                   │
│           │  │ Session: Legacy Review          │  │                   │
│           │  │ Rollover complete, ready resume │  │                   │
│           │  │ [Resume] [Dismiss]              │  │                   │
│           │  └─────────────────────────────────┘  │                   │
└───────────┴───────────────────────────────────────┴───────────────────┘
```

---

## 9. Prioritized Implementation List

### Phase 1: Identity and Structure (Foundational — do first)

1. **Replace "OpenCloset" brand with "Clo · OpenCloset"** in header. Clo first.
2. **Remove Builder/Workspace mode toggle**. Replace with context-driven center pane behavior.
3. **Remove "Runtime" and "Workspace Views" sections from left nav**. Workspace views become workspace-level menu items.
4. **Rename assistant avatar/label from 🤖 to "Clo"**.
5. **Remove `DEFAULT_MODEL` constant from UI**. Model is a session config, not a UI constant.
6. **Fix capture source `phonecloset` → `phone`** (or `mobile`).

### Phase 2: Chat Surface (Core usability)

7. **Implement turn-grouped chat rendering** — group messages by turn, show tool blocks inline, show status bar after turn completes.
8. **Implement collapsible tool blocks** — collapsed by default, expanded on failure.
9. **Implement run status bars** — ✅/❌/⏸ with metadata (tools, tokens, time, reason).
10. **Add action buttons to failed/interrupted status bars** — Retry, Continue, Resume, Adjust Plan.
11. **Differentiate Clo messages from system/tool messages** visually.

### Phase 3: Header and Context Strip (Operational visibility)

12. **Redesign header as operational context strip** — workspace, plan progress, run state, context pressure, model all visible.
13. **Add plan progress indicator** (X/Y items · Z%) to header.
14. **Show current plan item** in header subline.
15. **Condense workspace subline** — remove noise, show provider · model · run state.

### Phase 4: Dock Refinement (Operational surface)

16. **Reduce collapsed dock pills to 4** — Run, Context, Signals, Model.
17. **Reduce expanded dock to 3 sections** — Run Detail, Signals & Inbox, Context & Resources.
18. **Move signal inline actions to dock** — user can act on signals from the dock without expanding to right panel.
19. **Add substrate/authority placeholders** — reserved for future, hidden when absent.

### Phase 5: Right Panel and Settings (Control surfaces)

20. **Remove Queue tab from right panel** — queue visibility moves to dock.
21. **Condense plan tab during active sessions** — show execution view (next item, progress, proposals) instead of full plan editor.
22. **Move Settings to dedicated view** — replace right panel settings tab with full Settings view.
23. **Improve tool policy presentation** — group by access level (local, planning, external) with clear trust indicators.

### Phase 6: Future-Proofing (Reserved)

24. **Add Closet section placeholder to left nav** — hidden until paired nodes exist.
25. **Add substrate attribution to chat messages** — show model used per turn (for future multi-substrate routing).
26. **Add authority display to header** — show which node holds session authority (for future multi-node).
27. **Add array node status to dock** — online node count (for future multi-node).

---

## 10. Answers to Priority Questions

### What should the main screen feel like when Clo is actively building OpenCloset?

It should feel like a collaborative work session, not a chat window. Clo is speaking and acting, tool results are visible but not overwhelming, the plan drives the conversation, and the operational state (run progress, context pressure, signals) is always visible. The header tells you what workspace, what plan, what item, what state. The dock tells you what's happening now. The chat surface shows Clo's work in readable turns.

### What runtime state must always be visible?

1. **Run state** — Running/Queued/Idle/Failed/Interrupted. You need to know if Clo is working.
2. **Context pressure** — Token %. You need to know how much runway is left before rollover.
3. **Open signals** — Count of inbox items needing attention. You need to know if Clo is flagging something.
4. **Current model/substrate** — What's powering Clo right now.

These four belong in the collapsed dock strip. Always visible, always scannable.

### How should tool activity be readable without becoming spam?

Collapsible blocks within Clo's turn. By default, show tool name + status badge + truncated first line. Expand on click. Failed tools expand automatically. After the turn completes, the status bar summarizes: "Turn 5 · 3 tools · 1 failed · 340 tokens · 4.2s". The user sees the summary without reading every detail, and can dig into specifics when needed.

### Is the current Builder/Workspace split still right?

No. It's an outdated artifact from when OpenCloset was conceived as "chat app + build tool." Under v5, there's one product with sessions that can focus on different things. A session focused on a build project with an active plan is not a different mode — it's just a session. The UI should render contextually: session selected → chat surface, no session → workspace dashboard.

### What parts of the current UI encode outdated assumptions from pre-v5 OpenCloset?

1. **"OpenCloset" without Clo** — the persona/runtime distinction isn't reflected
2. **Builder/Workspace mode** — implies two products, not one
3. **🤖 assistant avatar** — generic bot, not Clo
4. **Runtime in left nav** — static, non-functional, wrong location for runtime state
5. **Workspace Views in left nav** — treats workspace-level views as first-class nav items alongside sessions
6. **phonecloset capture source** — outdated product identity
7. **Hardcoded default model** — substrate should be a runtime concept
8. **Queue tab in right panel** — operational state belongs in the dock, not the panel
9. **Settings buried in right panel** — safety/trust controls deserve a proper surface
10. **Tool results as plain text** — doesn't scale for real coding work with multiple tool calls per turn
