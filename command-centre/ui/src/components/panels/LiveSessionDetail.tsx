// Right-side Sheet opened from LiveSessions or Sessions. Tool timeline + follow-up input.
import { FormEvent, useState } from 'react';
import { Sheet } from '@/components/ui/Sheet';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { Textarea } from '@/components/ui/Field';
import { usePostLiveMessage, useSessionDetails } from '@/hooks/useQueries';
import { cwdShort, fmtMs, fmtUsd } from '@/lib/format';
import type { ToolTimelineRow } from '@/lib/types';
import { Send } from 'lucide-react';

export function LiveSessionDetail({ sessionId, onClose }: { sessionId: string | null; onClose: () => void }) {
  const { data, isLoading } = useSessionDetails(sessionId);
  const [body, setBody] = useState('');
  const post = usePostLiveMessage();

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!sessionId || !body.trim()) return;
    try {
      await post.mutateAsync({ id: sessionId, body: body.trim() });
      setBody('');
    } catch { /* keep text */ }
  }

  const session = data?.session;
  const timeline = data?.tool_timeline ?? [];
  const tokenBreakdown = data?.token_breakdown ?? {};

  return (
    <Sheet
      open={!!sessionId}
      onClose={onClose}
      widthClass="w-[min(640px,95vw)]"
      title={
        session?.title ?? (
          <span className="font-mono text-text-dim">
            session {sessionId?.slice(0, 12)}
          </span>
        )
      }
    >
      <div className="p-5 space-y-4">
        {isLoading && !data ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
          </div>
        ) : session ? (
          <>
            <div className="grid grid-cols-2 gap-3 text-[12px]">
              <Stat label="model" value={session.model ?? '—'} />
              <Stat label="branch" value={session.git_branch ?? '—'} />
              <Stat label="cwd" value={cwdShort(session.cwd)} mono />
              <Stat label="started" value={session.started_at?.slice(0, 19) ?? '—'} mono />
              <Stat label="tokens (effective)" value={session.effective_tokens.toLocaleString()} />
              <Stat label="cost" value={fmtUsd(session.cost_usd)} />
              <Stat label="errors" value={String(session.error_count)} />
              <Stat label="duration" value={fmtMs(session.duration_ms)} />
            </div>

            <div className="flex flex-wrap gap-1.5">
              {Object.entries(tokenBreakdown).map(([k, v]) => (
                <Badge key={k} tone="neutral">{k}: {Number(v).toLocaleString()}</Badge>
              ))}
            </div>

            <section className="space-y-1">
              <div className="kicker mb-2">Tool timeline · {timeline.length} calls</div>
              <div className="max-h-[320px] overflow-y-auto space-y-1 pr-1">
                {timeline.length === 0 ? (
                  <div className="text-[12px] text-text-subtle py-4 text-center">no tool calls recorded</div>
                ) : (
                  timeline.slice(-120).map(t => <TimelineRow key={t.tool_use_id} row={t} />)
                )}
              </div>
            </section>

            {!session.ended_at && (
              <form onSubmit={onSubmit} className="border-t border-border pt-3 space-y-2">
                <div className="kicker">Follow-up</div>
                <Textarea
                  value={body}
                  onChange={e => setBody(e.target.value)}
                  placeholder="Inject a user message into this session's mailbox…"
                  rows={3}
                />
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-text-subtle font-mono">
                    piped into the session's stream mailbox
                  </span>
                  <Button
                    type="submit" size="sm" variant="primary"
                    leftIcon={<Send size={12} />}
                    disabled={post.isPending || !body.trim()}
                  >
                    {post.isPending ? 'sending…' : 'send'}
                  </Button>
                </div>
                {post.error && (
                  <div className="text-[11px] text-status-red font-mono">
                    {(post.error as Error).message}
                  </div>
                )}
              </form>
            )}
          </>
        ) : (
          <div className="text-[13px] text-text-subtle text-center py-8">no session loaded</div>
        )}
      </div>
    </Sheet>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="bg-surface-2/40 border border-border rounded-lg px-3 py-2">
      <div className="kicker mb-0.5">{label}</div>
      <div className={mono ? 'font-mono text-[12px] truncate' : 'text-[12.5px] truncate'} title={value}>
        {value}
      </div>
    </div>
  );
}

function TimelineRow({ row }: { row: ToolTimelineRow }) {
  const errored = row.error === 1;
  return (
    <div className={`flex items-center gap-2 text-[11.5px] font-mono py-1 px-2 rounded border ${
      errored ? 'bg-status-red/5 border-status-red/20' : 'bg-surface-2/30 border-transparent'
    }`}>
      <span className="text-text-subtle w-[62px] tabular-nums">
        {row.ts.slice(11, 19)}
      </span>
      <span className={`flex-1 truncate ${errored ? 'text-status-red' : 'text-text'}`}>
        {row.tool_name}
      </span>
      {row.is_subagent === 1 && <Badge tone="purple">sub</Badge>}
      {row.caller && <span className="text-text-subtle">{row.caller}</span>}
      <span className="text-text-subtle w-[48px] text-right tabular-nums">
        {row.duration_ms != null ? fmtMs(row.duration_ms) : '—'}
      </span>
    </div>
  );
}
