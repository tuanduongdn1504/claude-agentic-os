// Sessions that dispatch many sub-agents. Scale hotspots.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAgentFanout } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { cwdShort } from '@/lib/format';

export function AgentFanoutCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useAgentFanout(range);
  const items = data?.items ?? [];
  const max = Math.max(1, ...items.map(i => i.agent_calls));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Agent fanout</Kicker>
          <CardTitle>Top sub-agent dispatchers</CardTitle>
          <CardDescription>Sessions that spawn sub-agents (Agent tool calls).</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no sub-agent dispatches in this range</div>
        ) : (
          <ul className="space-y-1">
            {items.slice(0, 10).map(r => (
              <li key={r.session_id} className="py-1">
                <div className="flex items-center gap-3 mb-1">
                  <span className="text-[12.5px] truncate flex-1" title={r.title ?? r.session_id}>
                    {r.title ?? <span className="font-mono text-text-dim">session {r.session_id.slice(0, 8)}</span>}
                  </span>
                  <span className="text-[11px] font-mono text-text-subtle truncate max-w-[180px]">
                    {cwdShort(r.cwd)}
                  </span>
                  <span className="font-mono text-[12px] num w-10 text-right">{r.agent_calls}</span>
                </div>
                <div className="h-1 rounded bg-surface-3 overflow-hidden">
                  <div
                    className="h-full bg-accent-purple"
                    style={{ width: `${(r.agent_calls / max) * 100}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
