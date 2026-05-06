import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { useSessionOutcomes } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { Skeleton } from '@/components/ui/Skeleton';

type Bucket = 'errored' | 'rate_limited' | 'truncated' | 'unfinished' | 'ok';
const BUCKETS: Bucket[] = ['errored', 'rate_limited', 'truncated', 'unfinished', 'ok'];
const COLORS: Record<Bucket, string> = {
  errored:      'bg-status-red',
  rate_limited: 'bg-status-amber',
  truncated:    'bg-status-amber/70',
  unfinished:   'bg-text-subtle',
  ok:           'bg-status-green',
};

export function SessionOutcomesCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useSessionOutcomes(range);
  const daily = data?.daily ?? [];
  const totals = data?.totals;
  const max = Math.max(1, ...daily.map(d => d.total));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Session outcomes</Kicker>
          <CardTitle>Daily breakdown</CardTitle>
          <CardDescription>Priority: errored &gt; rate-limited &gt; truncated &gt; unfinished &gt; ok.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-4 mb-4">
          {totals && BUCKETS.map(k => (
            <div key={k} className="flex items-center gap-1.5 text-[12px] text-text-dim">
              <span className={`w-2 h-2 rounded-full ${COLORS[k]}`} />
              <span className="font-mono">{k.replace('_', '-')}</span>
              <span className="num text-text">{totals[k]}</span>
            </div>
          ))}
        </div>
        <div className="h-[168px] flex items-end gap-1.5">
          {isLoading && Array.from({ length: 14 }).map((_, i) => <Skeleton key={i} className="flex-1 h-full" />)}
          {!isLoading && daily.length === 0 && (
            <div className="w-full grid place-items-center text-text-subtle text-[13px] h-full">
              no sessions in this range
            </div>
          )}
          {!isLoading && daily.map(d => {
            const h = d.total / max;
            return (
              <div key={d.date} className="flex-1 h-full flex flex-col justify-end group relative">
                <div className="w-full flex flex-col justify-end rounded-t-[3px] overflow-hidden"
                     style={{ height: `${(h * 100).toFixed(1)}%` }}>
                  {BUCKETS.map(k => {
                    const share = d.total > 0 ? d[k] / d.total : 0;
                    if (share === 0) return null;
                    return <div key={k} className={COLORS[k]} style={{ height: `${(share * 100).toFixed(2)}%`, minHeight: 1 }} />;
                  })}
                </div>
                <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 transition-opacity
                                px-2 py-1 rounded-md bg-surface-3 border border-border text-[11px] font-mono whitespace-nowrap shadow-lg">
                  {d.date} · {d.total}
                </div>
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
