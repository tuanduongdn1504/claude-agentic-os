// Full sessions table: search, range, row click → detail Sheet.
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Input } from '@/components/ui/Field';
import { RangePicker } from './TokenUsageCard';
import { LiveSessionDetail } from './LiveSessionDetail';
import * as api from '@/lib/api';
import type { Range } from '@/lib/api';
import { cwdShort, fmtCount, fmtMs, fmtUsd } from '@/lib/format';

export function SessionsTable() {
  const [range, setRange] = useState<Range>('30d');
  const [q, setQ] = useState('');
  const [active, setActive] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ['sessions-table', range, q],
    queryFn: () => api.listSessions({ range, limit: 100, q: q || undefined }),
    refetchInterval: 30_000,
  });
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Sessions</Kicker>
          <CardTitle>All sessions · {data?.total ?? '—'} total</CardTitle>
          <CardDescription>Derived from ~/.claude/projects JSONL. Click a row to open timeline.</CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle pointer-events-none" />
            <Input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="title, cwd, model…"
              className="!pl-7 !h-8 !w-[220px] !text-[12px]"
            />
          </div>
          <RangePicker value={range} onChange={setRange} />
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no sessions match</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead className="kicker border-b border-border">
                <tr className="text-left">
                  <th className="py-2 pr-3 font-normal">title</th>
                  <th className="py-2 pr-3 font-normal">model</th>
                  <th className="py-2 pr-3 font-normal">cwd</th>
                  <th className="py-2 pr-3 font-normal text-right">tokens</th>
                  <th className="py-2 pr-3 font-normal text-right">cost</th>
                  <th className="py-2 pr-3 font-normal text-right">duration</th>
                  <th className="py-2 pr-3 font-normal text-right">state</th>
                </tr>
              </thead>
              <tbody>
                {items.map(s => (
                  <tr
                    key={s.session_id}
                    className="border-b border-border/50 last:border-0 hover:bg-surface-2/40 cursor-pointer"
                    onClick={() => setActive(s.session_id)}
                  >
                    <td className="py-2 pr-3 truncate max-w-[260px]" title={s.title ?? s.session_id}>
                      {s.title ?? <span className="text-text-subtle font-mono">session {s.session_id.slice(0, 8)}</span>}
                    </td>
                    <td className="py-2 pr-3 font-mono text-text-dim">{s.model ?? '—'}</td>
                    <td className="py-2 pr-3 font-mono text-text-dim truncate max-w-[220px]" title={s.cwd ?? ''}>
                      {cwdShort(s.cwd)}
                    </td>
                    <td className="py-2 pr-3 text-right num">{fmtCount(s.effective_tokens)}</td>
                    <td className="py-2 pr-3 text-right num">{fmtUsd(s.cost_usd)}</td>
                    <td className="py-2 pr-3 text-right num">{fmtMs(s.duration_ms)}</td>
                    <td className="py-2 pr-3 text-right">
                      <Badge tone={
                        s.is_error_any ? 'red' :
                        s.rate_limit_hit ? 'amber' :
                        !s.ended_at ? 'neutral' : 'green'
                      }>
                        {s.is_error_any ? 'errored' :
                         s.rate_limit_hit ? 'rate_limited' :
                         !s.ended_at ? 'unfinished' : 'ok'}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
      <LiveSessionDetail sessionId={active} onClose={() => setActive(null)} />
    </Card>
  );
}
