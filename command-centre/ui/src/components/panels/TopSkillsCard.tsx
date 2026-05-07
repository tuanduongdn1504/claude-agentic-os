// Skill leaderboard — invocations / tokens / cost. Reads /api/skills/economics.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useSkillEconomics } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount, fmtUsd } from '@/lib/format';

type SortKey = 'invocations' | 'effective_tokens' | 'cost_usd';

export function TopSkillsCard() {
  const [range, setRange] = useState<Range>('30d');
  const [sort, setSort] = useState<SortKey>('cost_usd');
  const { data, isLoading } = useSkillEconomics(range);

  const items = data?.items ?? [];
  const sorted = [...items].sort((a, b) => (b[sort] ?? 0) - (a[sort] ?? 0));
  const max = Math.max(1, ...sorted.map(i => i[sort] ?? 0));

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Top skills</Kicker>
          <CardTitle>Invocations · tokens · cost per skill</CardTitle>
          <CardDescription>
            Sourced from OTEL events tagged with <span className="font-mono">skill_name</span>.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <SortPicker value={sort} onChange={setSort} />
          <RangePicker value={range} onChange={setRange} />
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}</div>
        ) : sorted.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">
            no skill-tagged OTEL events in this range — invoke a skill via the dispatcher to populate.
          </div>
        ) : (
          <ul className="space-y-1">
            {sorted.slice(0, 10).map((s, idx) => {
              const v = s[sort] ?? 0;
              return (
                <li key={s.skill_name} className="py-1.5">
                  <div className="flex items-center gap-3 mb-1">
                    <span className="text-[11px] font-mono text-text-subtle w-6 text-right">#{idx + 1}</span>
                    <span className="text-[12.5px] truncate flex-1" title={s.skill_name}>
                      {s.skill_name}
                    </span>
                    <Badge tone="purple">{fmtCount(s.invocations)} runs</Badge>
                    <span className="font-mono text-[11.5px] text-text-dim w-20 text-right">
                      {fmtCount(s.effective_tokens)}
                    </span>
                    <span className="font-mono text-[12px] text-text num w-16 text-right">
                      {fmtUsd(s.cost_usd)}
                    </span>
                  </div>
                  <div className="h-1 rounded bg-surface-3 overflow-hidden ml-9">
                    <div
                      className="h-full bg-gradient-to-r from-accent-purple to-accent-blue"
                      style={{ width: `${(v / max) * 100}%` }}
                    />
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

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'cost_usd',         label: 'cost' },
  { key: 'effective_tokens', label: 'tokens' },
  { key: 'invocations',      label: 'runs' },
];

function SortPicker({ value, onChange }: { value: SortKey; onChange: (k: SortKey) => void }) {
  return (
    <div className="inline-flex items-center rounded-lg border border-border bg-surface-2 p-0.5 text-[11px] font-mono uppercase tracking-wide">
      {SORT_OPTIONS.map(o => (
        <button
          key={o.key}
          onClick={() => onChange(o.key)}
          className={`px-2.5 h-6 rounded-md transition-colors ${
            value === o.key ? 'bg-surface-3 text-text' : 'text-text-dim hover:text-text'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
