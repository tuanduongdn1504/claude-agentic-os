import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Input } from '@/components/ui/Field';
import { LiveSessionDetail } from '@/components/panels/LiveSessionDetail';
import { RangePicker } from '@/components/panels/TokenUsageCard';
import * as api from '@/lib/api';
import type { Range } from '@/lib/api';
import type { CostSource, SessionRow } from '@/lib/types';
import { cwdShort, fmtMs, fmtUsd, fmtTimeUTC7, localDateUTC7 } from '@/lib/format';
import { cn } from '@/lib/cn';
import { CostSourcePill, CostSourceFilter } from '@/components/panels/CostSourceUI';

function toLocalDate(iso: string | null): string {
  return localDateUTC7(iso);
}

function labelDate(ymd: string): string {
  const today = localDateUTC7(new Date().toISOString());
  const yesterday = localDateUTC7(new Date(Date.now() - 86_400_000).toISOString());
  if (ymd === today) return 'Today';
  if (ymd === yesterday) return 'Yesterday';
  try {
    return new Date(ymd + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch {
    return ymd;
  }
}

function projectLabel(cwd: string): string {
  const short = cwdShort(cwd);
  return short.split('/').filter(Boolean).pop() || short;
}

export default function SessionsPage() {
  const [range, setRange] = useState<Range>('30d');
  const [q, setQ] = useState('');
  const [selectedCwd, setSelectedCwd] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [costFilter, setCostFilter] = useState<CostSource | 'all'>('all');

  const { data, isLoading } = useQuery({
    queryKey: ['sessions-explorer', range, costFilter],
    queryFn: () => api.listSessions({
      range, limit: 500,
      cost_source: costFilter === 'all' ? undefined : costFilter,
    }),
    refetchInterval: 60_000,
  });

  const allSessions = data?.items ?? [];

  const projects = useMemo(() => {
    const map = new Map<string, { sessions: number; cost: number; lastAt: string | null }>();
    for (const s of allSessions) {
      const key = s.cwd ?? '(unknown)';
      const b = map.get(key) ?? { sessions: 0, cost: 0, lastAt: null };
      b.sessions += 1;
      b.cost += s.cost_usd ?? 0;
      if (!b.lastAt || (s.started_at && s.started_at > b.lastAt)) b.lastAt = s.started_at;
      map.set(key, b);
    }
    return Array.from(map.entries())
      .map(([cwd, v]) => ({ cwd, ...v }))
      .sort((a, b) => (b.lastAt ?? '') > (a.lastAt ?? '') ? 1 : -1);
  }, [allSessions]);

  const filteredSessions = useMemo(() => {
    let list = allSessions;
    if (selectedCwd) list = list.filter(s => (s.cwd ?? '(unknown)') === selectedCwd);
    if (q.trim()) {
      const lc = q.toLowerCase();
      list = list.filter(s =>
        (s.title ?? '').toLowerCase().includes(lc) ||
        (s.cwd ?? '').toLowerCase().includes(lc) ||
        s.session_id.toLowerCase().includes(lc),
      );
    }
    return list;
  }, [allSessions, selectedCwd, q]);

  const grouped = useMemo(() => {
    const map = new Map<string, SessionRow[]>();
    for (const s of filteredSessions) {
      const d = toLocalDate(s.started_at);
      const g = map.get(d) ?? [];
      g.push(s);
      map.set(d, g);
    }
    return Array.from(map.entries()).sort(([a], [b]) => b.localeCompare(a));
  }, [filteredSessions]);

  return (
    <div className="flex gap-4 h-[calc(100vh-80px)] overflow-hidden">
      {/* Left: project list */}
      <div className="w-[240px] flex-shrink-0 flex flex-col gap-1 overflow-y-auto pb-4">
        <div className="kicker text-[11px] px-1 pt-1 pb-2 sticky top-0 bg-background z-10">
          Projects · {projects.length}
        </div>

        <ProjectBtn
          label="All projects"
          sub={`${allSessions.length} sessions`}
          active={selectedCwd === null}
          onClick={() => setSelectedCwd(null)}
        />

        {isLoading
          ? Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)
          : projects.map(p => (
              <ProjectBtn
                key={p.cwd}
                label={projectLabel(p.cwd)}
                sub={`${p.sessions} sess · ${fmtUsd(p.cost)}`}
                title={cwdShort(p.cwd)}
                active={selectedCwd === p.cwd}
                onClick={() => setSelectedCwd(p.cwd)}
              />
            ))}
      </div>

      {/* Right: session timeline */}
      <div className="flex-1 flex flex-col gap-3 overflow-hidden min-w-0">
        <div className="flex items-center gap-3 flex-shrink-0 flex-wrap">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-subtle pointer-events-none" />
            <Input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="search title, cwd, session id…"
              className="!pl-7 !h-8 !text-[12px] w-full"
            />
          </div>
          <CostSourceFilter value={costFilter} onChange={setCostFilter} />
          <RangePicker value={range} onChange={setRange} />
        </div>

        <div className="text-[11px] kicker px-0.5">
          {selectedCwd
            ? <span title={selectedCwd}>{cwdShort(selectedCwd)}</span>
            : 'All projects'
          }
          {' · '}{filteredSessions.length} sessions
        </div>

        <div className="flex-1 overflow-y-auto space-y-5 pr-1 pb-4">
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
            </div>
          ) : grouped.length === 0 ? (
            <div className="text-[13px] text-text-subtle text-center py-20">no sessions found</div>
          ) : (
            grouped.map(([date, sessions]) => (
              <div key={date}>
                <div className="kicker text-[11px] border-b border-border/50 pb-1.5 mb-1">
                  {labelDate(date)} · {sessions.length}
                </div>
                {sessions.map(s => (
                  <SessionItem
                    key={s.session_id}
                    session={s}
                    onClick={() => setActiveSession(s.session_id)}
                  />
                ))}
              </div>
            ))
          )}
        </div>
      </div>

      <LiveSessionDetail sessionId={activeSession} onClose={() => setActiveSession(null)} />
    </div>
  );
}

function ProjectBtn({
  label, sub, title, active, onClick,
}: { label: string; sub: string; title?: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cn(
        'text-left px-3 py-2 rounded-lg border text-[12.5px] transition-colors w-full',
        active
          ? 'bg-surface-2 border-border text-text'
          : 'border-transparent hover:bg-surface-2/60 text-text-dim hover:text-text',
      )}
    >
      <div className="font-medium truncate">{label}</div>
      <div className="text-[11px] text-text-subtle">{sub}</div>
    </button>
  );
}

function SessionItem({ session: s, onClick }: { session: SessionRow; onClick: () => void }) {
  const tone = s.is_error_any ? 'red' : s.rate_limit_hit ? 'amber' : !s.ended_at ? 'neutral' : 'green';
  const status = s.is_error_any ? 'error' : s.rate_limit_hit ? 'rate_limit' : !s.ended_at ? 'open' : 'ok';
  return (
    <div
      className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surface-2/40 cursor-pointer
                 text-[12.5px] border border-transparent hover:border-border/40 transition-colors"
      onClick={onClick}
    >
      <div className="flex-1 min-w-0">
        <div className="truncate text-text" title={s.title ?? s.session_id}>
          {s.title
            ? s.title
            : <span className="font-mono text-text-dim">{s.session_id.slice(0, 12)}</span>
          }
        </div>
        <div className="text-[11px] text-text-subtle font-mono">
          {fmtTimeUTC7(s.started_at)}
          {s.model ? ` · ${s.model.replace(/^claude-/, '').split('-').slice(0, 2).join('-')}` : ''}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0 text-text-dim">
        <CostSourcePill source={s.cost_source} />
        {s.duration_ms != null && <span className="num text-[12px]">{fmtMs(s.duration_ms)}</span>}
        <span className="num text-[12px]">{fmtUsd(s.cost_usd)}</span>
        <Badge tone={tone}>{status}</Badge>
      </div>
    </div>
  );
}
