// Sheet to create a cron schedule from natural language.
import { FormEvent, useEffect, useRef, useState } from 'react';
import { Sheet } from '@/components/ui/Sheet';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Switch, Textarea } from '@/components/ui/Field';
import { Badge } from '@/components/ui/Badge';
import { useCreateSchedule, useSkills } from '@/hooks/useQueries';
import { parseNl } from '@/lib/api';
import type { NewSchedule } from '@/lib/api';

export function ScheduleComposer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const [nl, setNl] = useState('every weekday at 9am');
  const [cron, setCron] = useState('0 9 * * 1-5');
  const [parseNote, setParseNote] = useState<string | null>(null);
  const [parseErr, setParseErr] = useState<string | null>(null);
  const [parsing, setParsing] = useState(false);
  const [taskTitle, setTaskTitle] = useState('');
  const [taskDesc, setTaskDesc] = useState('');
  const [skill, setSkill] = useState('');
  const [enabled, setEnabled] = useState(true);

  const skills = useSkills();
  const create = useCreateSchedule();
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setName(''); setNl('every weekday at 9am'); setCron('0 9 * * 1-5');
    setParseNote(null); setParseErr(null); setParsing(false);
    setTaskTitle(''); setTaskDesc(''); setSkill(''); setEnabled(true);
    requestAnimationFrame(() => nameRef.current?.focus());
  }, [open]);

  async function onParse() {
    if (!nl.trim()) return;
    setParsing(true); setParseErr(null);
    try {
      const res = await parseNl(nl);
      setCron(res.cron_expression);
      setParseNote(res.note);
    } catch (e) {
      setParseErr((e as Error).message);
    } finally {
      setParsing(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!taskTitle.trim() || !cron.trim()) return;
    const payload: NewSchedule = {
      name: name.trim() || undefined,
      cron_expression: cron.trim(),
      task_title: taskTitle.trim(),
      task_description: taskDesc.trim() || undefined,
      assigned_skill: skill || undefined,
      enabled,
    };
    try {
      await create.mutateAsync(payload);
      onClose();
    } catch { /* keep open */ }
  }

  return (
    <Sheet open={open} onClose={onClose} title="New schedule" widthClass="w-[min(520px,95vw)]">
      <form onSubmit={onSubmit} className="p-5 space-y-4">
        <Label>
          Name
          <Input
            ref={nameRef}
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="morning-triage (optional)"
          />
        </Label>

        <div className="space-y-2">
          <Label hint="press parse →">
            When (natural language)
            <Input
              value={nl}
              onChange={e => setNl(e.target.value)}
              placeholder="every weekday at 9am"
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); onParse(); } }}
            />
          </Label>
          <div className="flex items-center gap-2">
            <Button type="button" size="sm" variant="secondary" onClick={onParse} disabled={parsing || !nl.trim()}>
              {parsing ? 'parsing…' : 'parse → cron'}
            </Button>
            <Badge tone="cyan">{cron || '—'}</Badge>
          </div>
          {parseNote && <div className="text-[11px] text-text-subtle font-mono">{parseNote}</div>}
          {parseErr && <div className="text-[11px] text-status-red font-mono">{parseErr}</div>}
        </div>

        <Label hint="raw">
          Cron expression
          <Input
            value={cron}
            onChange={e => setCron(e.target.value)}
            placeholder="0 9 * * 1-5"
            className="font-mono"
          />
        </Label>

        <div className="border-t border-border pt-4 space-y-3">
          <Label hint="required">
            Task title
            <Input
              value={taskTitle}
              onChange={e => setTaskTitle(e.target.value)}
              placeholder="What should this schedule queue?"
              required
            />
          </Label>

          <Label>
            Task description
            <Textarea
              value={taskDesc}
              onChange={e => setTaskDesc(e.target.value)}
              placeholder="Context for the agent…"
              rows={3}
            />
          </Label>

          <Label>
            Skill
            <Select value={skill} onChange={e => setSkill(e.target.value)}>
              <option value="">auto-route</option>
              {skills.data?.items.map(s => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </Select>
          </Label>

          <Switch checked={enabled} onChange={setEnabled} label="Enable immediately" />
        </div>

        {create.error && (
          <div className="text-[12px] text-status-red bg-status-red/5 border border-status-red/20 rounded p-2 font-mono">
            {(create.error as Error).message}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
          <Button type="button" variant="ghost" onClick={onClose}>cancel</Button>
          <Button
            type="submit" variant="primary"
            disabled={create.isPending || !taskTitle.trim() || !cron.trim()}
          >
            {create.isPending ? 'creating…' : 'create schedule'}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
