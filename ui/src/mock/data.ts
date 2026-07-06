// Mocked data for OpenCloset UI shell
// Simple, readable, representative. Not backend-correct.

export type Workspace = {
  id: string;
  name: string;
  description: string;
  icon: string;
  status: 'active' | 'paused' | 'archived';
  buildProjects: string[];
};

export type BuildProject = {
  id: string;
  name: string;
  workspaceId: string;
  status: 'planning' | 'building' | 'review' | 'complete';
  progress: number;
  planId: string;
};

export type Session = {
  id: string;
  title: string;
  workspaceId: string;
  buildProjectId?: string;
  planId?: string;
  mode: 'builder' | 'workspace';
  lastActive: string;
  messageCount: number;
};

export type PlanItem = {
  id: string;
  title: string;
  status: 'pending' | 'active' | 'done' | 'blocked';
  priority: 'high' | 'medium' | 'low';
  assignee?: string;
  note?: string;
};

export type PlanSummary = {
  id: string;
  title: string;
  items: PlanItem[];
  progress: number;
  activeIndex: number;
};

export type Proposal = {
  id: string;
  title: string;
  summary: string;
  source: 'clo' | 'buddy' | 'worker' | 'autonomy';
  type: 'plan-change' | 'file-change' | 'review' | 'action';
  urgency: 'now' | 'soon' | 'later';
  status: 'pending' | 'approved' | 'rejected' | 'auto-applied';
  createdAt: string;
};

export type BriefingItem = {
  id: string;
  title: string;
  summary: string;
  type: 'completed' | 'changed' | 'queued' | 'signal' | 'proposal';
  significance: 'high' | 'medium' | 'low';
  timestamp: string;
};

export type RuntimeCandidate = {
  id: string;
  name: string;
  source: 'maintenance' | 'worker' | 'pastime' | 'autonomy' | 'cron';
  priority: number;
  status: 'running' | 'queued' | 'skipped' | 'idle';
  reason?: string;
};

export type RuntimeTask = {
  id: string;
  name: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  duration: string;
  progress?: number;
  startedAt: string;
  workspaceId: string;
};

export type CaptureItem = {
  id: string;
  content: string;
  source: 'phone' | 'manual' | 'ambient';
  workspaceId?: string;
  status: 'raw' | 'normalized' | 'linked';
  createdAt: string;
};

export type ChatMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  attachments?: string[];
  planRef?: string;
  toolUse?: { name: string; result: string };
};

// --- Mock Data ---

export const mockWorkspaces: Workspace[] = [
  {
    id: 'ws-opencloset',
    name: 'OpenCloset',
    description: 'Local-first AI harness for solo builders',
    icon: '🏗️',
    status: 'active',
    buildProjects: ['bp-ui-shell', 'bp-scheduler'],
  },
  {
    id: 'ws-personal',
    name: 'Personal',
    description: 'Day-to-day operations and captures',
    icon: '🏠',
    status: 'active',
    buildProjects: [],
  },
  {
    id: 'ws-research',
    name: 'Research',
    description: 'Exploration, papers, and notes',
    icon: '🔬',
    status: 'paused',
    buildProjects: [],
  },
];

export const mockBuildProjects: BuildProject[] = [
  {
    id: 'bp-ui-shell',
    name: 'UI Shell',
    workspaceId: 'ws-opencloset',
    status: 'building',
    progress: 35,
    planId: 'plan-ui',
  },
  {
    id: 'bp-scheduler',
    name: 'Scheduler Core',
    workspaceId: 'ws-opencloset',
    status: 'planning',
    progress: 10,
    planId: 'plan-scheduler',
  },
];

export const mockSessions: Session[] = [
  {
    id: 'sess-001',
    title: 'UI Shell Build',
    workspaceId: 'ws-opencloset',
    buildProjectId: 'bp-ui-shell',
    planId: 'plan-ui',
    mode: 'builder',
    lastActive: '2026-05-01T18:30:00Z',
    messageCount: 47,
  },
  {
    id: 'sess-002',
    title: 'Scheduler Design Review',
    workspaceId: 'ws-opencloset',
    planId: 'plan-scheduler',
    mode: 'builder',
    lastActive: '2026-05-01T15:12:00Z',
    messageCount: 23,
  },
  {
    id: 'sess-003',
    title: 'Daily Check-in',
    workspaceId: 'ws-personal',
    mode: 'workspace',
    lastActive: '2026-05-01T12:45:00Z',
    messageCount: 12,
  },
  {
    id: 'sess-004',
    title: 'Context Maintenance',
    workspaceId: 'ws-opencloset',
    mode: 'workspace',
    lastActive: '2026-05-01T06:00:00Z',
    messageCount: 5,
  },
];

export const mockPlans: Record<string, PlanSummary> = {
  'plan-ui': {
    id: 'plan-ui',
    title: 'UI Shell Implementation',
    progress: 35,
    activeIndex: 2,
    items: [
      { id: 'pi-1', title: 'Scaffold Vite + React + TS', status: 'done', priority: 'high' },
      { id: 'pi-2', title: 'Build four-zone desktop shell', status: 'done', priority: 'high' },
      { id: 'pi-3', title: 'Implement design tokens & layout system', status: 'active', priority: 'high' },
      { id: 'pi-4', title: 'Build left navigation panel', status: 'pending', priority: 'medium' },
      { id: 'pi-5', title: 'Build center chat pane with mock data', status: 'pending', priority: 'medium' },
      { id: 'pi-6', title: 'Build right context panel (mode-sensitive)', status: 'pending', priority: 'medium' },
      { id: 'pi-7', title: 'Build bottom runtime dock', status: 'pending', priority: 'medium' },
      { id: 'pi-8', title: 'Implement Builder Mode view', status: 'pending', priority: 'low' },
      { id: 'pi-9', title: 'Implement Workspace Mode view', status: 'pending', priority: 'low' },
      { id: 'pi-10', title: 'Build inbox / proposals surface', status: 'pending', priority: 'low' },
      { id: 'pi-11', title: 'Build briefing panel', status: 'pending', priority: 'low' },
    ],
  },
  'plan-scheduler': {
    id: 'plan-scheduler',
    title: 'Scheduler & Background Work',
    progress: 10,
    activeIndex: 0,
    items: [
      { id: 'pi-s1', title: 'Define WorkCandidate schema', status: 'active', priority: 'high' },
      { id: 'pi-s2', title: 'Build eligibility gate engine', status: 'pending', priority: 'high' },
      { id: 'pi-s3', title: 'Implement candidate ranking', status: 'pending', priority: 'high' },
      { id: 'pi-s4', title: 'Build cron job system', status: 'pending', priority: 'medium' },
      { id: 'pi-s5', title: 'Implement idle-tier gating', status: 'pending', priority: 'medium' },
    ],
  },
};

export const mockProposals: Proposal[] = [
  {
    id: 'prop-1',
    title: 'Refactor tool registry schema',
    summary: 'Consolidate tool definitions into a single registry with allowlist enforcement.',
    source: 'clo',
    type: 'plan-change',
    urgency: 'soon',
    status: 'pending',
    createdAt: '2026-05-01T17:00:00Z',
  },
  {
    id: 'prop-2',
    title: 'Add transcript compaction pass',
    summary: 'Background compaction of long sessions to preserve context within token limits.',
    source: 'buddy',
    type: 'review',
    urgency: 'later',
    status: 'pending',
    createdAt: '2026-05-01T14:30:00Z',
  },
  {
    id: 'prop-3',
    title: 'Update provider timeout defaults',
    summary: 'Increase provider timeout for local models to handle larger context windows.',
    source: 'autonomy',
    type: 'file-change',
    urgency: 'now',
    status: 'pending',
    createdAt: '2026-05-01T16:45:00Z',
  },
  {
    id: 'prop-4',
    title: 'Merge branch feat/tool-continuation',
    summary: 'Tool continuation validation passes; ready to merge into main.',
    source: 'clo',
    type: 'action',
    urgency: 'now',
    status: 'approved',
    createdAt: '2026-05-01T10:20:00Z',
  },
];

export const mockBriefing: BriefingItem[] = [
  {
    id: 'bf-1',
    title: 'Test suite updated',
    summary: 'Added 3 new test cases for tool executor edge cases. All 236 tests passing.',
    type: 'completed',
    significance: 'medium',
    timestamp: '2026-05-01T16:00:00Z',
  },
  {
    id: 'bf-2',
    title: 'Context maintenance ran',
    summary: 'Updated 4 memory files, refreshed 2 workspace signals. No captures to process.',
    type: 'completed',
    significance: 'low',
    timestamp: '2026-05-01T14:00:00Z',
  },
  {
    id: 'bf-3',
    title: 'New proposal queued',
    summary: 'CLO proposed refactoring the tool registry schema. Waiting for review.',
    type: 'proposal',
    significance: 'high',
    timestamp: '2026-05-01T17:00:00Z',
  },
  {
    id: 'bf-4',
    title: 'Local model restart detected',
    summary: 'llama.cpp on port 8080 restarted. Provider reconnected successfully.',
    type: 'changed',
    significance: 'medium',
    timestamp: '2026-05-01T12:30:00Z',
  },
  {
    id: 'bf-5',
    title: 'Scheduler idle tier: T2',
    summary: 'System idle for 45 minutes. Running maintenance pastimes only.',
    type: 'signal',
    significance: 'low',
    timestamp: '2026-05-01T13:15:00Z',
  },
];

export const mockRuntimeCandidates: RuntimeCandidate[] = [
  { id: 'rc-1', name: 'Context maintenance pass', source: 'maintenance', priority: 7, status: 'running' },
  { id: 'rc-2', name: 'Workspace signal refresh', source: 'worker', priority: 5, status: 'queued' },
  { id: 'rc-3', name: 'Reflective pastime: plan review', source: 'pastime', priority: 3, status: 'queued' },
  { id: 'rc-4', name: 'Autonomy: file diff check', source: 'autonomy', priority: 6, status: 'skipped', reason: 'user active' },
  { id: 'rc-5', name: 'Daily summary generation', source: 'cron', priority: 4, status: 'idle' },
];

export const mockRuntimeTasks: RuntimeTask[] = [
  {
    id: 'rt-1',
    name: 'Context maintenance pass',
    status: 'running',
    duration: '3m 12s',
    progress: 65,
    startedAt: '2026-05-01T18:00:00Z',
    workspaceId: 'ws-opencloset',
  },
  {
    id: 'rt-2',
    name: 'Test suite execution',
    status: 'completed',
    duration: '12s',
    startedAt: '2026-05-01T16:00:00Z',
    workspaceId: 'ws-opencloset',
  },
  {
    id: 'rt-3',
    name: 'Memory file update',
    status: 'completed',
    duration: '4s',
    startedAt: '2026-05-01T14:00:00Z',
    workspaceId: 'ws-opencloset',
  },
];

export const mockCaptures: CaptureItem[] = [
  {
    id: 'cap-1',
    content: 'Need to think about how workspace signals feed into the scheduler candidate pool. Maybe each worker emits a signal that becomes a candidate?',
    source: 'phone',
    workspaceId: 'ws-opencloset',
    status: 'raw',
    createdAt: '2026-05-01T15:30:00Z',
  },
  {
    id: 'cap-2',
    content: 'Briefing panel should filter by significance, not show every event. Concise over comprehensive.',
    source: 'phone',
    workspaceId: 'ws-personal',
    status: 'normalized',
    createdAt: '2026-05-01T11:00:00Z',
  },
  {
    id: 'cap-3',
    content: 'Token pressure monitor needs a visual indicator in the runtime dock. Maybe a gauge or gradient bar.',
    source: 'manual',
    workspaceId: 'ws-opencloset',
    status: 'linked',
    createdAt: '2026-05-01T09:15:00Z',
  },
];

export const mockChatMessages: ChatMessage[] = [
  {
    id: 'msg-1',
    role: 'user',
    content: 'Let\'s start building the OpenCloset UI shell. I want the four-zone desktop layout with mocked data.',
    timestamp: '2026-05-01T17:30:00Z',
  },
  {
    id: 'msg-2',
    role: 'assistant',
    content: 'Got it. I\'ll scaffold a Vite + React + TypeScript app and build the desktop shell with the four-zone layout: left nav, center chat, right context panel, and bottom runtime dock. Starting with design tokens and the shell layout.',
    timestamp: '2026-05-01T17:31:00Z',
  },
  {
    id: 'msg-3',
    role: 'assistant',
    content: 'Design tokens are defined — dark palette with blue accent, serious and tool-like. Spacing scale, typography, and radius tokens are set. Now building the layout shell.',
    timestamp: '2026-05-01T17:32:00Z',
  },
  {
    id: 'msg-4',
    role: 'system',
    content: 'Tool: write — Created src/theme/tokens.ts (design tokens)',
    timestamp: '2026-05-01T17:32:30Z',
    toolUse: { name: 'write', result: 'success' },
  },
  {
    id: 'msg-5',
    role: 'assistant',
    content: 'Global styles are in place with custom scrollbar styling, selection colors, and utility classes for badges and status indicators. The mock data layer is next — representing all key entities with coherent sample data.',
    timestamp: '2026-05-01T17:33:00Z',
  },
  {
    id: 'msg-6',
    role: 'user',
    content: 'Build out the left navigation with workspace list, build projects, sessions, inbox, and runtime sections.',
    timestamp: '2026-05-01T18:00:00Z',
  },
  {
    id: 'msg-7',
    role: 'assistant',
    content: 'Left nav is built with all five sections. Workspaces show status indicators, build projects show progress bars, sessions show last-active times, and inbox has a count badge. Runtime section links to the runtime view.',
    timestamp: '2026-05-01T18:05:00Z',
  },
  {
    id: 'msg-8',
    role: 'assistant',
    content: 'The desktop shell is coming together. Builder Mode pins the active plan to the right panel, while Workspace Mode shows queue and signals. The runtime dock shows the active task with progress, queued candidates, and model status.',
    timestamp: '2026-05-01T18:20:00Z',
  },
];
