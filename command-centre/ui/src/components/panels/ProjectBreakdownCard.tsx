// Sessions grouped by cwd with share bars.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useProjectBreakdown } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { cwdShort, fmtCount, fmtUsd } from '@/lib/format';

export function ProjectBreakdownCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useProjectBreakdown(range);
  const items = data?.items ?? [];
  const max = Math.max(1, ...items.map(i => i.share_pct || 0));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Project breakdown</Kicker>
          <CardTitle>Sessions by cwd</CardTitle>
          <CardDescription>Share of effective tokens per project root.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no sessions in this range</div>
        ) : (
          <ul className="space-y-1">
            {items.slice(0, 12).map(r => (
              <li key={r.cwd} className="py-1.5">
                <div className="flex items-center gap-3 mb-1">
                  <span className="text-[12.5px] font-mono truncate flex-1" title={r.cwd}>
                    {cwdShort(r.cwd)}
                  </span>
                  <span className="text-[11px] font-mono text-text-subtle w-14 text-right">
                    {r.sessions} sess
                  </span>
                  <span className="text-[11px] font-mono text-text-subtle w-20 text-right">
                    {fmtCount(r.effective_tokens)}
                  </span>
                  <span className="text-[11px] font-mono text-text w-14 text-right">{fmtUsd(r.cost_usd)}</span>
                </div>
                <div className="h-1 rounded bg-surface-3 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-accent-blue to-accent-purple"
                    style={{ width: `${(r.share_pct / max) * 100}%` }}
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
