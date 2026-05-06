// 3-column task board: pending · running · done. Per-row approve/rerun/delete.
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { StatePill } from '@/components/ui/StatePill';
import {
  useApproveTask, useDeleteTask, useRerunTask, useTasks, useTriggerDispatcher,
} from '@/hooks/useQueries';
import type { TaskQuadrant, TaskRow, TaskStatus } from '@/lib/types';
import { fmtMs, fmtUsd } from '@/lib/format';
import { CheckCircle2, Play, RotateCcw, Trash2, Zap } from 'lucide-react';

type Column = { key: 'queue' | 'running' | 'done'; label: string; statuses: TaskStatus[] };
const COLUMNS: Column[] = [
  { key: 'queue',   label: 'queue',   statuses: ['pending', 'awaiting_approval'] },
  { key: 'running', label: 'running', statuses: ['running'] },
  { key: 'done',    label: 'done',    statuses: ['done', 'failed', 'cancelled'] },
];

const QUADRANT_DOT: Record<TaskQuadrant, string> = {
  do:       'bg-status-red',
  schedule: 'bg-accent-blue',
  delegate: 'bg-accent-purple',
  archive:  'bg-text-subtle',
};

function statusTone(s: TaskStatus) {
  switch (s) {
    case 'running':            return 'info' as const;
    case 'done':               return 'ok' as const;
    case 'failed':             return 'error' as const;
    case 'cancelled':          return 'idle' as const;
    case 'awaiting_approval':  return 'warn' as const;
    default:                   return 'idle' as const;
  }
}

export function TaskBoard() {
  const { data, isLoading } = useTasks({});
  const trigger = useTriggerDispatcher();
  const items = data?.items ?? [];

  const grouped: Record<Column['key'], TaskRow[]> = { queue: [], running: [], done: [] };
  for (const t of items) {
    const col = COLUMNS.find(c => c.statuses.includes(t.status))?.key ?? 'queue';
    grouped[col].push(t);
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Mission Control</Kicker>
          <CardTitle>Task board</CardTitle>
          <CardDescription>
            Claimed by the dispatcher. Approve awaiting, rerun failed, queue new from the button →
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm" variant="ghost"
            leftIcon={<Zap size={14} />}
            onClick={() => trigger.mutate()}
            disabled={trigger.isPending}
            title="Poke the dispatcher to claim pending tasks"
          >
            {trigger.isPending ? 'triggering…' : 'trigger dispatcher'}
          </Button>
          <Button
            size="sm" variant="primary"
            data-action="open-task-composer"
            leftIcon={<Play size={14} />}
          >
            queue a task
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {COLUMNS.map(col => (
            <div key={col.key} className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="kicker">{col.label}</span>
                <span className="text-[11px] text-text-subtle font-mono">{grouped[col.key].length}</span>
              </div>
              <div className="space-y-2 min-h-[120px]">
                {isLoading && Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}
                {!isLoading && grouped[col.key].length === 0 && (
                  <div className="text-[12px] text-text-subtle text-center py-8 border border-dashed border-border/60 rounded-lg">
                    nothing here
                  </div>
                )}
                {!isLoading && grouped[col.key].map(t => <TaskCard key={t.id} task={t} />)}
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function TaskCard({ task }: { task: TaskRow }) {
  const approve = useApproveTask();
  const rerun = useRerunTask();
  const del = useDeleteTask();
  const quadrant = task.quadrant ?? 'do';

  return (
    <div className="bg-surface-2/60 border border-border rounded-lg p-3 hover:border-border-glow transition-colors">
      <div className="flex items-start gap-2 mb-2">
        <span
          className={`w-1.5 h-1.5 rounded-full mt-[6px] ${QUADRANT_DOT[quadrant]}`}
          title={`${quadrant} quadrant`}
        />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium truncate" title={task.title}>{task.title}</div>
          {task.description && (
            <div className="text-[11.5px] text-text-dim line-clamp-2 mt-0.5">{task.description}</div>
          )}
        </div>
      </div>

      <div className="flex items-center flex-wrap gap-1.5 mb-2">
        <StatePill tone={statusTone(task.status)} className="!py-0.5 !text-[10.5px]">
          {task.status.replace('_', ' ')}
        </StatePill>
        {task.assigned_skill && (
          <Badge tone="purple">{task.assigned_skill}</Badge>
        )}
        {task.model && <Badge tone="blue">{task.model}</Badge>}
        {task.execution_mode === 'stream' && <Badge tone="cyan">stream</Badge>}
        {!!task.dry_run && <Badge tone="amber">dry-run</Badge>}
        {task.risk_level && task.risk_level !== 'low' && (
          <Badge tone={task.risk_level === 'high' ? 'red' : 'amber'}>{task.risk_level}</Badge>
        )}
      </div>

      {(task.duration_ms != null || task.cost_usd != null) && (
        <div className="flex items-center gap-3 text-[11px] text-text-subtle font-mono mb-2">
          {task.duration_ms != null && <span>{fmtMs(task.duration_ms)}</span>}
          {task.cost_usd != null && <span>{fmtUsd(task.cost_usd)}</span>}
          {task.consecutive_failures > 0 && (
            <span className="text-status-red">{task.consecutive_failures} fails</span>
          )}
        </div>
      )}

      {task.error_message && (
        <div className="text-[11px] text-status-red/80 font-mono bg-status-red/5 border border-status-red/20 rounded p-1.5 mb-2 line-clamp-2">
          {task.error_message}
        </div>
      )}

      <div className="flex items-center gap-1">
        {task.status === 'awaiting_approval' && (
          <Button
            size="sm" variant="primary"
            leftIcon={<CheckCircle2 size={12} />}
            onClick={() => approve.mutate(task.id)}
            disabled={approve.isPending}
          >
            approve
          </Button>
        )}
        {task.status === 'failed' && (
          <Button
            size="sm" variant="secondary"
            leftIcon={<RotateCcw size={12} />}
            onClick={() => rerun.mutate(task.id)}
            disabled={rerun.isPending}
          >
            rerun
          </Button>
        )}
        <Button
          size="sm" variant="ghost"
          leftIcon={<Trash2 size={12} />}
          onClick={() => { if (confirm(`Delete task "${task.title}"?`)) del.mutate(task.id); }}
          disabled={del.isPending}
          className="ml-auto"
          aria-label="Delete task"
        >
          delete
        </Button>
      </div>
    </div>
  );
}
