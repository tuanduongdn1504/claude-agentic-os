// commits / PRs / LoC per day. Honest about its best-effort estimation.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useProductivity } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount } from '@/lib/format';

export function ProductivityCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useProductivity(range);
  const daily = data?.daily ?? [];
  const totals = data?.totals;
  const max = Math.max(1, ...daily.map(d => d.commits + d.pull_requests));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Productivity</Kicker>
          <CardTitle>Commits · PRs · LoC</CardTitle>
          <CardDescription>
            {data?.note ?? 'Derived from local git history in tracked project roots.'}
          </CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {totals && (
          <div className="flex items-center gap-4 mb-3 text-[12px] text-text-dim">
            <span>commits <span className="text-text font-mono ml-1">{fmtCount(totals.commits)}</span></span>
            <span>PRs <span className="text-text font-mono ml-1">{fmtCount(totals.pull_requests)}</span></span>
            <span>LoC <span className="text-text font-mono ml-1">{fmtCount(totals.lines_of_code)}</span></span>
          </div>
        )}
        {isLoading ? (
          <div className="h-[120px] flex items-end gap-1">
            {Array.from({ length: 14 }).map((_, i) => <Skeleton key={i} className="flex-1 h-full" />)}
          </div>
        ) : daily.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no activity in this range</div>
        ) : (
          <div className="h-[120px] flex items-end gap-1.5">
            {daily.map(d => {
              const total = d.commits + d.pull_requests;
              const h = total / max;
              return (
                <div key={d.date} className="flex-1 h-full flex flex-col justify-end group relative">
                  <div
                    className="bg-gradient-to-t from-accent-blue to-accent-purple rounded-t-sm"
                    style={{ height: `${(h * 100).toFixed(1)}%`, minHeight: total > 0 ? 2 : 0 }}
                  />
                  <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity
                                  px-2 py-1 rounded-md bg-surface-3 border border-border text-[11px] font-mono whitespace-nowrap shadow-lg pointer-events-none">
                    {d.date} · {d.commits}c / {d.pull_requests}pr
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
