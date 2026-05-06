import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useUsageTokens } from '@/hooks/useQueries';
import type { Range } from '@/lib/api';
import { fmtCount } from '@/lib/format';

const RANGES: Range[] = ['today', '7d', '30d'];

// Stacked bars: input + output + cache_read + cache_create per day.
export function TokenUsageCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useUsageTokens(range);

  const totals = data?.totals;
  const daily = data?.daily ?? [];

  // Aggregate per day for the stacked bar (the API row is per model).
  const byDay = new Map<string, { in: number; out: number; cr: number; cc: number }>();
  for (const r of daily) {
    const b = byDay.get(r.date) ?? { in: 0, out: 0, cr: 0, cc: 0 };
    b.in += r.input_tokens || 0;
    b.out += r.output_tokens || 0;
    b.cr += r.cache_read_tokens || 0;
    b.cc += r.cache_create_tokens || 0;
    byDay.set(r.date, b);
  }
  const dayEntries = Array.from(byDay.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const max = Math.max(1, ...dayEntries.map(([, v]) => v.in + v.out + v.cr + v.cc));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Token usage</Kicker>
          <CardTitle>Daily token consumption</CardTitle>
          <CardDescription>Stacked: input + output + cache-read + cache-create. Local time.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <Stat label="input"        value={fmtCount(totals?.input)}        color="bg-accent-blue" loading={isLoading} />
          <Stat label="output"       value={fmtCount(totals?.output)}       color="bg-accent-purple" loading={isLoading} />
          <Stat label="cache-read"   value={fmtCount(totals?.cache_read)}   color="bg-accent-cyan" loading={isLoading} />
          <Stat label="cache-create" value={fmtCount(totals?.cache_create)} color="bg-status-amber" loading={isLoading} />
        </div>
        <div className="h-[168px] flex items-end gap-1.5">
          {isLoading && Array.from({ length: 14 }).map((_, i) => (
            <Skeleton key={i} className="flex-1 h-full" />
          ))}
          {!isLoading && dayEntries.length === 0 && (
            <div className="w-full grid place-items-center text-text-subtle text-[13px] h-full">
              no usage in this range yet
            </div>
          )}
          {!isLoading && dayEntries.map(([day, v]) => {
            const total = v.in + v.out + v.cr + v.cc;
            const h = total / max;
            return (
              <div key={day} className="flex-1 h-full flex flex-col justify-end group relative">
                <div className="w-full flex flex-col justify-end" style={{ height: `${(h * 100).toFixed(1)}%` }}>
                  <Seg h={v.cc / Math.max(total, 1)} cls="bg-status-amber/75" />
                  <Seg h={v.cr / Math.max(total, 1)} cls="bg-accent-cyan/75" />
                  <Seg h={v.out / Math.max(total, 1)} cls="bg-accent-purple/85" />
                  <Seg h={v.in  / Math.max(total, 1)} cls="bg-accent-blue/85 rounded-b-[3px]" />
                </div>
                <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity
                                bottom-full flex items-end justify-center pointer-events-none">
                  <div className="mb-2 px-2 py-1 rounded-md bg-surface-3 border border-border text-[11px] font-mono whitespace-nowrap shadow-lg">
                    {day} · {fmtCount(total)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 flex items-center gap-4 text-[11px] font-mono text-text-subtle">
          <Legend color="bg-accent-blue"   label="input" />
          <Legend color="bg-accent-purple" label="output" />
          <Legend color="bg-accent-cyan"   label="cache-read" />
          <Legend color="bg-status-amber"  label="cache-create" />
        </div>
      </CardContent>
    </Card>
  );
}

function Seg({ h, cls }: { h: number; cls: string }) {
  if (h <= 0) return null;
  return <div className={cls} style={{ height: `${(h * 100).toFixed(2)}%`, minHeight: 1 }} />;
}

function Stat({ label, value, color, loading }: { label: string; value: string; color: string; loading?: boolean }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 kicker">
        <span className={`w-2 h-2 rounded-full ${color}`} /> {label}
      </div>
      <div className="text-[20px] font-semibold num mt-1.5">
        {loading ? <Skeleton className="h-[20px] w-16" /> : value}
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${color}`} /> {label}
    </span>
  );
}

export function RangePicker({ value, onChange }: { value: Range; onChange: (r: Range) => void }) {
  return (
    <div className="inline-flex items-center rounded-lg border border-border bg-surface-2 p-0.5 text-[11px] font-mono uppercase tracking-wide">
      {RANGES.map(r => (
        <button
          key={r}
          onClick={() => onChange(r)}
          className={`px-2.5 h-6 rounded-md transition-colors ${
            value === r ? 'bg-surface-3 text-text' : 'text-text-dim hover:text-text'
          }`}
        >
          {r}
        </button>
      ))}
    </div>
  );
}
