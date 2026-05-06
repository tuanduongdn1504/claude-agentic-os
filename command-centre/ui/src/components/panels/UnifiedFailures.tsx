// Unified failure feed: errored sessions, rate limits, api_error events.
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useSessionFailures } from '@/hooks/useQueries';
import { cwdShort, fmtCount, fmtUsd } from '@/lib/format';

export function UnifiedFailures() {
  const { data, isLoading } = useSessionFailures('30d');
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Failures · 30d</Kicker>
          <CardTitle>Errored, rate-limited, or api_error</CardTitle>
          <CardDescription>Any session whose outcome or event stream shows trouble.</CardDescription>
        </div>
        <Badge tone={items.length ? 'red' : 'neutral'}>{data?.count ?? 0}</Badge>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no failures recorded</div>
        ) : (
          <ul className="space-y-1.5">
            {items.map(s => (
              <li key={s.session_id} className="bg-surface-2/40 border border-status-red/20 rounded-lg p-2.5">
                <div className="flex items-start gap-3 mb-1">
                  <div className="flex-1 min-w-0">
                    <div className="text-[12.5px] truncate" title={s.title ?? s.session_id}>
                      {s.title ?? <span className="font-mono text-text-dim">session {s.session_id.slice(0, 8)}</span>}
                    </div>
                    <div className="text-[11px] text-text-subtle font-mono truncate" title={s.cwd ?? ''}>
                      {cwdShort(s.cwd)}
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-1 justify-end">
                    {s.is_error_any > 0 && <Badge tone="red">errored</Badge>}
                    {s.rate_limit_hit > 0 && <Badge tone="amber">rate-limited</Badge>}
                    {s.api_errors > 0 && <Badge tone="red">api_error · {s.api_errors}</Badge>}
                    {s.stop_reason && <Badge tone="neutral">{s.stop_reason}</Badge>}
                  </div>
                </div>
                <div className="flex items-center gap-3 text-[11px] font-mono text-text-subtle">
                  <span>{s.model ?? '—'}</span>
                  <span>{fmtCount(s.effective_tokens)} tok</span>
                  <span>{fmtUsd(s.cost_usd)}</span>
                  {s.started_at && <span className="ml-auto">{s.started_at.slice(0, 19)}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
