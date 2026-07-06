# OpenCloset Transient Windows — Engineering / Runtime Architecture Spec v1

_Companion to: `transient-windows-spec-v1.md`_
_Scope: Everything needed to safely build Phase TW-1 and TW-2 without backing into architectural dead ends._

---

## 0. What This Spec Is For

The product spec defines **what** transient windows are.

This spec defines **how to build the runtime safely** — with explicit calls on every non-obvious decision point before code is written.

The traps documented here are not theoretical. Each one is a decision that, if deferred, produces a rewrite when you try to add the next phase.

---

## 1. Rendering Architecture

### 1.1 The Core Problem

Transient windows live inside a chat message list.

The chat message list is a React-rendered scrollable container.

This creates three structural problems:

**Problem A — React key instability.**
React will unmount and remount iframe elements if their position in the virtual DOM changes (new messages above them, message list updates, etc.). An unmounted iframe loses all JS runtime state — chart zoom, form values, scroll position, WebSocket connections.

**Problem B — Sticky/pin is impossible inside a scroll container.**
CSS `position: sticky` works relative to the nearest scrolling ancestor. A window inside the chat scroll container cannot stick to the viewport edge while also staying visually associated with its originating message.

**Problem C — Multiple windows compete for render order.**
If two windows are open and the user pins one, the pinned window should escape the scroll container without being removed from the message history.

### 1.2 Decision: Portal Architecture

**Resolution: Render iframes outside the chat scroll container via React portals.**

```
DOM Layout
├── #app-root
│   ├── #chat-container (scrollable)
│   │   └── MessageList
│   │       └── Message
│   │           └── TransientWindowPlaceholder [data-window-id="abc"]
│   └── #transient-window-host (fixed, zero-size, overflow: visible)
│       └── WindowPortal [id="abc"]  ← actual iframe lives here
```

Each `TransientWindowPlaceholder` in the message list:
- Renders a frame with the window's title, chrome controls, and loading/error states
- Uses a `ResizeObserver` or `IntersectionObserver` to track its bounding rect
- Reports its rect to the `WindowRegistry` via a React context

The `WindowPortal` in `#transient-window-host`:
- Renders the actual `<iframe>` absolutely positioned to match the placeholder's rect
- Updates position via CSS custom properties when the placeholder's rect changes
- Is keyed by `window.id` — survives any re-render of the message list

This means:
- The iframe is never unmounted by message list churn
- Pin is implemented by changing the portal's positioning strategy (see §4)
- The placeholder in the message serves as the visual "anchor" but contains no iframe DOM

### 1.3 Window Registry

A singleton `WindowRegistry` (React context + Zustand store or equivalent) tracks:

```ts
type WindowEntry = {
  id: string;
  record: TransientWindowRecord;   // the persisted record
  renderState: "generating" | "rendering" | "ready" | "updating" | "failed" | "minimized";
  placeholderRect: DOMRect | null; // from placeholder observer
  pinned: boolean;
  portalRef: React.RefObject<HTMLIFrameElement>;
};
```

All window operations — create, mutate, pin, close, save — go through the registry. The registry is the single source of truth for render state.

---

## 2. Sandbox Model

### 2.1 The Decision: srcdoc + no-same-origin

Generated windows use `srcdoc` to inject the HTML bundle, with the following `sandbox` attribute:

```html
<iframe
  srcdoc={bundle}
  sandbox="allow-scripts allow-forms allow-modals allow-popups-to-escape-sandbox"
  referrerpolicy="no-referrer"
  ...
/>
```

**Why `srcdoc` over blob URLs:**
- `srcdoc` never acquires a real origin — the frame's `window.location.origin` is `"null"` (string)
- No URL to revoke or manage
- Works inside strict CSP on the parent without `blob:` in `connect-src`
- Simpler lifecycle: set attribute → browser mounts → clear attribute → browser destroys

**Why NOT `allow-same-origin`:**
- Adding `allow-same-origin` to a frame that also has `allow-scripts` is equivalent to no sandbox — the frame can access `parent`, `window.top`, read parent DOM, exfiltrate localStorage
- Never add `allow-same-origin` to generated content

**Why no `allow-top-navigation` or `allow-storage-access-by-user-activation`:**
- Generated content has no business navigating the parent page
- Storage access is gated behind Phase TW-2 capability escalation

**Capability escalation mapping:**

| Capability | Additional sandbox flags |
|---|---|
| `network: true` | _(no change — srcdoc frames can make fetch() with no-same-origin already)_ |
| `toolBridge: true` | _(handled via postMessage, no additional sandbox flags needed)_ |
| `storage: true` | Grant only after Phase TW-2; use `allow-storage-access-by-user-activation` |
| `media: true` | Add `allow-autoplay` |

Note: `network: true` is enforced at the CSP level on the parent page, not the sandbox attribute — see §2.3.

### 2.2 postMessage Channel and the Origin Problem

With `srcdoc`, `event.origin` on messages from the frame is always the string `"null"` — not the `null` JS value, the string `"null"`. You cannot safely filter by origin.

**Resolution: Nonce-based handshake.**

When the window mounts the iframe, it generates a cryptographically random nonce:

```ts
const nonce = crypto.randomUUID(); // or getRandomValues
```

The nonce is injected into the `srcdoc` bundle at a known location:

```html
<!-- injected by renderer before mounting -->
<script>window.__CLO_BRIDGE_NONCE__ = "{{NONCE}}";</script>
```

The frame's bootstrap code sends an `INIT` message immediately on load:

```js
// Inside generated bundle (injected by renderer)
window.parent.postMessage({
  type: "CLO_INIT",
  nonce: window.__CLO_BRIDGE_NONCE__,
  windowId: window.__CLO_WINDOW_ID__,
}, "*");
```

The parent validates:
1. `event.data.type === "CLO_INIT"`
2. `event.data.nonce === expectedNonce` (checked against the registry entry for that window)
3. `event.data.windowId === expectedWindowId`

Only after a successful handshake does the parent register the frame's `MessagePort` or add it to the allowed-sender set. All subsequent messages are validated against the `windowId` + `nonce` pair.

Messages that fail validation are silently dropped and logged.

### 2.3 CSP for the Parent Page

The parent page should have a `Content-Security-Policy` that restricts what `srcdoc` frames can reach:

```
frame-src 'self' blob:;
```

For generated windows with `network: false`, the frame's own fetch/XHR calls will use the frame's null origin, so CORS will block most real network requests anyway. But adding a frame-level CSP via the `csp` attribute provides defense-in-depth:

```html
<iframe
  srcdoc={bundle}
  sandbox="allow-scripts allow-forms allow-modals"
  csp="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:;"
  ...
/>
```

This is a `<iframe csp>` attribute (Chrome 61+, not Safari). Treat as enhancement, not sole defense.

---

## 3. Bundle Format and Injection

### 3.1 What the LLM Produces

The LLM generates a single self-contained HTML document. All CSS and JS are inline — no external imports except what's explicitly whitelisted via capability escalation.

The LLM is prompted to produce:

```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    /* all styles inline */
  </style>
</head>
<body>
  <!-- content -->
  <script>
    // all JS inline
  </script>
</body>
</html>
```

The renderer wraps this with the bridge bootstrap before mounting:

```ts
function injectBridgeBootstrap(rawBundle: string, windowId: string, nonce: string): string {
  const bootstrap = `
<script>
window.__CLO_WINDOW_ID__ = ${JSON.stringify(windowId)};
window.__CLO_BRIDGE_NONCE__ = ${JSON.stringify(nonce)};
window.__CLO_INITIAL_STATE__ = ${JSON.stringify(initialState ?? null)};

// Minimal bridge API injected by renderer
window.clo = {
  ready: () => window.parent.postMessage({
    type: "CLO_INIT",
    nonce: window.__CLO_BRIDGE_NONCE__,
    windowId: window.__CLO_WINDOW_ID__,
  }, "*"),
  emit: (event, data) => window.parent.postMessage({
    type: "CLO_EVENT",
    nonce: window.__CLO_BRIDGE_NONCE__,
    windowId: window.__CLO_WINDOW_ID__,
    event,
    data,
  }, "*"),
};

window.addEventListener("load", () => window.clo.ready());
<\/script>
`;
  // Inject immediately after <head> or before first <script>
  return rawBundle.replace(/<head>/i, `<head>${bootstrap}`);
}
```

### 3.2 Streaming Extraction

The LLM streams its response as text. The bundle is delimited in the stream by a structured tag:

```
<transient-window id="abc" title="Prospect Dashboard" source-type="generated">
<!DOCTYPE html>
...full bundle...
</transient-window>
```

The stream parser state machine:

```
NORMAL → detect "<transient-window" → ACCUMULATING_WINDOW
ACCUMULATING_WINDOW → accumulate into buffer → detect "</transient-window>" → BUNDLE_COMPLETE
BUNDLE_COMPLETE → extract attributes, extract bundle → emit WindowCreatedEvent → NORMAL
```

During `ACCUMULATING_WINDOW`:
- The placeholder renders `renderState: "generating"` with a spinner
- The text outside the tag streams normally into the message content

When `BUNDLE_COMPLETE`:
- The bundle is injected into the srcdoc
- The iframe mounts
- `renderState` transitions: `"generating"` → `"rendering"` → (on `CLO_INIT` handshake) `"ready"`

The 5-second timeout from `"rendering"` → `"failed"` if the `CLO_INIT` message never arrives.

### 3.3 Bundle Storage

Bundles are stored in the backend `transient_windows` table (already partially scaffolded in `storage.py` per session 39 work). The record stores:

```python
@dataclass
class TransientWindowRecord:
    id: str
    session_id: str
    title: str
    source_type: str          # "generated" | "native"
    native_type: str | None
    html: str | None          # raw generated HTML (post-injection)
    css: str | None           # extracted CSS (optional, may be inline)
    js: str | None            # extracted JS (optional, may be inline)
    capabilities: str         # JSON: {network, toolBridge, storage, media}
    state_flags: str          # JSON: {pinned, minimized, stale, saved}
    conversation_id: str
    mutable: bool
    version: int
    summary: str
    created_at: str
    updated_at: str
```

Bundles are stored in full — not just a reference. This is intentional: bundles must be self-contained and reproducible without re-querying the LLM. Bundles for a single session are typically 5–50 KB each; this is acceptable SQLite storage.

---

## 4. Mutation Protocol

### 4.1 The Problem

When the user asks to mutate a window ("make the charts larger", "add prospect B"), the LLM generates a new bundle. Naively replacing the `srcdoc` destroys all iframe runtime state.

For most windows this is acceptable — a dashboard's visual state (which tab is open, chart zoom) is low-value. The data is what matters.

But for windows that accumulate user state (filled forms, drawn annotations, typed notes), destruction is not acceptable.

### 4.2 Decision: Full Replacement with Optional State Snapshot

**Default: full replacement.** The renderer generates a new bundle and replaces `srcdoc`.

**Optional state preservation: snapshot before replace.** Before replacing `srcdoc`, the renderer requests a state snapshot from the frame:

```ts
// Parent → frame
iframe.contentWindow.postMessage({
  type: "CLO_SNAPSHOT_REQUEST",
  nonce,
  windowId,
}, "*");
```

The frame responds:

```js
// Frame → parent (generated bundle must implement this)
window.addEventListener("message", (e) => {
  if (e.data.type === "CLO_SNAPSHOT_REQUEST") {
    window.parent.postMessage({
      type: "CLO_SNAPSHOT",
      nonce: window.__CLO_BRIDGE_NONCE__,
      windowId: window.__CLO_WINDOW_ID__,
      state: window.__captureState?.() ?? null,
    }, "*");
  }
});
```

The LLM is instructed (in the system prompt tool manifest) to implement `window.__captureState()` and `window.__restoreState(state)` when generating stateful windows.

The snapshot is passed to the new bundle as `window.__CLO_INITIAL_STATE__` via the bootstrap injection.

State snapshot is a best-effort mechanism — it works when the LLM implements the protocol. For Phase TW-1, treat it as optional and do not block on it.

### 4.3 Fork vs Mutate

The LLM decides fork vs mutate based on the request:

- **Mutate**: "make the charts larger" — replace in-place, increment `version`, keep same `id`
- **Fork**: "duplicate this and compare with prospect B" — new `id`, `version: 1`, `parentId` reference

The registry maintains both in the window store. The chat message for a fork renders a new placeholder; the original placeholder still points to the original window entry.

Version history is stored as lightweight records in a `transient_window_versions` table — just `(id, window_id, version, html, created_at)` — not full records. Keep the last 5 versions per window. Anything older is pruned on session end.

---

## 5. Pin / Sticky Implementation

### 5.1 The Problem

"Pin" means: this window stays visible near the bottom of the chat even as the user sends new messages and scrolls.

A window `position: sticky` inside a scrollable message list cannot stick to the viewport edge — it sticks to the scroll container's content area, which means it scrolls with new content.

### 5.2 Decision: Dedicated Pin Rail

A **pin rail** is a fixed-position container outside the chat scroll container, anchored to the edge of the chat view:

```
+----------------------------------+
|  Chat header                     |
+------------------+---------------+
|                  | [Pin Rail]    |
|  Message list    | [Window A]    |
|  (scrollable)    | [Window B]    |
|                  |               |
|                  |               |
+------------------+---------------+
|  Input bar                       |
+----------------------------------+
```

The pin rail:
- Is `position: fixed` (or `position: absolute` within the chat layout if preferred)
- Has `overflow-y: auto` if multiple windows are pinned
- Is a separate React tree with its own portal targets

When a window is pinned:
1. `WindowRegistry` sets `pinned: true` for the window entry
2. The portal for that window is re-parented from `#transient-window-host` into `#pin-rail`
3. The iframe itself is NOT remounted — just the portal's parent changes (React portals support this)
4. The placeholder in the message list shows a "pinned" state badge and becomes visually collapsed

When unpinned:
1. Reverse the above
2. Chat scrolls to the placeholder position

**Sizing in the pin rail**: Pinned windows default to a fixed size (e.g., 400×300). The user can resize via a drag handle. Size is stored in the registry entry, not the backend record (it's a session-local UI preference).

### 5.3 Multiple Pinned Windows

If more than one window is pinned:
- Rail shows a tab-strip or stacked cards
- Only one window is "active" at a time in the rail (others collapsed to title bar)
- The rail's total height is capped; scrolls internally if needed

---

## 6. Tool Bridge (Phase TW-2)

### 6.1 Architecture

The tool bridge lets a transient window call OpenCloset backend tools — real-time data queries, plan reads, workspace actions — from inside the sandboxed frame.

Bridge call flow:

```
Frame JS
  └─ window.clo.invoke(tool, args)
       └─ postMessage → parent (validated by nonce)
            └─ WindowRegistry.handleBridgeCall(windowId, tool, args)
                 └─ ToolBridgeRouter.route(call)
                      ├─ PermissionCheck: is tool in window's allowedTools?
                      ├─ ToolExecutor.execute(tool, args)
                      └─ result → postMessage → frame
                               → frame promise resolves
```

### 6.2 Frame-Side API

```js
// Available inside generated bundle when toolBridge: true
const result = await window.clo.invoke("search_prospect_db", { query: "..." });
```

Returns a Promise. Times out after 10 seconds.

Under the hood:

```js
window.clo.invoke = (tool, args) => {
  const callId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Bridge timeout")), 10000);
    window.__pendingBridgeCalls[callId] = { resolve, reject, timer };
    window.parent.postMessage({
      type: "CLO_BRIDGE_CALL",
      nonce: window.__CLO_BRIDGE_NONCE__,
      windowId: window.__CLO_WINDOW_ID__,
      callId,
      tool,
      args,
    }, "*");
  });
};

window.addEventListener("message", (e) => {
  if (e.data.type === "CLO_BRIDGE_RESULT" && e.data.callId in window.__pendingBridgeCalls) {
    const { resolve, reject, timer } = window.__pendingBridgeCalls[e.data.callId];
    clearTimeout(timer);
    delete window.__pendingBridgeCalls[e.data.callId];
    if (e.data.error) reject(new Error(e.data.error));
    else resolve(e.data.result);
  }
});
```

### 6.3 Permission Model

Every tool bridge call is validated against the window's `allowedTools` list before execution:

```python
# Window record carries capability spec
capabilities = {
  "toolBridge": True,
  "allowedTools": ["search_prospect_db", "get_player_stats"],
  "network": False,
  "storage": False,
  "media": False,
}
```

`allowedTools` is set at window creation time by the LLM (in the `<transient-window>` tag attributes) and cannot be modified by the frame at runtime. If a frame calls a tool not in its `allowedTools`, the call is rejected with a permission error and the rejection is logged.

The backend `ToolBridgeRouter` validates the window record against the registry before executing any tool — not just the in-memory permission check. This prevents stale or tampered in-memory state from granting access.

### 6.4 Streaming Bridge Results

For long-running tool calls, the bridge can stream partial results:

```js
await window.clo.stream("analyze_game_footage", { gameId: "..." }, (chunk) => {
  appendToTable(chunk);
});
```

Streaming uses a series of `CLO_BRIDGE_STREAM_CHUNK` messages followed by a `CLO_BRIDGE_STREAM_END`. The same nonce + windowId + callId validation applies to every chunk.

---

## 7. Lifecycle and Pruning

### 7.1 Window States

```
created
  └─ generating       (LLM is writing the bundle)
       └─ rendering   (srcdoc set, waiting for CLO_INIT)
            └─ ready  (handshake complete, live)
                 ├─ updating   (new bundle incoming, may snapshot state)
                 │    └─ ready (after re-mount)
                 ├─ minimized  (window collapsed, iframe preserved in DOM, no re-mount)
                 └─ stale      (conversation has moved on, auto-prunable)
  └─ failed           (bundle generation or mount failed)
```

Transitions:
- `ready → stale`: triggered by the pruning policy (see §7.2)
- `stale → closed`: triggered by pruning, or explicit close
- `closed`: iframe removed from DOM, portal destroyed, record updated in backend

### 7.2 Auto-Pruning Policy

The goal is "intuitive" retirement without user management.

Implementation:

A `WindowPruner` runs on each new user message. It evaluates each open (non-pinned, non-saved) window:

```ts
function shouldMarkStale(window: WindowEntry, context: PruningContext): boolean {
  // Never prune pinned or saved windows
  if (window.pinned || window.record.state_flags.saved) return false;

  // Never prune windows that were referenced by name in the last N messages
  if (context.recentlyReferenced.has(window.id)) return false;

  // Mark stale if the conversation has produced >= 4 new user messages since creation
  if (context.messagesSinceCreation >= 4) return true;

  // Mark stale if the conversation topic clearly shifted (heuristic: new plan item activated)
  if (context.planItemChanged) return true;

  return false;
}
```

Stale windows:
- Are visually marked in the placeholder ("archived" state)
- Are NOT immediately destroyed — kept in DOM for 30 seconds
- Are destroyed (iframe removed) after 30 seconds if still stale
- Can be reopened from the window history list during the 30-second grace period

This is intentionally conservative. Better to keep a few extra stale windows than to prune something the user was about to reference.

### 7.3 History Ledger

When a window is closed or pruned, a history entry is written:

```python
@dataclass
class TransientWindowHistoryEntry:
    id: str
    window_id: str
    session_id: str
    title: str
    summary: str                # 1-2 sentence LLM-generated summary, or title if not available
    created_at: str
    closed_at: str
    saved_artifact_id: str | None
```

The history ledger is surfaced via a `[Window History]` action in the chat chrome. It shows the last 20 closed windows for the session. Clicking one reopens it (remounts the stored bundle).

---

## 8. Native Windows (Phase TW-3)

### 8.1 Native vs Generated

Native windows are React components, not iframes. They:
- Have full access to the app's React context, state, and APIs
- Are registered in the `NativeWindowRegistry` at build time
- Are instantiated by name: `{ sourceType: "native", nativeType: "calendar" }`
- Do not use the sandbox or postMessage bridge

For Phase TW-1 and TW-2, no native windows are needed. The architecture is designed to accommodate them without refactoring.

### 8.2 Native Window Registration

```ts
// In native window registry
registerNativeWindow({
  type: "plan_visualizer",
  component: PlanVisualizerWindow,
  defaultTitle: "Plan",
  defaultSize: { width: 600, height: 400 },
});
```

The `WindowPortal` checks `sourceType`: if `"native"`, it renders the registered React component instead of an iframe. The pin rail, minimize, save, and close controls work identically.

---

## 9. Artifact Export (Promotion Path)

Any generated window can be saved as an artifact.

When saved:
1. The current bundle (post-injection, with all mutations applied) is written to the `artifacts` table
2. The artifact type is `"generated_html"` (extending the existing `ArtifactKind`)
3. The artifact is linked to the current workspace
4. The transient window record's `state_flags.saved` is set to `true`

Saved artifacts:
- Are accessible from the workspace artifact browser
- Can be reopened in a new session by re-mounting the stored bundle in an iframe
- Are immutable snapshots — mutations in the new session fork a new artifact
- Can be promoted to a `SavedTransientTemplate` (Phase TW-4) via a user action

---

## 10. LLM Prompt Integration

### 10.1 System Prompt Additions

The agent system prompt (in `runner.py`'s `DEFAULT_BASE_IDENTITY`) needs additions for Transient Windows awareness:

```
When a response would benefit from visual, interactive, or app-like presentation —
including dashboards, charts, reports, tables, comparisons, timelines, calculators,
or operational controls — produce a transient window instead of plain text.

To create a transient window, wrap your generated HTML bundle in:

<transient-window id="{uuid}" title="{title}" source-type="generated">
<!DOCTYPE html>
...full self-contained HTML/CSS/JS...
</transient-window>

Rules for generated bundles:
- Fully self-contained: no external URLs, no CDN imports unless network capability is granted
- Must call window.clo.ready() on load (injected by renderer — do not write this yourself)
- For stateful windows: implement window.__captureState() returning a JSON-serializable object,
  and window.__restoreState(state) accepting the snapshot
- All styles inline in <style> tags; all JS inline in <script> tags

Do not create a transient window when: the answer is a short text response, the user
clearly wants a quick answer, or the visualization would not add meaningful value.
```

### 10.2 Tool Manifest Extension

For Phase TW-2, add a `window_tool_bridge` tool to the manifest:

```
**window_tool_bridge** — Declare tools that a transient window may call via its tool bridge.
Input schema:
{
  "window_id": "string",
  "allowed_tools": ["tool_name", ...]
}
```

This is called by the LLM when creating a tool-capable window, before the window's `<transient-window>` tag appears in the stream.

---

## 11. Backend Storage Schema

The `transient_windows` table (to be added to `storage.py`):

```sql
CREATE TABLE IF NOT EXISTS transient_windows (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'generated',
    native_type TEXT,
    html TEXT,
    capabilities TEXT NOT NULL DEFAULT '{}',
    state_flags TEXT NOT NULL DEFAULT '{}',
    conversation_id TEXT,
    mutable INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS transient_window_versions (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    html TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (window_id) REFERENCES transient_windows(id)
);

CREATE TABLE IF NOT EXISTS transient_window_history (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    closed_at TEXT NOT NULL,
    saved_artifact_id TEXT
);
```

REST API routes (to be added in `routes.py` or a new `windows.py`):

```
GET    /api/sessions/:id/windows           list open windows
GET    /api/sessions/:id/windows/history   list closed window history
POST   /api/sessions/:id/windows           create window (from agent run)
GET    /api/windows/:id                    get single window
PATCH  /api/windows/:id                    update state_flags, title, version
DELETE /api/windows/:id                    close window
POST   /api/windows/:id/save               promote to artifact
POST   /api/windows/:id/fork               fork to new window
```

---

## 12. Phase TW-1 Build Checklist

Before writing any code, confirm these decisions are understood:

- [ ] Portal architecture is agreed: iframes live outside the message scroll container
- [ ] Nonce handshake is agreed: origin-based postMessage filtering is not used
- [ ] `srcdoc` is agreed: blob URLs are not used
- [ ] `allow-same-origin` is explicitly excluded from the sandbox attribute
- [ ] Full replacement is the default mutation strategy; snapshot is opt-in
- [ ] Pin rail is a separate fixed DOM node, not sticky inside the scroll container
- [ ] Bundle streaming uses `<transient-window>` tag delimiters
- [ ] Backend stores full HTML bundle (not just a reference)
- [ ] Version table keeps last 5 versions per window
- [ ] Auto-pruning fires on new user messages, not on a timer

### Phase TW-1 Build Order

1. Backend schema + API routes (`transient_windows`, history, versions tables)
2. `WindowRegistry` store (Zustand or React context)
3. `TransientWindowPlaceholder` component (rendering shell in message list)
4. `WindowPortal` component (iframe mounting + nonce handshake)
5. Bundle injector (bootstrap injection + srcdoc set)
6. Stream parser extension (detect `<transient-window>` tag in LLM stream)
7. Pin rail DOM node + pin/unpin logic in registry
8. Window chrome controls (pin, save, duplicate, close, minimize)
9. Mutation flow (snapshot request → new bundle → re-mount)
10. Artifact export from window save action
11. History ledger UI (closed window list + reopen)
12. System prompt additions for Transient Windows awareness
13. Backend pruning hook (mark stale on session message count)

---

## 13. Known Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| LLM generates invalid HTML that breaks iframe mount | Medium | Render `"failed"` state with Retry/Show Source; do not crash message renderer |
| LLM ignores `<transient-window>` tag format | Medium | Stream parser falls through gracefully; bundle appears as raw text in message |
| Frame sends malicious postMessage to escape sandbox | Low (srcdoc has null origin) | Nonce validation drops all unrecognized messages; no `allow-same-origin` |
| React portal re-parenting causes iframe reload | Medium | Test explicitly; mitigate with stable `key` on portal; React 18 portals do not remount on parent change if key is preserved |
| Large bundle (50KB+) causes slow mount | Low | Async srcdoc set via `requestIdleCallback`; placeholder spinner during mount |
| Auto-pruning closes a window the user wanted | Medium | 30-second grace period + history reopen; conservative threshold (4 messages) |
| Pin rail occludes chat on small screens | Low | Hide rail behind toggle on narrow viewports; collapse to icon strip |
