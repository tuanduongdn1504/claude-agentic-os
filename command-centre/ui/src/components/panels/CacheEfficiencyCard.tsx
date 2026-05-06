import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { useUsageCache } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount, fmtPct } from '@/lib/format';
import { Skeleton } from '@/components/ui/Skeleton';

const TARGET = 0.7;

export function CacheEfficiencyCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useUsageCache(range);
  const overall = data?.overall_hit_rate ?? null;

  // Build a sparkline from the daily hit_rate.
  const daily = data?.daily ?? [];
  const w = 260;
  const h = 56;
  const points = daily
    .map((d, i) => {
      const x = daily.length > 1 ? (i / (daily.length - 1)) * w : w / 2;
      const y = h - (d.hit_rate ?? 0) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const targetY = h - TARGET * h;

  const tone = overall == null ? 'neutral'
    : overall >= TARGET ? 'green'
    : overall >= 0.5 ? 'amber'
    : 'red';

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Cache efficiency</Kicker>
          <CardTitle>Hit rate</CardTitle>
          <CardDescription>cache_read ÷ (input + cache_read + cache_create). Target 70%.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-4 mb-4">
          <div className="flex items-baseline gap-3">
            <div className="text-[44px] font-semibold num leading-none tracking-tight">
              {isLoading ? <Skeleton className="h-[44px] w-24" /> : fmtPct(overall)}
            </div>
            <Badge tone={tone as 'green' | 'amber' | 'red' | 'neutral'}>
              {overall == null ? '—' : overall >= TARGET ? 'above target' : 'below target'}
            </Badge>
          </div>
          {data?.low_sample && (
            <Badge tone="amber">low sample · {fmtCount(data.billable_tokens)} billable</Badge>
          )}
        </div>
        <div className="rounded-lg border border-border bg-surface-2 p-4 flex items-center gap-5">
          <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} className="flex-1 min-w-0">
            <line x1="0" x2={w} y1={targetY} y2={targetY} stroke="#3d3d5c" strokeDasharray="3 3" strokeWidth={1} />
            {points && (
              <>
                <polyline
                  points={points}
                  fill="none"
                  stroke="url(#grad)"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <defs>
                  <linearGradient id="grad" x1="0" x2="1">
                    <stop offset="0" stopColor="#4d7cff" />
                    <stop offset="1" stopColor="#8b5cf6" />
                  </linearGradient>
                </defs>
              </>
            )}
          </svg>
          <div className="w-28 text-right">
            <div className="kicker">billable</div>
            <div className="text-[14px] font-mono num">{fmtCount(data?.billable_tokens ?? 0)}</div>
            <div className="kicker mt-2">days</div>
            <div className="text-[14px] font-mono num">{daily.length}</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
