import { useState } from 'react';
import { ChevronDown, ChevronRight, Inbox, MessageSquare } from 'lucide-react';
import { DecisionsCard } from '@/components/panels/DecisionsCard';
import { InboxCard } from '@/components/panels/InboxCard';
import { TelegramBridgeStatus } from '@/components/panels/TelegramBridgeStatus';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Skeleton } from '@/components/ui/Skeleton';
import { useDecisions, useInbox } from '@/hooks/useQueries';
import { fmtAgoFromIso } from '@/lib/format';
import { cn } from '@/lib/cn';

export default function DecisionsPage() {
  const [showHistory, setShowHistory] = useState(false);

  const { data: pendingData } = useDecisions('pending');
  const { data: inboxData } = useInbox(0);

  const pending = pendingData?.items.length ?? 0;
  const unread = (inboxData?.items ?? []).filter(m => !m.read).length;

  return (
    <div className="space-y-4 pb-8">
      <header className="flex items-center justify-between">
        <div>
          <Kicker>HITL · queue</Kicker>
          <h1 className="text-2xl font-semibold tracking-tight text-text">Decisions queue</h1>
          <p className="text-[13px] text-text-dim mt-1">
            Approve risky steps and answer agent questions. Replies pipe back into the running session.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <Badge tone={pending > 0 ? 'amber' : 'neutral'}>
            <MessageSquare size={11} className="mr-1" />{pending} pending
          </Badge>
          <Badge tone={unread > 0 ? 'amber' : 'neutral'}>
            <Inbox size={11} className="mr-1" />{unread} unread
          </Badge>
        </div>
      </header>

      <TelegramBridgeStatus />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <DecisionsCard />
        <InboxCard />
      </div>

      <Card>
        <CardHeader>
          <div className="flex-1">
            <button
              onClick={() => setShowHistory(v => !v)}
              className="flex items-center gap-2 text-left group"
            >
              {showHistory ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <div>
                <Kicker>HITL · history</Kicker>
                <CardTitle className="group-hover:text-accent-blue transition-colors">
                  Answered decisions
                </CardTitle>
              </div>
            </button>
            <CardDescription className="mt-1">
              Recent decisions you've already answered.
            </CardDescription>
          </div>
        </CardHeader>
        {showHistory && (
          <CardContent>
            <AnsweredList />
          </CardContent>
        )}
      </Card>
    </div>
  );
}

function AnsweredList() {
  const { data, isLoading } = useDecisions('answered');
  const items = data?.items ?? [];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
      </div>
    );
  }

  if (items.length === 0) {
    return <div className="text-[13px] text-text-subtle text-center py-8">no answered decisions yet</div>;
  }

  return (
    <ul className="space-y-2">
      {items.map(d => (
        <li
          key={d.id}
          className={cn(
            'bg-surface-2/40 border border-border rounded-lg p-3',
            'text-[12.5px]',
          )}
        >
          <div className="flex items-start justify-between gap-3 mb-1.5">
            <div className="flex-1 min-w-0">
              <div className="text-text line-clamp-2 mb-1">{d.prompt}</div>
              <div className="flex items-center gap-2 text-[11px] text-text-subtle font-mono">
                {d.task_id != null && <Badge tone="blue">task #{d.task_id}</Badge>}
                {d.session_id && <span>session {d.session_id.slice(0, 8)}</span>}
                <span>· answered {fmtAgoFromIso(d.answered_at ?? d.created_at)}</span>
              </div>
            </div>
            <Badge tone="green">answered</Badge>
          </div>
          {d.answer && (
            <div className="text-[12px] text-text-dim bg-surface-3/40 border border-border/40 rounded px-2 py-1.5 mt-1.5 whitespace-pre-wrap">
              {d.answer}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

