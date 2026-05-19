// Typed fetch wrappers over /api/*.

import type {
  ActivityHeatmap, AgentFanout, AttentionFeed, ContextHealth, CostSource,
  DecisionsList,
  DispatcherState, EditDecisions, FirehoseEvent, HookActivity, InboxList,
  LiveSessionsList, LiveSessionState, McpServers, McpTools, ProjectBreakdown,
  Productivity, SchedulesList, ScheduleRuns, SessionDetails, SessionFailure,
  SessionList, SessionOutcomes, SkillPreset, SkillRow, SkillsEconomics,
  SkillsList, Sparklines,
  Summary, SystemHealth, SystemPressure, TasksList, TelegramStatus, ToolLatency,
  UsageCache, UsageTokens,
} from './types';

export type Range = 'today' | '7d' | '30d';

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Accept': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new ApiError(res.status, body || res.statusText, path);
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(public status: number, public body: string, public path: string) {
    super(`${status} ${path}: ${body.slice(0, 200)}`);
    this.name = 'ApiError';
  }
}

const qs = (obj: Record<string, unknown>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  return p.size ? `?${p}` : '';
};

// -- Summary + system --
export const getSummary = () => j<Summary>('/api/summary');
export const getSystemHealth = () => j<SystemHealth>('/api/system/health');
export const getAttention = () => j<AttentionFeed>('/api/attention');
export const getSystemPressure = () => j<SystemPressure>('/api/system/pressure');
export const getDispatcherState = () => j<DispatcherState>('/api/system/dispatcher');
export const getTelegramStatus = () => j<TelegramStatus>('/api/system/telegram');
export const postSync = () =>
  j<{ sessions: unknown; cowork: unknown; skills: unknown }>('/api/sync', { method: 'POST' });
export const emergencyStop = () =>
  j<{ stopped: boolean; processes_killed: number; interactive_spared: number }>(
    '/api/system/emergency-stop', { method: 'POST' },
  );
export const emergencyResume = () =>
  j<{ resumed: boolean }>('/api/system/emergency-resume', { method: 'POST' });

// -- Sessions --
export const listSessions = (p: {
  range?: Range; source?: string; model?: string; cost_source?: CostSource;
  limit?: number; offset?: number; q?: string;
} = {}) =>
  j<SessionList>(`/api/sessions${qs(p)}`);
export const liveSessions = () => j<LiveSessionsList>('/api/sessions/live');
export const sessionDetails = (id: string) => j<SessionDetails>(`/api/sessions/${encodeURIComponent(id)}/details`);
export const liveSessionState = (id: string) => j<LiveSessionState>(`/api/sessions/live/${encodeURIComponent(id)}/state`);
export const postLiveMessage = (id: string, body: string) =>
  j<{ queued: boolean; mailbox: string }>(`/api/sessions/live/${encodeURIComponent(id)}/message`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
  });
export const sessionFailures = (range: Range = '30d', limit = 25) =>
  j<{ range: string; items: SessionFailure[]; count: number }>(`/api/sessions/failures${qs({ range, limit })}`);

// -- Observability --
export const getUsageTokens = (range: Range = '7d') => j<UsageTokens>(`/api/usage/tokens?range=${range}`);
export const getUsageCache = (range: Range = '7d') => j<UsageCache>(`/api/usage/cache?range=${range}`);
export const getSessionOutcomes = (range: Range = '7d') => j<SessionOutcomes>(`/api/sessions/outcomes?range=${range}`);
export const getToolLatency = (range: Range = '7d') => j<ToolLatency>(`/api/tools/latency?range=${range}`);
export const getHookActivity = (range: Range = '7d') => j<HookActivity>(`/api/hooks/activity?range=${range}`);
export const getProjectBreakdown = (range: Range = '7d') => j<ProjectBreakdown>(`/api/sessions/by-project?range=${range}`);
export const getAgentFanout = (range: Range = '7d') => j<AgentFanout>(`/api/tools/agent-fanout?range=${range}`);
export const getEditDecisions = (range: Range = '7d') => j<EditDecisions>(`/api/tools/edit-decisions?range=${range}`);
export const getProductivity = (range: Range = '7d') => j<Productivity>(`/api/activity/productivity?range=${range}`);
export const getSummarySparklines = () => j<Sparklines>('/api/summary/sparklines');
export const getActivityHeatmap = (range: Range = '30d') => j<ActivityHeatmap>(`/api/activity/heatmap?range=${range}`);

// -- MCP --
export const getMcpServers = (range: Range = '7d') => j<McpServers>(`/api/mcp?range=${range}`);
export const getMcpTools = (server: string, range: Range = '7d') =>
  j<McpTools>(`/api/mcp/${encodeURIComponent(server)}/tools?range=${range}`);
export const postMcpSync = () => j<{ ok: true; servers: number }>('/api/mcp/sync', { method: 'POST' });

// -- Skills --
export const listSkills = () => j<SkillsList>('/api/skills');
export const postSkillsSync = () => j<{ ok: true; discovered: number }>('/api/skills/sync', { method: 'POST' });
export const skillsEconomics = (range: Range = '30d') => j<SkillsEconomics>(`/api/skills/economics?range=${range}`);
export const patchSkillAutonomy = (name: string, level: 'auto' | 'review' | 'manual') =>
  j<{ updated: boolean; name: string; autonomy_level: string }>(
    `/api/skills/${encodeURIComponent(name)}/autonomy`,
    { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ autonomy_level: level }) },
  );

// v0.6.0 — launcher. PATCH replaces (not merges) the preset; pass `null`
// to clear. POST launches a one-click task that runs as api_pool spend.
export const patchSkillPreset = (name: string, preset: SkillPreset | null) =>
  j<SkillRow>(`/api/skills/${encodeURIComponent(name)}/preset`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(preset),
  });

export const launchSkill = (name: string, opts: { description_override?: string } = {}) =>
  j<{ task_id: number; status: string; skill: string }>(
    `/api/skills/${encodeURIComponent(name)}/launch`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    },
  );

// -- Context --
export const getContextHealth = () => j<ContextHealth>('/api/context/health');

// -- HITL --
export const listDecisions = (status?: 'pending' | 'answered') =>
  j<DecisionsList>(`/api/decisions${qs({ status })}`);
export const answerDecision = (id: number, answer: string) =>
  j<{ answered: boolean }>(`/api/decisions/${id}/answer`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer }),
  });

export const listInbox = (p: { unread?: 0 | 1; max_age_days?: number } = {}) =>
  j<InboxList>(`/api/inbox${qs(p)}`);
export const markInboxRead = (id: number) =>
  j<{ read: boolean }>(`/api/inbox/${id}/read`, { method: 'POST' });
export const replyInbox = (id: number, body: string) =>
  j<{ replied: boolean; id: number }>(`/api/inbox/${id}/reply`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body }),
  });

// -- Tasks --
export const listTasks = (p: { status?: string; quadrant?: string; limit?: number } = {}) =>
  j<TasksList>(`/api/tasks${qs(p)}`);
export interface NewTask {
  title: string;
  description?: string;
  priority?: number;
  execution_mode?: 'classic' | 'stream';
  requires_approval?: boolean;
  risk_level?: string;
  dry_run?: boolean;
  quadrant?: 'do' | 'schedule' | 'delegate' | 'archive';
  assigned_skill?: string;
  model?: string;
  scheduled_for?: string;
}
export const createTask = (t: NewTask) =>
  j<{ id: number; created: boolean }>('/api/tasks', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(t),
  });
export const deleteTask = (id: number) =>
  j<{ deleted: number }>(`/api/tasks/${id}`, { method: 'DELETE' });
export const approveTask = (id: number) =>
  j<{ approved: boolean }>(`/api/tasks/${id}/approve`, { method: 'POST' });
export const rerunTask = (id: number) =>
  j<{ rerun: boolean; task_id: number }>(`/api/tasks/${id}/rerun`, { method: 'POST' });
export const patchTask = (id: number, patch: Partial<NewTask> & { status?: string }) =>
  j<{ updated: boolean }>(`/api/tasks/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
  });
export const triggerDispatcher = () =>
  j<{ triggered: boolean; pid?: number; note?: string; error?: string }>('/api/dispatcher/trigger', { method: 'POST' });

// -- Schedules --
export const listSchedules = () => j<SchedulesList>('/api/schedules');
export interface NewSchedule {
  name?: string; cron_expression: string; task_title: string;
  task_description?: string; assigned_skill?: string; enabled?: boolean; next_run_at?: string;
}
export const createSchedule = (s: NewSchedule) =>
  j<{ id: number; created: boolean }>('/api/schedules', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(s),
  });
export const patchSchedule = (id: number, p: Partial<NewSchedule>) =>
  j<{ updated: boolean }>(`/api/schedules/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p),
  });
export const deleteSchedule = (id: number) =>
  j<{ deleted: number }>(`/api/schedules/${id}`, { method: 'DELETE' });
export const scheduleRuns = (id: number, limit = 10) =>
  j<ScheduleRuns>(`/api/schedules/${id}/runs?limit=${limit}`);
export const parseNl = (text: string) =>
  j<{ cron_expression: string; note: string; input: string }>('/api/schedules/parse-nl', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }),
  });

// -- Firehose SSE source (consumer wraps with EventSource) --
export const firehoseUrl = (event_name?: string) =>
  `/api/firehose${qs({ event_name })}`;

export type { FirehoseEvent };
