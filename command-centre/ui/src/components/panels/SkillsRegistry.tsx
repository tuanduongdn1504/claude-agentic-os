// Skills table with autonomy level dropdown.
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { usePatchSkillAutonomy, useSkills } from '@/hooks/useQueries';
import type { SkillRow } from '@/lib/types';
import { Select } from '@/components/ui/Field';
import { fmtAgoFromIso } from '@/lib/format';

const AUTONOMY_TONE: Record<SkillRow['autonomy_level'], 'green' | 'amber' | 'red'> = {
  auto:   'green',
  review: 'amber',
  manual: 'red',
};

export function SkillsRegistry() {
  const { data, isLoading } = useSkills();
  const patch = usePatchSkillAutonomy();
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Skills registry</Kicker>
          <CardTitle>Installed skills</CardTitle>
          <CardDescription>
            Autonomy level gates how a task routed to this skill runs.
            <span className="ml-2"><Badge tone="green">auto</Badge> runs immediately</span>
            <span className="ml-2"><Badge tone="amber">review</Badge> requires approval</span>
            <span className="ml-2"><Badge tone="red">manual</Badge> never auto-runs</span>
          </CardDescription>
        </div>
        <Badge tone="neutral">{items.length} skills</Badge>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no skills registered</div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full text-[12.5px]">
              <thead className="kicker bg-surface-2/40">
                <tr className="text-left">
                  <th className="py-2 px-3 font-normal">name</th>
                  <th className="py-2 px-3 font-normal">env</th>
                  <th className="py-2 px-3 font-normal">description</th>
                  <th className="py-2 px-3 font-normal text-right">scripts</th>
                  <th className="py-2 px-3 font-normal text-right">modified</th>
                  <th className="py-2 px-3 font-normal">autonomy</th>
                </tr>
              </thead>
              <tbody>
                {items.map(s => (
                  <tr key={s.name} className="border-t border-border hover:bg-surface-2/40">
                    <td className="py-2 px-3 font-mono">{s.name}</td>
                    <td className="py-2 px-3">
                      <Badge tone={s.environment === 'cc' ? 'blue' : 'purple'}>{s.environment}</Badge>
                    </td>
                    <td className="py-2 px-3 text-text-dim truncate max-w-[340px]" title={s.description}>
                      {s.description || '—'}
                    </td>
                    <td className="py-2 px-3 text-right num">{s.script_count}</td>
                    <td className="py-2 px-3 text-right font-mono text-text-subtle text-[11px]">
                      {s.last_modified ? fmtAgoFromIso(s.last_modified) : '—'}
                    </td>
                    <td className="py-2 px-3">
                      <div className="flex items-center gap-2">
                        <Select
                          value={s.autonomy_level}
                          onChange={e => patch.mutate({ name: s.name, level: e.target.value as SkillRow['autonomy_level'] })}
                          className="!h-7 !py-0 !pr-6 !pl-2 !text-[11.5px]"
                        >
                          <option value="auto">auto</option>
                          <option value="review">review</option>
                          <option value="manual">manual</option>
                        </Select>
                        <Badge tone={AUTONOMY_TONE[s.autonomy_level]}>{s.autonomy_level}</Badge>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

