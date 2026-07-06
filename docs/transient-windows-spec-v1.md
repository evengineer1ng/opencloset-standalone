# OpenCloset Transient Windows — Product / Technical Spec v1

---

# Core Thesis

Transient Windows are:

> **Ephemeral, inline, stateful, sandboxed micro-applications generated or instantiated by Clo inside the chat stream.**

They are a core interaction primitive of OpenCloset and should become synonymous with the product experience.

They enable Clo to surface:

* visual reports
* dashboards
* generated websites
* mini-tools
* interactive planners
* terminal/browser/media viewers
* utility controls
* custom one-off visualizations
* structured operational controls for OpenCloset itself

without requiring dedicated hardcoded pages for every use case.

---

# Product Philosophy

## Design Principle

> **Conversation should be able to materialize interface.**

The user describes what they want.

Clo decides whether plain text or a transient window is the better response medium.

The chat remains primary because it carries reasoning, synthesis, tone, nuance, ambiguity handling, and the sense of thinking with Clo.

The transient window is the structured companion artifact.

If visual / interactive / app-like presentation improves utility:

> Clo generates or instantiates a transient window inline.

Refined doctrine:

> **Conversation first, artifact second.**

Not artifact instead of conversation.

---

# Definition

```ts
type TransientWindow =
  | GeneratedMicroApp
  | NativeWindowType
```

Two sourcing modes:

---

## 1. Generated Windows

LLM writes full HTML/CSS/JS applet.

Examples:

* Prospect comparison dashboard
* HTML scouting report
* Architecture diagram visualizer
* Interactive race telemetry chart
* Kanban board
* Custom calculator
* Concept map
* Ad hoc timeline explorer

---

## 2. Native Windows

Prebuilt system windows.

Examples:

* Calendar
* Notes
* Browser
* Terminal
* File Viewer
* Image Viewer
* Audio Player
* Video Player
* Plan Visualizer
* Scheduler Controls
* Workspace Priority Mixer

---

# Key Product Behaviors

---

## Inline Embedded in Chat

Rendered directly in message stream.

```text
User: Compare these prospects visually.

Clo:
Here's a dashboard.

[Transient Window Rendered Inline]
```

No floating panels.
No detached windows.
No separate app mode.

When Clo emits a transient window, it should usually still provide surrounding chat that explains what the user is looking at, summarizes key takeaways, frames caveats or recommendations, and preserves conversational continuity.

---

## User Addressable

Every transient window has conversational identity.

Examples:

```text
"Add a sortable table to that window"
"Save this as an artifact"
"Duplicate it and compare another player"
"Pin that scouting dashboard"
"Turn that into a reusable template"
```

---

## Iterative / Mutable

Windows are not one-shot.

They support iterative refinement:

```text
User: Make the charts larger
User: Add a radar graph
User: Change color palette
User: Add prospect B
```

Clo may:

* mutate existing window
* fork new version
* ask if ambiguous

---

## Pinned / Sticky Mode

Critical UX requirement.

Pinned windows:

> remain surfaced near active conversation area

so user is not forced to scroll upward repeatedly.

Pinning can mean:

* docked "active working transient" region near latest message
* sticky repeated render near bottom
* chat-following pinned clone

Implementation can vary.

But UX goal is:

> **in-progress windows remain accessible during iteration.**

---

# Lifecycle Philosophy

---

## Ephemeral by Default

Transient windows are disposable.

Like mandalas:

> useful in the moment, not permanent clutter.

---

## Auto-Pruned by Relevance

System should retire windows when conversation meaningfully moves on.

Not immediately.

Not manually managed.

Should feel intuitive.

---

## History Retained Minimally

Keep lightweight inspectable history:

```ts
type TransientHistoryEntry = {
  id: string;
  title: string;
  createdAt: string;
  summary: string;
  savedArtifactId?: string;
};
```

Purpose:

* auditability
* recap after context loss
* inspectability
* rollback/reference

---

## Promotion Path

Any transient window may become:

* Artifact
* Canvas doc
* Workspace page
* Template
* Native/prebuilt promoted view (advanced/admin flow)

---

# Architecture Spec

---

# Runtime Model

```ts
Transient Window Runtime
├── Renderer Host
├── Sandbox Executor
├── Window State Store
├── Tool Bridge
├── Persistence Layer
└── Promotion/Artifact Exporter
```

---

## Window Schema

```ts
type TransientWindowRecord = {
  id: string;

  title: string;

  sourceType:
    | "generated"
    | "native";

  nativeType?: string;

  htmlBundle?: {
    html: string;
    css: string;
    js: string;
  };

  state: {
    pinned: boolean;
    minimized: boolean;
    stale: boolean;
    saved: boolean;
  };

  capabilities: {
    network: boolean;
    toolBridge: boolean;
    storage: boolean;
    media: boolean;
  };

  conversationId: string;

  mutable: boolean;

  version: number;

  createdAt: string;
  updatedAt: string;

  summary: string;
};
```

---

# Sandbox Model

Generated windows execute sandboxed.

---

## Security Model

Default:

```ts
capabilities = {
  network: false,
  toolBridge: false,
  storage: false,
  media: false
}
```

Escalate only when needed.

---

## Sandbox Permissions Examples

### Prospect Dashboard

```ts
network: false
toolBridge: false
```

---

### Browser Window

```ts
network: true
toolBridge: limited
```

---

### Interactive Analysis Tool

```ts
toolBridge: true
allowedTools: ["recompute_stats"]
```

---

# Tool Bridge Model

Transient windows may call backend tools.

Via structured RPC/event bridge.

```ts
window.toolBridge.invoke({
  tool: "search_prospect_db",
  args: {...}
});
```

Bridge routes through OpenCloset tool runtime.

---

# Generation Pipeline

---

## Window Creation Flow

```text
1. User asks/implies visual/app output
2. Clo decides transient window appropriate
3. Clo plans window type
4. If native:
      instantiate native window
5. If generated:
      produce HTML/CSS/JS bundle
6. Sandbox render inline
7. Register window in runtime store
8. Return conversational message + window
```

---

## Mutation Flow

```text
1. User references window
2. Clo resolves target window
3. Determines mutate vs fork
4. Regenerates patch/full replacement
5. Re-renders window
6. Increment version/history
```

---

# Heuristic Policy for Auto-Surfacing

Clo should proactively create transient windows when:

---

## Strong Candidates

* comparative analysis
* reports
* dashboards
* visual summaries
* planning structures
* timelines
* tabular data
* charts/graphs
* architecture diagrams
* file/browser/media viewing
* generated tools
* operational OpenCloset controls

Use this practical heuristic:

> Would this be meaningfully easier to understand, revisit, compare, or act on as a small interface?

If yes, surface a window.

If maybe, answer in text and optionally offer a window.

If no, stay text-only.

Transient windows are justified when they add one or more of:

* legibility
* comparison
* interaction
* persistence-candidate structure
* cognitive relief

---

## Avoid When

* answer is trivial
* visualization cost > value
* would interrupt conversational flow
* user clearly wants quick text response
* several recent windows already cover the thread
* the task is conversational or emotional and added structure would cheapen it

Special case:

* deterministic error windows should bypass normal hesitation and appear whenever possible because they are instrumentation, not decoration

---

# Native First-Class Windows

Prebuilt windows should still exist.

---

## Initial Native Set

```text
- Plan Visualizer
- Notes
- Calendar
- File Viewer
- Image Viewer
- Audio Player
- Video Player
- Browser
- Terminal
- Artifact Viewer
- Workspace Priority Mixer
- Scheduler Inspector
```

---

## Promotion Pipeline

Generated window can become reusable.

```text
"Promote this scouting dashboard to reusable template"
```

Creates:

```ts
SavedTransientTemplate
```

Potential later admin flow:

```text
Promote to native/prebuilt OpenCloset tool
```

---

# UX / Rendering Requirements

---

## Window Chrome

Minimal but standardized:

```text
[Title]
[Pin]
[Save]
[Duplicate]
[Expand]
[Close]
```

Optional:

```text
[Version]
[Capabilities]
[Refresh]
```

---

## Loading / Generation States

Need explicit render states:

```ts
"generating"
"rendering"
"ready"
"updating"
"failed"
```

---

## Error Handling

If generated app fails:

```text
Window failed to render.
[Retry]
[Show Source]
[Regenerate]
```

---

# Strategic Product Importance

This feature is not ornamental.

It is:

> **the primary mechanism by which OpenCloset transcends ordinary chatbot UX.**

Because it turns Clo from:

```text
assistant that talks about tools
```

into

```text
assistant that manifests tools/interfaces on demand
```

That is a substantial experiential leap.

---

# Recommended MVP Build Slice

To keep implementation tractable:

---

## Phase 1 — Core Generated HTML Runtime

Build:

* Inline iframe/sandbox renderer
* HTML/CSS/JS bundle mounting
* Window registry/store
* Pin/save/close controls
* Mutation/versioning support
* Artifact export of generated source
* Basic history ledger

No tool bridge initially except maybe trivial postMessage.

---

## Phase 2 — Tool-Capable Windows

Add:

* Sandbox RPC bridge
* Permission model
* Backend callable actions
* Interactive recompute/refetch

---

## Phase 3 — Native Window Suite

Build first-party:

* Browser
* Terminal
* Media Viewers
* Calendar
* Scheduler tools
* Workspace controls

---

## Phase 4 — Promotion/Template System

Add:

* Save as reusable template
* Promote to workspace widget
* Admin/native registration flow
