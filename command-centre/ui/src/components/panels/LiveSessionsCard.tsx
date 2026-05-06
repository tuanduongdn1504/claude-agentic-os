// Currently-running sessions. Click → detail Sheet with timeline + follow-up.
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { StatePill } from '@/components/ui/StatePill';
import { Skeleton } from '@/components/ui/Skeleton';
import { useLiveSessions } from '@/hooks/useQueries';
import { cwdShort, fmtAgeSeconds } from '@/lib/format';
import type { SessionRow } from '@/lib/types';
import { LiveSessionDetail } from './LiveSessionDetail';

export function LiveSessionsCard() {
  const { data, isLoading } = useLiveSessions();
  const [active, setActive] = useState<string | null>(null);
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Live · sessions</Kicker>
          <CardTitle>Running now</CardTitle>
          <CardDescription>
            Active in the last {data?.window_s ? `${Math.round(data.window_s / 60)}m` : '10m'}. Click to open timeline.
          </CardDescription>
        </div>
        <StatePill tone={items.length ? 'info' : 'idle'}>{items.length} live</StatePill>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no sessions running</div>
        ) : (
          <ul className="space-y-2">
            {items.map(s => (
              <li
                key={s.session_id}
                className="bg-surface-2/60 border border-border rounded-lg p-3 hover:border-border-glow transition-colors cursor-pointer"
                onClick={() => setActive(s.session_id)}
                role="button"
                tabIndex={0}
                onKeyDown={e => { if (e.key === 'Enter') setActive(s.session_id); }}
              >
                <LiveRow s={s} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <LiveSessionDetail sessionId={active} onClose={() => setActive(null)} />
    </Card>
  );
}

function LiveRow({ s }: { s: SessionRow }) {
  const startedAgo = s.started_at ? fmtIso(s.started_at) : 'unknown';
  return (
    <div>
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="text-[13px] font-medium truncate flex-1" title={s.title ?? s.session_id}>
          {s.title ?? <span className="font-mono text-text-dim">session {s.session_id.slice(0, 12)}</span>}
        </div>
        <span className="text-[11px] font-mono text-text-subtle whitespace-nowrap">{startedAgo}</span>
      </div>
      <div className="flex items-center gap-2 text-[11px] text-text-subtle font-mono">
        {s.model && <span className="text-text-dim">{s.model}</span>}
        <span className="truncate max-w-[240px]" title={s.cwd ?? ''}>{cwdShort(s.cwd)}</span>
        {s.git_branch && <span>· {s.git_branch}</span>}
      </div>
    </div>
  );
}

function fmtIso(iso: string): string {
  const then = Date.parse(iso);
  if (isNaN(then)) return iso;
  return fmtAgeSeconds((Date.now() - then) / 1000);
}
