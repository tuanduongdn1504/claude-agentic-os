// Sheet to queue a new task. Title + description + skill + model + mode + risk + flags.
import { FormEvent, useEffect, useRef, useState } from 'react';
import { Sheet } from '@/components/ui/Sheet';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Switch, Textarea } from '@/components/ui/Field';
import { useCreateTask, useSkills } from '@/hooks/useQueries';
import type { NewTask } from '@/lib/api';
import type { TaskMode, TaskQuadrant } from '@/lib/types';

const MODELS = ['', 'claude-sonnet-4-5', 'claude-opus-4-5', 'claude-haiku-4-5'];
const QUADRANTS: TaskQuadrant[] = ['do', 'schedule', 'delegate', 'archive'];
const RISKS = ['low', 'medium', 'high'];

export function TaskComposer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [successCriteria, setSuccessCriteria] = useState('');
  const [skill, setSkill] = useState('');
  const [model, setModel] = useState('');
  const [mode, setMode] = useState<TaskMode>('classic');
  const [priority, setPriority] = useState(5);
  const [quadrant, setQuadrant] = useState<TaskQuadrant>('do');
  const [risk, setRisk] = useState('low');
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [dryRun, setDryRun] = useState(false);

  const skills = useSkills();
  const create = useCreateTask();
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setTitle(''); setDescription(''); setSuccessCriteria(''); setSkill(''); setModel('');
    setMode('classic'); setPriority(5); setQuadrant('do'); setRisk('low');
    setRequiresApproval(false); setDryRun(false);
    requestAnimationFrame(() => titleRef.current?.focus());
  }, [open]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    const payload: NewTask = {
      title: title.trim(),
      description: description.trim() || undefined,
      success_criteria: successCriteria.trim() || undefined,
      assigned_skill: skill || undefined,
      model: model || undefined,
      execution_mode: mode,
      priority,
      quadrant,
      risk_level: risk,
      requires_approval: requiresApproval,
      dry_run: dryRun,
    };
    try {
      await create.mutateAsync(payload);
      onClose();
    } catch {
      /* stays open so user can retry */
    }
  }

  return (
    <Sheet open={open} onClose={onClose} title="Queue a task" widthClass="w-[min(520px,95vw)]">
      <form onSubmit={onSubmit} className="p-5 space-y-4">
        <Label hint="required">
          Title
          <Input
            ref={titleRef}
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder="What should the agent do?"
            required
          />
        </Label>

        <Label hint="optional · markdown ok">
          Description
          <Textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            placeholder="Context, constraints, acceptance criteria…"
          />
        </Label>

        {/* v0.7.0 — optional success criteria. Only sharpens the reviewer
            prompt when the assigned skill has review_mode on; ignored
            otherwise. Free text, no client-side validation. */}
        <Label hint="optional · read by the reviewer">
          Success criteria
          <Textarea
            value={successCriteria}
            onChange={e => setSuccessCriteria(e.target.value)}
            placeholder={'EARS-ish, e.g. "WHEN the build runs, THE system SHALL exit 0 and write dist/app.js"'}
            data-testid="task-success-criteria"
          />
        </Label>

        <div className="grid grid-cols-2 gap-3">
          <Label>
            Skill
            <Select value={skill} onChange={e => setSkill(e.target.value)}>
              <option value="">auto-route</option>
              {skills.data?.items.map(s => (
                <option key={s.name} value={s.name}>
                  {s.name} {s.autonomy_level !== 'auto' ? `· ${s.autonomy_level}` : ''}
                </option>
              ))}
            </Select>
          </Label>

          <Label>
            Model
            <Select value={model} onChange={e => setModel(e.target.value)}>
              {MODELS.map(m => <option key={m} value={m}>{m || 'default'}</option>)}
            </Select>
          </Label>
        </div>

        <div className="grid grid-cols-3 gap-3">
          <Label>
            Mode
            <Select value={mode} onChange={e => setMode(e.target.value as TaskMode)}>
              <option value="classic">one-shot</option>
              <option value="stream">stream</option>
            </Select>
          </Label>

          <Label>
            Priority
            <Input
              type="number" min={0} max={10}
              value={priority}
              onChange={e => setPriority(Number(e.target.value))}
            />
          </Label>

          <Label>
            Quadrant
            <Select value={quadrant} onChange={e => setQuadrant(e.target.value as TaskQuadrant)}>
              {QUADRANTS.map(q => <option key={q} value={q}>{q}</option>)}
            </Select>
          </Label>
        </div>

        <Label>
          Risk
          <Select value={risk} onChange={e => setRisk(e.target.value)}>
            {RISKS.map(r => <option key={r} value={r}>{r}</option>)}
          </Select>
        </Label>

        <div className="space-y-2 pt-1">
          <Switch
            checked={requiresApproval}
            onChange={setRequiresApproval}
            label="Require human approval before launch"
          />
          <Switch
            checked={dryRun}
            onChange={setDryRun}
            label="Dry-run (skill plans but skips side-effects)"
          />
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
            disabled={create.isPending || !title.trim()}
          >
            {create.isPending ? 'queuing…' : 'queue task'}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}
