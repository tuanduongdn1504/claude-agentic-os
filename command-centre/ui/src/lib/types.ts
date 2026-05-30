// Types — mirror the FastAPI response shapes hit by /api/*.

// Multi-account split (WIP from claude/hopeful-borg-062f4e). Populated by the
// backend's `by_account` rollup keyed by account_id.
export interface AccountSummaryRow {
  label: string;
  sessions: number;
  tokens: number;
  cost_usd: number;
}

// v0.6.0 — cost-source enum. `codex_api` slot reserved for a future
// backend swap; not produced anywhere in this release.
export type CostSource = 'api_pool' | 'max_sub' | 'unknown' | 'codex_api';
export interface CostBySource {
  api_pool: number;
  max_sub: number;
  unknown: number;
  codex_api?: number;
}

export interface Summary {
  sessions_today: number;
  effective_tokens_today: number;
  errors_today: number;
  cost_usd_today: number;
  cost_by_source: CostBySource;
  tools_today: number;
  // WIP — populated only when the backend ships the multi-account UI surface
  // (currently only in the installed deploy, not on main).
  by_account?: Record<string, AccountSummaryRow>;
}

export interface SystemHealth {
  ok: boolean;
  uptime_s: number;
  tz: string;
  mem_rss_mb: number;
  db_size_bytes: number | null;
  last_otel_event_age_s: number | null;
  last_sync_tick_age_s: number | null;
  daemon_last_tick_age_s: number | null;
  notifier_last_tick_age_s: number | null;
  sync_loop_heartbeat_age_s: number | null;
}

export interface AttentionIssue { kind: string; severity: string; [extra: string]: unknown; }
export interface AttentionFeed { issues: AttentionIssue[]; count: number; }

export interface SessionRow {
  session_id: string;
  source: string | null;
  entrypoint: string | null;
  cwd: string | null;
  git_branch: string | null;
  model: string | null;
  title: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_ms: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_create_tokens: number;
  total_tokens: number;
  effective_tokens: number;
  cost_usd: number;
  cost_source: CostSource;
  error_count: number;
  is_error_any: number;
  rate_limit_hit?: number;
  stop_reason: string | null;
  service_tier?: string | null;
  jsonl_mtime?: number;
}
export interface SessionList { items: SessionRow[]; total: number; offset: number; limit: number; }

export interface LiveSessionsList { items: SessionRow[]; window_s: number; }
export interface LiveSessionState {
  session_id: string; state: string | null; current_tool: string | null; updated_at: string | null;
}

export interface ToolTimelineRow {
  tool_use_id: string; tool_name: string; ts: string; duration_ms: number | null;
  error: number; caller: string | null; is_subagent: number; parent_uuid: string | null;
}
export interface SystemEventRow {
  timestamp: string; subtype: string | null; stop_reason: string | null;
  retry_attempt: number | null; max_retries: number | null; retry_in_ms: number | null;
  compact_metadata: string | null; hook_errors: string | null;
}
export interface SessionDetails {
  session: SessionRow;
  tool_timeline: ToolTimelineRow[];
  system_events: SystemEventRow[];
  token_breakdown: Record<string, number>;
  title_source: string | null;
}

export interface SessionFailure {
  session_id: string; title: string | null; cwd: string | null; model: string | null;
  started_at: string | null; ended_at: string | null;
  error_count: number; is_error_any: number; rate_limit_hit: number;
  stop_reason: string | null; cost_usd: number; effective_tokens: number;
  api_errors: number;
}

// -- Observability --
export interface DailyTokenRow {
  date: string; model: string; source: string;
  input_tokens: number; output_tokens: number;
  cache_read_tokens: number; cache_create_tokens: number;
  cost_by_source: CostBySource;
}
export interface UsageTokens {
  range: string; daily: DailyTokenRow[];
  totals: { input: number; output: number; cache_read: number; cache_create: number; total: number };
  cost_by_source: CostBySource;
}

export interface UsageCacheDay {
  date: string; cache_read_tokens: number; input_tokens: number;
  cache_create_tokens: number; billable_tokens: number; hit_rate: number | null;
}
export interface UsageCache {
  range: string; overall_hit_rate: number | null; target_hit_rate: number;
  low_sample: boolean; billable_tokens: number; daily: UsageCacheDay[];
}

export interface OutcomesDay {
  date: string;
  errored: number; rate_limited: number; truncated: number; unfinished: number; ok: number; total: number;
}
export interface SessionOutcomes {
  range: string; daily: OutcomesDay[];
  totals: { errored: number; rate_limited: number; truncated: number; unfinished: number; ok: number; total: number };
  buckets_priority: string[];
}

export interface ToolLatencyRow {
  tool_name: string; call_count: number; error_count: number; error_rate: number;
  p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}
export interface ToolLatency { range: string; items: ToolLatencyRow[]; }

export interface HookActivity {
  range: string; total_fires: number; starts: number; completes: number;
  jsonl_stop_hook_summaries: number;
  paired_count: number; paired_p50_ms: number | null;
  paired_p95_ms: number | null; paired_max_ms: number | null;
}

export interface ProjectRow {
  cwd: string; sessions: number; effective_tokens: number; cost_usd: number;
  tool_count: number; share_pct: number;
}
export interface ProjectBreakdown { range: string; items: ProjectRow[]; }

export interface AgentFanoutRow {
  session_id: string; agent_calls: number; title: string | null; cwd: string | null; started_at: string | null;
}
export interface AgentFanout { range: string; items: AgentFanoutRow[]; }

export interface EditDecisionRow {
  tool_name: string; accept: number; reject: number; other: number; total: number; accept_rate: number | null;
}
export interface EditDecisions { range: string; items: EditDecisionRow[]; total: number; low_sample: boolean; }

export interface ProductivityDay { date: string; commits: number; pull_requests: number; lines_of_code: number; }
export interface Productivity {
  range: string; daily: ProductivityDay[];
  totals: { commits: number; pull_requests: number; lines_of_code: number };
  note: string;
}

export interface PressureRow {
  timestamp: string; session_id: string; subtype: string;
  retry_attempt: number | null; max_retries: number | null; retry_in_ms: number | null; content: string | null;
}
export interface SystemPressure {
  retry_exhaust_threshold: number;
  retry_exhaust_count_jsonl: number;
  retry_exhaust_count_otel: number;
  compaction_count: number;
  recent_api_errors: PressureRow[];
}

// -- MCP --
export interface McpServerRow {
  server: string; tool_count: number; calls: number; errors: number; error_rate: number;
  avg_ms: number | null; p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}
export interface McpServers { range: string; items: McpServerRow[]; }
export interface McpToolRow {
  tool: string; calls: number; errors: number; error_rate: number;
  p50_ms: number | null; p95_ms: number | null; max_ms: number | null;
}
export interface McpTools { range: string; server: string; items: McpToolRow[]; }

// -- Skills --
export interface SkillPreset {
  title?: string;
  description?: string;
  model?: string;
  execution_mode?: TaskMode;
  priority?: number;
  quadrant?: TaskQuadrant;
  risk_level?: 'low' | 'medium' | 'high';
  requires_approval?: boolean;
  dry_run?: boolean;
}
export interface SkillRow {
  name: string; environment: string; description: string; path: string;
  autonomy_level: 'auto' | 'review' | 'manual'; user_invocable: number;
  script_count: number; last_modified: string | null;
  // v0.6.0 — launcher.
  preset: SkillPreset | null;
  last_launched_at: string | null;
  launch_count: number;
  avg_cost_usd_30d: number | null;
  // v0.6.7 — per-skill daily budget. `null` = unlimited; `0` = blocked;
  // `>0` = post-hoc cap. `today_cost_usd` is the api_pool spend assigned
  // to this skill that has completed today (local).
  daily_budget_usd: number | null;
  today_cost_usd: number;
  // v0.7.0 — adversarial review gate opt-in. `0` = off (default), `1` = verify
  // every successful non-dry-run with an independent reviewer (~2× cost).
  review_mode: number;
}
export interface SkillsList { items: SkillRow[]; count: number; }
export interface SkillEconomicsRow {
  skill_name: string; invocations: number; effective_tokens: number; cost_usd: number;
}
export interface SkillsEconomics { range: string; items: SkillEconomicsRow[]; count: number; }

export interface ContextFileStats {
  exists: boolean; path?: string; bytes?: number; lines?: number; mtime_ts?: number;
  env_keys?: number; mcp_server_count?: number; hook_count?: number; rule_count?: number | null;
  error?: string;
}
export interface ContextHealth { settings: ContextFileStats; claude_md: ContextFileStats; }

// -- HITL + tasks + schedules --
export interface DecisionRow {
  id: number; task_id: number | null; session_id: string | null;
  prompt: string; answer: string | null;
  status: 'pending' | 'answered'; created_at: string; answered_at: string | null;
}
export interface DecisionsList { items: DecisionRow[]; count: number; }

export interface InboxRow {
  id: number; task_id: number | null; session_id: string | null;
  direction: 'agent_to_user' | 'user_to_agent'; body: string; read: number; created_at: string;
}
export interface InboxList { items: InboxRow[]; count: number; }

export type TaskStatus = 'pending' | 'awaiting_approval' | 'running' | 'done' | 'failed' | 'cancelled';
export type TaskMode = 'classic' | 'stream';
export type TaskQuadrant = 'do' | 'schedule' | 'delegate' | 'archive';

export interface TaskRow {
  id: number; title: string; description: string | null;
  status: TaskStatus; priority: number;
  assigned_skill: string | null; model: string | null;
  execution_mode: TaskMode; scheduled_for: string | null;
  requires_approval: number; risk_level: string | null;
  dry_run: number; quadrant: TaskQuadrant | null; approved_at: string | null;
  session_id: string | null; started_at: string | null;
  completed_at: string | null; duration_ms: number | null;
  cost_usd: number | null; cost_source: CostSource;
  output_summary: string | null; error_message: string | null;
  consecutive_failures: number; created_at: string;
  // v0.7.0 — adversarial review gate. success_criteria is operator-writable on
  // create; the other three are dispatcher-owned (read-only). review_verdict is
  // null until the task has been reviewed.
  success_criteria?: string | null;
  review_verdict?: 'VERIFIED' | 'NOT_VERIFIED' | 'MANUAL_VERIFY_REQUIRED' | null;
  review_count?: number;
  review_feedback?: string | null;
  // v0.7.1 — 1 when the operator accepted the output despite a non-VERIFIED
  // review (review_verdict is preserved as-is). 0 / undefined = normal.
  review_overridden?: number;
}
export interface TasksList { items: TaskRow[]; count: number; }

export interface ScheduleRow {
  id: number; name: string | null;
  cron_expression: string; task_title: string; task_description: string | null;
  assigned_skill: string | null; enabled: number;
  next_run_at: string | null; last_run_at: string | null; created_at: string;
}
export interface SchedulesList { items: ScheduleRow[]; count: number; }
export interface ScheduleRuns { schedule_id: number; items: TaskRow[]; }

// -- Telegram bridge status --
export interface TelegramStatus {
  configured: boolean;
  alive: boolean;
  pid: number | null;
  chat_id_set: boolean;
  token_set: boolean;
  last_outbound_at: string | null;
  notified_24h: number;
  notified_24h_by_type: Record<string, number>;
  stdout_log_mtime_age_s: number | null;
  last_error: string | null;
  last_error_at: string | null;
}

// -- Dispatcher hardening state --
export interface DispatcherState {
  max_concurrent: number;
  running: number;
  free_slots: number;
  back_pressure: boolean;
  daily_cost_cap_usd: number | null;
  today_cost_usd: number;
  // v0.6.0 — split by cost_source. Cap reads api_pool only.
  today_cost_api_pool_usd: number;
  today_cost_max_sub_usd: number;
  today_cost_unknown_usd: number;
  cost_capped: boolean;
  hard_risk_gate: boolean;
  risk_gated_today: number;
  // v0.6.7 — per-skill daily budget rollup.
  skills_with_budget: number;
  skills_at_budget: number;
  // v0.7.0 — adversarial review rollup. skills_with_review = opted-in skills;
  // tasks_awaiting_review_approval = awaiting_approval rows with a non-NULL
  // review_verdict (reviewer-escalated, distinct from risk-gated).
  skills_with_review: number;
  tasks_awaiting_review_approval: number;
}

// -- Sparklines + heatmap --
export interface Sparklines {
  slots: string[];     // 24 strings, oldest → newest, "YYYY-MM-DD HH"
  sessions: number[];
  tokens: number[];
  cost_usd: number[];
  errors: number[];
}
export interface ActivityHeatmap {
  range: string;
  grid: number[][];    // [7][24] rows=weekday (0=Sun..6=Sat), cols=hour
  total: number;
  peak: number;
  weekdays: string[];
}

// -- Firehose event --
export interface FirehoseEvent {
  id: number; event_name: string | null; session_id: string | null;
  timestamp: string | null; model: string | null;
  tool_name: string | null; mcp_server_name: string | null; mcp_tool_name: string | null;
  tool_duration_ms: number | null; cost_usd: number | null;
  input_tokens: number | null; output_tokens: number | null; error_message: string | null;
}
