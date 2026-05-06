// Accept/reject rates for edit tools.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useEditDecisions } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtPct } from '@/lib/format';

export function EditAcceptanceCard() {
  const [range, setRange] = useState<Range>('7d');
  const { data, isLoading } = useEditDecisions(range);
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Edit decisions</Kicker>
          <CardTitle>Acceptance rates</CardTitle>
          <CardDescription>
            Accept / reject per edit tool.
            {data?.low_sample && <Badge tone="amber" className="ml-2">low sample</Badge>}
          </CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no edit-decision events in this range</div>
        ) : (
          <ul className="space-y-1.5">
            {items.map(r => {
              const acceptFrac = r.accept_rate ?? (r.total > 0 ? r.accept / r.total : null);
              return (
                <li key={r.tool_name} className="py-1">
                  <div className="flex items-center gap-3 mb-1 text-[12.5px]">
                    <span className="font-mono flex-1">{r.tool_name}</span>
                    <span className="text-text-subtle font-mono text-[11px]">n={r.total}</span>
                    <span className="font-mono num w-14 text-right">{fmtPct(acceptFrac)}</span>
                  </div>
                  <div className="h-1.5 rounded bg-surface-3 overflow-hidden flex">
                    <div className="bg-status-green h-full" style={{ width: `${r.total ? (r.accept / r.total) * 100 : 0}%` }} />
                    <div className="bg-status-red h-full" style={{ width: `${r.total ? (r.reject / r.total) * 100 : 0}%` }} />
                    <div className="bg-text-subtle h-full" style={{ width: `${r.total ? (r.other / r.total) * 100 : 0}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
