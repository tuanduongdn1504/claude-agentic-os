import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { useToolLatency } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { Badge } from '@/components/ui/Badge';
import { fmtMs, fmtPct } from '@/lib/format';
import { Skeleton } from '@/components/ui/Skeleton';

export function ToolLatencyCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useToolLatency(range);
  const items = data?.items ?? [];
  const max95 = Math.max(1, ...items.map(i => i.p95_ms ?? 0));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Tool latency</Kicker>
          <CardTitle>p50 / p95 / max · by tool</CardTitle>
          <CardDescription>Sort by p95 desc. Red at ≥ 10s; green at sub-500ms.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading && (
          <div className="space-y-2">{Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        )}
        {!isLoading && items.length === 0 && (
          <div className="text-center text-text-subtle py-10 text-[13px]">no tool calls in this range</div>
        )}
        {!isLoading && items.length > 0 && (
          <table className="w-full text-[12.5px]">
            <thead className="kicker border-b border-border">
              <tr className="text-left">
                <th className="py-2 font-normal">Tool</th>
                <th className="py-2 font-normal text-right w-16">N</th>
                <th className="py-2 font-normal text-right w-20">p50</th>
                <th className="py-2 font-normal text-right w-20">p95</th>
                <th className="py-2 font-normal text-right w-20">max</th>
                <th className="py-2 font-normal text-right w-16">err</th>
                <th className="py-2 font-normal w-[32%]">p95 scale</th>
              </tr>
            </thead>
            <tbody>
              {items.map(r => {
                const tone =
                  (r.p95_ms ?? 0) >= 10_000 ? 'red' :
                  (r.p95_ms ?? 0) < 500 ? 'green' : 'neutral';
                const width = ((r.p95_ms ?? 0) / max95) * 100;
                return (
                  <tr key={r.tool_name} className="border-b border-border/60 last:border-0 hover:bg-surface-2/50">
                    <td className="py-2 font-mono">{r.tool_name}</td>
                    <td className="py-2 text-right num">{r.call_count}</td>
                    <td className="py-2 text-right num">{fmtMs(r.p50_ms)}</td>
                    <td className="py-2 text-right num">
                      <Badge tone={tone as 'red' | 'green' | 'neutral'}>{fmtMs(r.p95_ms)}</Badge>
                    </td>
                    <td className="py-2 text-right num text-text-dim">{fmtMs(r.max_ms)}</td>
                    <td className="py-2 text-right num text-text-dim">{r.error_count ? fmtPct(r.error_rate) : '—'}</td>
                    <td className="py-2">
                      <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
                        <div
                          className={tone === 'red' ? 'bg-status-red' : tone === 'green' ? 'bg-status-green' : 'bg-accent-blue'}
                          style={{ width: `${width.toFixed(1)}%`, height: '100%' }}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
