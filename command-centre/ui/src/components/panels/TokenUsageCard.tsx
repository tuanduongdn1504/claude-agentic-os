import { useState } from 'react';
import { Card, CardHeader, CardTitle, CardDescription, CardContent, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useUsageTokens } from '@/hooks/useQueries';
import type { Range } from '@/lib/api';
import { fmtCount, fmtUsd } from '@/lib/format';

// v0.6.8 — `90d` / `1y` / `all` exposed. The single shared RangePicker below
// is consumed by ~14 panels + SessionsTable + SessionsPage, so widening this
// one array propagates the new options everywhere. Per-panel defaults are
// unchanged (each keeps its own useState<Range>('7d' | '30d')).
const RANGES: Range[] = ['today', '7d', '30d', '90d', '1y', 'all'];

// Stacked bars: input + output + cache_read + cache_create per day.
export function TokenUsageCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useUsageTokens(range);

  const totals = data?.totals;
  const daily = data?.daily ?? [];

  // Aggregate per day for the stacked bar (the API row is per model).
  // v0.6.0 — also capture per-day cost split by source. token_usage is
  // per-model so each model row repeats the same date's cost_by_source;
  // we take the first non-zero seen rather than summing to avoid double-
  // counting. (Backend ships the same blob on every model row for a date.)
  type DayBucket = { in: number; out: number; cr: number; cc: number;
                     api: number; max: number; unk: number };
  const byDay = new Map<string, DayBucket>();
  for (const r of daily) {
    const b = byDay.get(r.date) ?? { in: 0, out: 0, cr: 0, cc: 0, api: 0, max: 0, unk: 0 };
    b.in += r.input_tokens || 0;
    b.out += r.output_tokens || 0;
    b.cr += r.cache_read_tokens || 0;
    b.cc += r.cache_create_tokens || 0;
    const cbs = r.cost_by_source;
    if (cbs) {
      b.api = Math.max(b.api, cbs.api_pool || 0);
      b.max = Math.max(b.max, cbs.max_sub  || 0);
      b.unk = Math.max(b.unk, cbs.unknown  || 0);
    }
    byDay.set(r.date, b);
  }
  const dayEntries = Array.from(byDay.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  const max = Math.max(1, ...dayEntries.map(([, v]) => v.in + v.out + v.cr + v.cc));
  const costMax = Math.max(0, ...dayEntries.map(([, v]) => v.api + v.max));
  const hasCost = costMax > 0;

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
        {hasCost && (
          <div className="mt-1 pt-1 h-[16px] flex items-end gap-1.5" aria-label="per-day cost by source">
            {dayEntries.map(([day, v]) => {
              const total = v.api + v.max;
              if (total <= 0) {
                return <div key={day} className="flex-1 h-full" />;
              }
              const apiH = (v.api / costMax) * 100;
              const maxH = (v.max / costMax) * 100;
              return (
                <div key={day} className="flex-1 h-full flex flex-col justify-end group relative">
                  <div className="w-full flex flex-col justify-end">
                    <Seg h={v.max / Math.max(total, 1) * (total / costMax)} cls="bg-text-subtle/70" />
                    <Seg h={v.api / Math.max(total, 1) * (total / costMax)} cls="bg-accent-cyan/85 rounded-b-[2px]" />
                  </div>
                  <div className="absolute inset-x-0 bottom-full opacity-0 group-hover:opacity-100 transition-opacity flex justify-center pointer-events-none">
                    <div className="mb-1 px-2 py-1 rounded-md bg-surface-3 border border-border text-[11px] font-mono whitespace-nowrap shadow-lg">
                      {day} · api {fmtUsd(v.api)} · max {fmtUsd(v.max)}
                      {v.unk > 0 && <> · ? {fmtUsd(v.unk)}</>}
                    </div>
                  </div>
                  <div className="sr-only">
                    {day} api {v.api.toFixed(4)} max {v.max.toFixed(4)} unknown {v.unk.toFixed(4)} ({apiH.toFixed(0)}/{maxH.toFixed(0)}%)
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div className="mt-3 flex items-center gap-4 text-[11px] font-mono text-text-subtle flex-wrap">
          <Legend color="bg-accent-blue"   label="input" />
          <Legend color="bg-accent-purple" label="output" />
          <Legend color="bg-accent-cyan"   label="cache-read" />
          <Legend color="bg-status-amber"  label="cache-create" />
          {hasCost && (
            <>
              <span className="text-text-subtle/50">·</span>
              <Legend color="bg-accent-cyan/85"   label="api $" />
              <Legend color="bg-text-subtle/70"  label="max $" />
            </>
          )}
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
          data-range={r}
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
