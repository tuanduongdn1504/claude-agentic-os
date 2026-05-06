// Hook telemetry: total fires, start/complete pairing, paired latency.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { useHookActivity } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount, fmtMs } from '@/lib/format';

export function HookActivityCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data } = useHookActivity(range);
  const d = data;

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Hooks · activity</Kicker>
          <CardTitle>Hook fires + pairing</CardTitle>
          <CardDescription>
            PreToolUse/PostToolUse pairs. Paired latency proxies hook overhead.
          </CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Stat label="total fires" value={fmtCount(d?.total_fires)} />
          <Stat label="start/complete" value={`${fmtCount(d?.starts)} / ${fmtCount(d?.completes)}`} />
          <Stat label="paired count" value={fmtCount(d?.paired_count)} />
          <Stat label="jsonl stop-hook summaries" value={fmtCount(d?.jsonl_stop_hook_summaries)} />
          <Stat label="p50 paired" value={fmtMs(d?.paired_p50_ms ?? null)} />
          <Stat label="p95 paired" value={fmtMs(d?.paired_p95_ms ?? null)} />
          <Stat label="max paired" value={fmtMs(d?.paired_max_ms ?? null)} />
          <Stat
            label="pairing rate"
            value={
              d && d.starts > 0
                ? `${Math.round((d.paired_count / d.starts) * 100)}%`
                : '—'
            }
          />
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-2/40 border border-border rounded-lg px-3 py-2">
      <div className="kicker mb-0.5">{label}</div>
      <div className="font-mono text-[13px] num">{value}</div>
    </div>
  );
}
