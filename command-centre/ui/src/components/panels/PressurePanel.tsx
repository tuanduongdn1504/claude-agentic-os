// API retry exhaustion, compaction boundaries, recent api_error events.
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { StatePill } from '@/components/ui/StatePill';
import { Skeleton } from '@/components/ui/Skeleton';
import { useSystemPressure } from '@/hooks/useQueries';

export function PressurePanel() {
  const { data, isLoading } = useSystemPressure();

  const retryJsonl = data?.retry_exhaust_count_jsonl ?? 0;
  const retryOtel = data?.retry_exhaust_count_otel ?? 0;
  const threshold = data?.retry_exhaust_threshold ?? 0;
  const compactions = data?.compaction_count ?? 0;
  const recent = data?.recent_api_errors ?? [];
  const exhausted = retryJsonl + retryOtel >= threshold;

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>System pressure</Kicker>
          <CardTitle>Retry exhaustion + compactions</CardTitle>
          <CardDescription>
            When API retries hit max, or a session is compacted to fit context.
          </CardDescription>
        </div>
        <StatePill tone={exhausted ? 'error' : 'ok'}>
          {exhausted ? 'pressured' : 'healthy'}
        </StatePill>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-4">
          <Stat label="retry exhaust · jsonl" value={String(retryJsonl)} alert={retryJsonl > 0} />
          <Stat label="retry exhaust · otel" value={String(retryOtel)} alert={retryOtel > 0} />
          <Stat label="compactions" value={String(compactions)} />
        </div>

        <div className="kicker mb-2">Recent api_error events</div>
        {isLoading ? (
          <div className="space-y-1">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : recent.length === 0 ? (
          <div className="text-[12.5px] text-text-subtle py-4 text-center">no api_error events recorded</div>
        ) : (
          <ul className="space-y-1 max-h-[220px] overflow-y-auto">
            {recent.slice(0, 30).map((r, i) => (
              <li key={`${r.timestamp}-${i}`} className="text-[11.5px] font-mono py-1.5 px-2 rounded bg-status-red/5 border border-status-red/20">
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-text-subtle">{r.timestamp.slice(11, 19)}</span>
                  <Badge tone="red">{r.subtype}</Badge>
                  {r.retry_attempt != null && r.max_retries != null && (
                    <span className="text-text-subtle">retry {r.retry_attempt}/{r.max_retries}</span>
                  )}
                  <span className="text-text-subtle ml-auto truncate">
                    session {r.session_id.slice(0, 8)}
                  </span>
                </div>
                {r.content && (
                  <div className="text-text-dim truncate" title={r.content}>{r.content}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, alert }: { label: string; value: string; alert?: boolean }) {
  return (
    <div className={`border rounded-lg px-3 py-2 ${
      alert ? 'bg-status-red/5 border-status-red/30' : 'bg-surface-2/40 border-border'
    }`}>
      <div className="kicker mb-0.5">{label}</div>
      <div className={`font-mono text-[13px] num ${alert ? 'text-status-red' : ''}`}>{value}</div>
    </div>
  );
}
