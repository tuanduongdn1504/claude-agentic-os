import { AlertCircle } from 'lucide-react';
import { useAttention } from '@/hooks/useQueries';
import { fmtDateTimeUTC7 } from '@/lib/format';
import type { AttentionIssue } from '@/lib/types';
import { cn } from '@/lib/cn';

// v0.6.7 — severity normalisation. Producers historically use `warn` for
// dispatcher staleness / back-pressure; the new per-skill budget producer
// emits `warning`. Treat both as amber so the visual stays consistent.
function isWarning(sev: unknown): boolean {
  const s = String(sev ?? '').toLowerCase();
  return s === 'warning' || s === 'warn';
}

// Render order: errors first, then warnings, then anything else. Keeps
// `cost_capped` (error red) above `skill_budget_capped` (warning amber).
function severityRank(it: AttentionIssue): number {
  const s = String(it.severity ?? '').toLowerCase();
  if (s === 'error') return 0;
  if (s === 'warning' || s === 'warn') return 1;
  return 2;
}

export function AttentionBar() {
  const { data } = useAttention();
  if (!data || data.count === 0) return null;
  const issues = [...data.issues].sort((a, b) => severityRank(a) - severityRank(b));
  // Tone the banner to the worst severity in the feed: any `error` still wins
  // (red), warnings-only drops to amber (matches v0.6.7 skill_budget_capped).
  const hasError = issues.some(it => String(it.severity).toLowerCase() === 'error');

  return (
    <div
      className={cn(
        'rounded-xl px-5 py-3 flex items-start gap-3 animate-fade-in',
        hasError
          ? 'border border-status-red/40 bg-gradient-to-r from-status-red/15 to-status-red/0'
          : 'border border-status-amber/40 bg-gradient-to-r from-status-amber/15 to-status-amber/0',
      )}
    >
      <AlertCircle
        className={cn('shrink-0 mt-0.5', hasError ? 'text-status-red' : 'text-status-amber')}
        size={18}
      />
      <div className="flex-1 text-[13px]">
        <div
          className={cn(
            'font-semibold mb-1',
            hasError ? 'text-status-red' : 'text-status-amber',
          )}
        >
          Needs attention · {data.count}
        </div>
        <ul className="space-y-1 text-text-dim">
          {issues.slice(0, 6).map((it, i) => (
            <li
              key={i}
              className="flex items-center gap-2"
              data-issue-kind={String(it.kind)}
            >
              <span
                className={cn(
                  'font-mono uppercase text-[10.5px]',
                  isWarning(it.severity) && !hasError
                    ? 'text-status-amber/80'
                    : 'text-text-subtle',
                )}
              >
                {String(it.kind).replace(/_/g, ' ')}
              </span>
              <span className="truncate">{describe(it)}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function describe(it: Record<string, unknown>): string {
  // Backend supplies a `message` field for some kinds — prefer it.
  if (typeof it.message === 'string' && it.message) return it.message;
  switch (it.kind) {
    case 'stuck_session':
      return `${it.title ?? it.session_id ?? 'session'} started ${it.started_at ? fmtDateTimeUTC7(String(it.started_at)) : '?'}`;
    case 'failed_task':
      return `${it.title ?? 'task'} — ${it.error_message ?? 'no message'}`;
    case 'decisions_pending':
      return `${it.count} decision${(it.count as number) === 1 ? '' : 's'} awaiting answer`;
    case 'dispatcher_stale':
      return `dispatcher silent ${it.age_s}s`;
    case 'schedule_overdue':
      return `${it.name ?? 'schedule'} — next run ${it.next_run_at ? fmtDateTimeUTC7(String(it.next_run_at)) : '?'}`;
    case 'cost_capped': {
      const api = (it.today_cost_api_pool_usd ?? it.today_cost_usd) as number | undefined;
      const cap = it.cap_usd as number | undefined;
      if (api != null && cap != null) {
        return `API-pool spend reached cap ($${api.toFixed(2)} of $${cap.toFixed(2)} today). Max-sub usage continues.`;
      }
      return 'API-pool spend reached daily cap.';
    }
    case 'back_pressure':
      return `${it.running}/${it.max_concurrent} dispatcher slots in use`;
    case 'skill_budget_capped': {
      // v0.6.7 — N skills at daily budget. Uses dispatcher-state-supplied
      // `count` + the first skill's spend / budget for context.
      const n = (it.count as number) ?? 1;
      const skill = it.skill as string | undefined;
      const today = it.today_cost_usd as number | undefined;
      const budget = it.daily_budget_usd as number | undefined;
      const head = `${n} skill${n === 1 ? '' : 's'} at daily budget`;
      if (skill && today != null && budget != null) {
        return `${head} — ${skill} blocked at $${today.toFixed(2)} / $${budget.toFixed(2)}`;
      }
      return head;
    }
    default:
      return JSON.stringify(it);
  }
}
