// List of cron schedules; toggle enabled, delete, open composer.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatePill } from '@/components/ui/StatePill';
import { useDeleteSchedule, usePatchSchedule, useSchedules } from '@/hooks/useQueries';
import { fmtAgoFromIso, fmtDateTimeUTC7 } from '@/lib/format';
import { CalendarPlus, Power, Trash2 } from 'lucide-react';
import { ScheduleComposer } from './ScheduleComposer';

export function SchedulesCard() {
  const { data, isLoading } = useSchedules();
  const patch = usePatchSchedule();
  const del = useDeleteSchedule();
  const items = data?.items ?? [];
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Schedules</Kicker>
          <CardTitle>Cron jobs</CardTitle>
          <CardDescription>Natural-language → cron. Materializes a task at next_run_at.</CardDescription>
        </div>
        <Button
          size="sm" variant="primary"
          leftIcon={<CalendarPlus size={14} />}
          onClick={() => setOpen(true)}
        >
          new schedule
        </Button>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no schedules configured</div>
        ) : (
          <ul className="space-y-2">
            {items.map(s => (
              <li key={s.id} className="bg-surface-2/60 border border-border rounded-lg p-3">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="min-w-0">
                    <div className="text-[13px] font-medium truncate">{s.name ?? s.task_title}</div>
                    <div className="text-[11.5px] text-text-dim truncate" title={s.task_title}>
                      → {s.task_title}
                    </div>
                  </div>
                  <StatePill tone={s.enabled ? 'ok' : 'idle'}>
                    {s.enabled ? 'enabled' : 'paused'}
                  </StatePill>
                </div>
                <div className="flex items-center flex-wrap gap-2 text-[11px] text-text-subtle font-mono mb-2">
                  <Badge tone="cyan">{s.cron_expression}</Badge>
                  {s.assigned_skill && <Badge tone="purple">{s.assigned_skill}</Badge>}
                  {s.next_run_at && <span title={s.next_run_at}>next: {fmtDateTimeUTC7(s.next_run_at)}</span>}
                  {s.last_run_at && <span title={s.last_run_at}>last: {fmtAgoFromIso(s.last_run_at)}</span>}
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm" variant="ghost"
                    leftIcon={<Power size={12} />}
                    onClick={() => patch.mutate({ id: s.id, p: { enabled: !s.enabled } })}
                    disabled={patch.isPending}
                  >
                    {s.enabled ? 'pause' : 'resume'}
                  </Button>
                  <Button
                    size="sm" variant="ghost"
                    leftIcon={<Trash2 size={12} />}
                    onClick={() => { if (confirm(`Delete schedule "${s.name ?? s.task_title}"?`)) del.mutate(s.id); }}
                    disabled={del.isPending}
                    className="ml-auto"
                  >
                    delete
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <ScheduleComposer open={open} onClose={() => setOpen(false)} />
    </Card>
  );
}

