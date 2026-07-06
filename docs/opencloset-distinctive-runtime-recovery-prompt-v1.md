# OpenCloset Distinctive Runtime Recovery Prompt v1

## Ground Truth Before You Start

OpenCloset is no longer trying to win by replacing OpenClaw's execution runtime. The current direction is:

- OpenClaw should become the trusted execution substrate.
- OpenCloset should justify itself as the continuity, orchestration, workspace, planning, ambient-runtime, and cross-device coordination layer.
- The UI should stay intentionally minimal, but not so minimal that it hides the very systems that make OpenCloset distinct.

The problem to solve is not "invent a new product from scratch". The problem is "recover and expose the parts of OpenCloset that already make it valuable, while letting OpenClaw own execution reliability."

## Repo Facts You Must Treat As Real

Use the codebase as the source of truth. Do not assume these systems are hypothetical. They already exist in meaningful form.

### Existing backend workers and runtime seams

- [opencloset/api/api/app.py](d:/openclaw/opencloset/api/api/app.py) wires and starts these background systems:
  - `maintenance_worker`
  - `workspace_runtime_worker`
  - `watchdog`
  - `scheduler_worker`
  - `clo_queue_worker`
  - `delegation_worker`
- [opencloset/api/api/app.py](d:/openclaw/opencloset/api/api/app.py) also wires:
  - `workspace_runtime`
  - `reflective_pastimes`
  - `scheduler_arbiter`
  - `briefing`
  - `agent_channels`

### Existing distinctive ambient/runtime behaviors

- [opencloset/api/api/maintenance.py](d:/openclaw/opencloset/api/api/maintenance.py): idle maintenance and maintenance artifacts.
- [opencloset/api/api/workspace_runtime.py](d:/openclaw/opencloset/api/api/workspace_runtime.py): workspace candidate production, signal emission, worker registry, selected pastime matching, runtime snapshots, and signal actions.
- [opencloset/api/api/reflective_pastimes.py](d:/openclaw/opencloset/api/api/reflective_pastimes.py): fresh-eyes reflective passes that generate reflection notes and thread candidates as durable captures.
- [opencloset/api/api/clo_queue.py](d:/openclaw/opencloset/api/api/clo_queue.py): multi-session sequential queue with reorder, cancel, pause-on-error, background worker dispatch, and SSE state stream.
- [opencloset/api/api/delegation.py](d:/openclaw/opencloset/api/api/delegation.py): read-only delegation task system with background worker execution.
- [opencloset/api/api/briefing.py](d:/openclaw/opencloset/api/api/briefing.py): workspace return-briefing generation.
- [opencloset/api/api/events.py](d:/openclaw/opencloset/api/api/events.py): canonical event taxonomy including scheduler, worker, pastime, reflection, thread-candidate, prompt-blocker, and trust-floor runtime events.

### Existing UI surfaces that still exist or partially exist

- [opencloset/ui/src/layout/DesktopShell.tsx](d:/openclaw/opencloset/ui/src/layout/DesktopShell.tsx): current shell loads chat, transient windows, captures, evidence, delegations, queue state, and interactive process handling.
- [opencloset/ui/src/components/workspace/TreeViewHome.tsx](d:/openclaw/opencloset/ui/src/components/workspace/TreeViewHome.tsx): current home view already exposes a real `Clo Queue` with queued items, recent items, pause-on-error, reordering, and session targeting.
- [opencloset/ui/src/components/workspace/WorkspaceDashboard.tsx](d:/openclaw/opencloset/ui/src/components/workspace/WorkspaceDashboard.tsx): a richer workspace operating picture component already exists.
- [opencloset/ui/src/components/dock/RuntimeDock.tsx](d:/openclaw/opencloset/ui/src/components/dock/RuntimeDock.tsx): a richer runtime dock component already exists.
- [opencloset/ui/src/components/chatnative/artifacts/ArtifactRenderers.tsx](d:/openclaw/opencloset/ui/src/components/chatnative/artifacts/ArtifactRenderers.tsx): `WorkspaceDashboard` and `RuntimeDock` are only visibly wired through chat-native artifact rendering paths.
- [opencloset/ui/src/api/client.ts](d:/openclaw/opencloset/ui/src/api/client.ts): `getWorkspaceRuntime()` exists.
- [opencloset/ui/src/api/types.ts](d:/openclaw/opencloset/ui/src/api/types.ts): the UI already has types for `WorkspaceRuntimeRecord`, `WorkspacePastimeRecord`, `WorkspaceSignalRecord`, `WorkspaceWorkerRecord`, captures, evidence, delegations, and transient windows.

### Important current gap

The current shell appears to fetch and render chat-side context, queue state, transient windows, captures, evidence, and delegations, but the workspace runtime snapshot is not obviously being loaded into the main shell. In other words:

- the backend still has workers, signals, pastimes, and ambient orchestration,
- the UI still has types and even some dedicated components for them,
- but the main user experience no longer foregrounds them.

That is the core tension this prompt is about.

## The Assignment

Audit OpenCloset very deeply and answer the following question:

**If OpenClaw becomes the execution substrate, what must OpenCloset visibly do in the backend and UI to justify its existence as a continuity-and-orchestration product, without falling back into panel spam or trying to reimplement OpenClaw?**

You must reason from the actual repo state, not from generic product intuition.

## Core Product Thesis To Preserve

You must preserve all of the following:

- OpenClaw is trusted for execution, tool use, and runtime reliability.
- OpenCloset is trusted for continuity, plans, workspaces, overnight flow, ambient reflection, inboxing, queueing, handoff, and orchestration.
- OpenCloset should remain chat-native, but not chat-only.
- Minimalism is still desirable, but not if it makes the orchestration layer invisible.
- Transient windows remain important because they let the user wake up to concrete reports and artifacts.
- The user wants a real `Clo Queue`: not cron, but a manually composed assembly line of prompts across sessions, where one finishes and the next starts.
- The future product must support a satellite array: PC, phone, and other nodes working together, with the phone acting as both input surface and capable satellite node.
- The user specifically wants voice rambles and phone-native affordances to become machine-ready prompts and outputs for larger models running elsewhere.

## What You Must Produce

Produce a long, rigorous answer with the following sections.

### 1. Distinctive Capability Audit

Identify the backend capabilities that make OpenCloset distinct from a plain chat shell.

You must include at minimum:

- maintenance artifacts and idle maintenance
- workspace runtime candidates and signals
- reflective pastimes
- delegation
- clo queue
- transient windows
- captures and evidence
- workspace briefings
- attention/context guard surfaces
- scheduler/ambient worker orchestration

For each capability, answer:

- What it currently does in the backend.
- What user-facing value it implies.
- Whether it is currently visible, partially visible, or effectively hidden in the UI.
- Whether it should become a primary surface, a secondary surface, or a background-only surface once OpenClaw owns execution.

### 2. What We Lost

Explain what was lost when the UI became too minimal.

Be specific. Distinguish between:

- things we truly removed,
- things still present in code but no longer surfaced,
- things surfaced only in obscure or artifact-only flows,
- things that exist as types/components but are not part of the main operating loop.

I want an explicit list of the "lost feeling of OpenCloset" and the technical/UI reasons that happened.

### 3. UI Recovery Without Regression Into Clutter

Design a UI information architecture that restores OpenCloset's identity without going back to uncontrolled panel sprawl.

You must propose a concrete structure for:

- primary chat surface
- workspace overview surface
- runtime/ambient dock or rail
- clo queue surface
- inbox/review surface for signals, proposals, captures, delegations
- transient window relationship to chat
- overnight/return-in-the-morning reporting flow
- mobile/phone bridge entry points

The design must answer:

- What should always be visible?
- What should be collapsible?
- What should open only when there is something worth seeing?
- What should be rendered as chat-adjacent artifacts or transient windows instead of permanent chrome?

Do not give generic UX advice. Use the actual runtime concepts in this repo.

### 4. Clo Queue Productization

Treat `Clo Queue` as a first-class feature.

Design it as an overnight orchestration surface where the user can:

- line up prompts across different sessions,
- control ordering,
- pause on error,
- trust sequential execution,
- wake up to transient reports, captures, evidence, and updated plans.

You must specify:

- why this queue matters now that OpenClaw will own execution,
- what state the queue should show before, during, and after overnight runs,
- what artifacts should be generated automatically,
- how queue items should relate to sessions, plans, workspaces, and transient windows,
- what additional backend or UI work is still missing even though the queue backend already exists.

### 5. Satellite Array and Phone Node Direction

Design how OpenCloset should evolve into the orchestrator for a multi-node personal compute array.

This must include:

- the phone as a capture, control, and possibly execution node,
- transforming rambles, voice notes, images, and phone-native actions into machine-ready prompts or artifacts,
- routing work from phone to PC to larger models,
- returning useful outputs back to the phone,
- preserving workspace continuity across devices,
- how OpenCloset should think about node capability, role, and orchestration without trying to duplicate OpenClaw's execution core.

Be concrete about what belongs to OpenCloset policy/orchestration and what belongs to OpenClaw substrate execution.

### 6. Immediate Backend/UI Re-exposure Plan

Give a short implementation plan for the next practical milestone.

I want a staged recommendation such as:

- Stage 1: re-expose already-existing runtime state with minimal backend changes.
- Stage 2: connect queue outcomes to windows/evidence/briefings.
- Stage 3: restore ambient worker visibility and workspace review flow.
- Stage 4: establish satellite/phone bridge primitives.

Each stage should include:

- files or systems most likely to change,
- whether the work is mostly backend, frontend, or contract work,
- what user-visible improvement would result.

### 7. Hard Boundaries

End with a section called `Hard Boundaries` that clearly states:

- what OpenCloset should stop trying to own,
- what OpenCloset absolutely should own,
- what must remain background-only,
- what must become visible again if OpenCloset is going to matter.

## Constraints

Follow these constraints strictly:

- Do not propose rebuilding a second execution loop beside OpenClaw.
- Do not flatten OpenCloset into "just a prettier chat app."
- Do not recommend bringing back every old panel uncritically.
- Do not confuse transient windows with clutter; use them as purposeful morning-report and artifact surfaces.
- Do not ignore existing code just because the current UI hides it.
- Do not give shallow product language. Tie every major claim to actual backend/runtime structures already present in the repo.

## Desired Tone

Be ambitious, technical, and unsentimental.

I want the answer to feel like:

- a deep architectural/product recovery memo,
- grounded in code,
- focused on how OpenCloset can become visibly valuable again once OpenClaw owns execution.

## One-Sentence Summary

OpenCloset should win by making ambient work, continuity, queueing, reflection, review, and cross-device orchestration visible and trustworthy, not by trying to out-execute OpenClaw.