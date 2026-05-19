import { AlertCircle } from 'lucide-react';
import { useAttention } from '@/hooks/useQueries';
import { fmtDateTimeUTC7 } from '@/lib/format';

export function AttentionBar() {
  const { data } = useAttention();
  if (!data || data.count === 0) return null;

  return (
    <div className="rounded-xl border border-status-red/40 bg-gradient-to-r from-status-red/15 to-status-red/0 px-5 py-3 flex items-start gap-3 animate-fade-in">
      <AlertCircle className="text-status-red shrink-0 mt-0.5" size={18} />
      <div className="flex-1 text-[13px]">
        <div className="font-semibold text-status-red mb-1">Needs attention · {data.count}</div>
        <ul className="space-y-1 text-text-dim">
          {data.issues.slice(0, 6).map((it, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="font-mono uppercase text-[10.5px] text-text-subtle">
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
    default:
      return JSON.stringify(it);
  }
}
