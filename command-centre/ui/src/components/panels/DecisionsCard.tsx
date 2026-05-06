// Pending decisions prompted by running sessions. Click row → answer in Modal.
import { FormEvent, useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';
import { Textarea, Label } from '@/components/ui/Field';
import { Skeleton } from '@/components/ui/Skeleton';
import { useAnswerDecision, useDecisions } from '@/hooks/useQueries';
import type { DecisionRow } from '@/lib/types';
import { fmtAgeSeconds } from '@/lib/format';

export function DecisionsCard() {
  const { data, isLoading } = useDecisions('pending');
  const [active, setActive] = useState<DecisionRow | null>(null);
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>HITL · decisions</Kicker>
          <CardTitle>Awaiting your answer</CardTitle>
          <CardDescription>
            Emitted from session stdout as <span className="font-mono">DECISION:</span> markers.
          </CardDescription>
        </div>
        <Badge tone={items.length ? 'amber' : 'neutral'}>{items.length} pending</Badge>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-8">no pending decisions</div>
        ) : (
          <ul className="space-y-2">
            {items.map(d => (
              <li
                key={d.id}
                className="bg-surface-2/60 border border-border rounded-lg p-3 hover:border-border-glow transition-colors cursor-pointer"
                onClick={() => setActive(d)}
                role="button"
                tabIndex={0}
                onKeyDown={e => { if (e.key === 'Enter') setActive(d); }}
              >
                <div className="flex items-start justify-between gap-3 mb-1">
                  <div className="text-[13px] text-text line-clamp-2 flex-1">{d.prompt}</div>
                  <span className="text-[11px] font-mono text-text-subtle whitespace-nowrap">
                    {fmtAge(d.created_at)}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[11px] text-text-subtle font-mono">
                  {d.task_id != null && <Badge tone="blue">task #{d.task_id}</Badge>}
                  {d.session_id && <span>session {d.session_id.slice(0, 8)}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <AnswerModal decision={active} onClose={() => setActive(null)} />
    </Card>
  );
}

function fmtAge(iso: string): string {
  const then = Date.parse(iso);
  if (isNaN(then)) return iso;
  return fmtAgeSeconds((Date.now() - then) / 1000);
}

function AnswerModal({ decision, onClose }: { decision: DecisionRow | null; onClose: () => void }) {
  const [answer, setAnswer] = useState('');
  const answerMut = useAnswerDecision();
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (decision) { setAnswer(''); requestAnimationFrame(() => ref.current?.focus()); }
  }, [decision]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!decision || !answer.trim()) return;
    try {
      await answerMut.mutateAsync({ id: decision.id, answer: answer.trim() });
      onClose();
    } catch { /* keep open */ }
  }

  return (
    <Modal
      open={!!decision}
      onClose={onClose}
      title="Answer decision"
      footer={
        <>
          <Button type="button" variant="ghost" onClick={onClose}>cancel</Button>
          <Button
            type="submit" form="decision-answer-form" variant="primary"
            disabled={answerMut.isPending || !answer.trim()}
          >
            {answerMut.isPending ? 'submitting…' : 'submit'}
          </Button>
        </>
      }
    >
      {decision && (
        <form id="decision-answer-form" onSubmit={onSubmit} className="space-y-3">
          <div className="text-[13px] bg-surface-3/50 border border-border rounded-lg p-3 whitespace-pre-wrap">
            {decision.prompt}
          </div>
          <Label hint="piped back to the session inbox">
            Your answer
            <Textarea
              ref={ref}
              value={answer}
              onChange={e => setAnswer(e.target.value)}
              rows={4}
              placeholder="yes / no / specific guidance"
            />
          </Label>
          {answerMut.error && (
            <div className="text-[12px] text-status-red bg-status-red/5 border border-status-red/20 rounded p-2 font-mono">
              {(answerMut.error as Error).message}
            </div>
          )}
        </form>
      )}
    </Modal>
  );
}
