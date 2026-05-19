// Inbox of agent→user messages, with mark-read + inline reply.
import { FormEvent, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Textarea } from '@/components/ui/Field';
import { useInbox, useMarkInboxRead, useReplyInbox } from '@/hooks/useQueries';
import type { InboxRow } from '@/lib/types';
import { fmtAgoFromIso } from '@/lib/format';
import { Check, Reply } from 'lucide-react';

export function InboxCard() {
  const { data, isLoading } = useInbox(0);
  const items = data?.items ?? [];
  const unread = items.filter(m => !m.read).length;

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>HITL · inbox</Kicker>
          <CardTitle>Messages</CardTitle>
          <CardDescription>
            Agent-to-user from <span className="font-mono">INBOX:</span> markers · user-to-agent shows your replies.
          </CardDescription>
        </div>
        <Badge tone={unread > 0 ? 'amber' : 'neutral'}>{unread} unread</Badge>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">inbox is empty</div>
        ) : (
          <ul className="space-y-2">
            {items.map(m => <InboxItem key={m.id} msg={m} />)}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function InboxItem({ msg }: { msg: InboxRow }) {
  const [replying, setReplying] = useState(false);
  const [body, setBody] = useState('');
  const markRead = useMarkInboxRead();
  const reply = useReplyInbox();
  const isAgent = msg.direction === 'agent_to_user';

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    try {
      await reply.mutateAsync({ id: msg.id, body: body.trim() });
      setBody(''); setReplying(false);
    } catch { /* keep open */ }
  }

  return (
    <li className={`rounded-lg p-3 border transition-colors ${
      msg.read
        ? 'bg-surface-2/40 border-border'
        : 'bg-accent-blue/5 border-accent-blue/30'
    }`}>
      <div className="flex items-start justify-between gap-3 mb-1">
        <div className="flex items-center gap-2">
          <Badge tone={isAgent ? 'cyan' : 'purple'}>
            {isAgent ? 'agent → you' : 'you → agent'}
          </Badge>
          {msg.task_id != null && <Badge tone="blue">task #{msg.task_id}</Badge>}
          {msg.session_id && (
            <span className="text-[11px] font-mono text-text-subtle">
              session {msg.session_id.slice(0, 8)}
            </span>
          )}
        </div>
        <span className="text-[11px] font-mono text-text-subtle whitespace-nowrap">
          {fmtAgoFromIso(msg.created_at)}
        </span>
      </div>

      <div className="text-[13px] whitespace-pre-wrap mb-2">{msg.body}</div>

      <div className="flex items-center gap-2">
        {isAgent && !msg.read && (
          <Button
            size="sm" variant="ghost"
            leftIcon={<Check size={12} />}
            onClick={() => markRead.mutate(msg.id)}
            disabled={markRead.isPending}
          >
            mark read
          </Button>
        )}
        {isAgent && !replying && (
          <Button
            size="sm" variant="secondary"
            leftIcon={<Reply size={12} />}
            onClick={() => setReplying(true)}
          >
            reply
          </Button>
        )}
      </div>

      {replying && (
        <form onSubmit={onSubmit} className="mt-2 space-y-2">
          <Textarea
            value={body}
            onChange={e => setBody(e.target.value)}
            placeholder="Your reply to the agent…"
            autoFocus
            rows={3}
          />
          <div className="flex items-center gap-2">
            <Button type="submit" size="sm" variant="primary" disabled={reply.isPending || !body.trim()}>
              {reply.isPending ? 'sending…' : 'send reply'}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setReplying(false); setBody(''); }}>
              cancel
            </Button>
          </div>
        </form>
      )}
    </li>
  );
}

