// Invocations, tokens, cost per skill.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useSkillEconomics } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount, fmtUsd } from '@/lib/format';

export function SkillCostCard() {
  const [range, setRange] = useState<Range>('30d');
  const { data, isLoading } = useSkillEconomics(range);
  const items = data?.items ?? [];
  const maxCost = Math.max(0.01, ...items.map(i => i.cost_usd));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Skill economics</Kicker>
          <CardTitle>Invocations · tokens · cost per skill</CardTitle>
          <CardDescription>Sourced from OTEL events tagged with skill_name.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no skill-tagged events in this range</div>
        ) : (
          <ul className="space-y-1">
            {items.map(r => (
              <li key={r.skill_name} className="py-1.5">
                <div className="flex items-center gap-3 mb-1">
                  <span className="text-[12.5px] font-mono flex-1 truncate">{r.skill_name}</span>
                  <span className="text-[11px] font-mono text-text-subtle w-20 text-right">
                    {r.invocations} runs
                  </span>
                  <span className="text-[11px] font-mono text-text-subtle w-20 text-right">
                    {fmtCount(r.effective_tokens)}
                  </span>
                  <span className="text-[12px] font-mono text-text w-16 text-right num">{fmtUsd(r.cost_usd)}</span>
                </div>
                <div className="h-1 rounded bg-surface-3 overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-accent-cyan to-accent-blue"
                    style={{ width: `${(r.cost_usd / maxCost) * 100}%` }}
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
