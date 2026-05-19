// v0.6.0 — one-click skill launcher on the Command page.
//
// Sits between Token-usage and the Observability section. Reads skills
// filtered to user_invocable=1, sorts by launch_count then last-launched,
// renders 3-col grid at lg, 2 at sm, 1 below. Launch button posts to
// /api/skills/{name}/launch (api_pool spend) and the dispatcher is poked
// inline so the task starts within ~1s. Edit pencil expands an inline
// editor beneath the row (NOT a Sheet, NOT a Modal — matches the panel
// rhythm) with the 9 preset fields. Esc cancels.
import { AnimatePresence, motion } from 'framer-motion';
import { Pencil, Play, Sparkles, X } from 'lucide-react';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Button } from '@/components/ui/Button';
import { Input, Label, Select, Switch, Textarea } from '@/components/ui/Field';
import { Badge } from '@/components/ui/Badge';
import { useLaunchSkill, usePatchSkillPreset, useSkills } from '@/hooks/useQueries';
import type { SkillPreset, SkillRow, TaskMode, TaskQuadrant } from '@/lib/types';
import { fmtAgeSeconds, fmtUsd } from '@/lib/format';
import { cn } from '@/lib/cn';

const SHOW_LIMIT = 12;
const MODELS = ['', 'claude-sonnet-4-5', 'claude-opus-4-5', 'claude-haiku-4-5'];
const MODES: TaskMode[] = ['classic', 'stream'];
const QUADRANTS: TaskQuadrant[] = ['do', 'schedule', 'delegate', 'archive'];
const RISKS = ['low', 'medium', 'high'] as const;

function ageSeconds(iso: string | null): number | null {
  if (!iso) return null;
  // Server emits SQLite datetime('now') as "YYYY-MM-DD HH:MM:SS" (UTC).
  // Treat any missing TZ as UTC.
  const t = Date.parse(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z');
  if (isNaN(t)) return null;
  return (Date.now() - t) / 1000;
}

export function SkillLauncher() {
  const { data, isLoading } = useSkills();
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);

  const items = useMemo(() => {
    const invocable = (data?.items ?? []).filter(s => s.user_invocable === 1);
    invocable.sort((a, b) => {
      if (b.launch_count !== a.launch_count) return b.launch_count - a.launch_count;
      const aAt = a.last_launched_at ?? '';
      const bAt = b.last_launched_at ?? '';
      if (aAt !== bAt) return bAt.localeCompare(aAt);
      return a.name.localeCompare(b.name);
    });
    return invocable;
  }, [data]);

  const overflow = items.length > SHOW_LIMIT;
  const visible = showAll ? items : items.slice(0, SHOW_LIMIT);

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Skill launcher</Kicker>
          <CardTitle>One-click skill runs</CardTitle>
          <CardDescription>
            Skills marked <code className="font-mono text-text-dim">user_invocable: true</code>.
            Launch fires a headless task on the API pool — sensible defaults baked in, edit the
            preset to change them.
          </CardDescription>
        </div>
        <Badge tone="neutral">{items.length} ready</Badge>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-[112px] w-full" />)}
          </div>
        ) : items.length === 0 ? (
          <div className="border border-dashed border-border/60 rounded-xl px-5 py-8 text-center text-[13px] text-text-subtle">
            <Sparkles size={18} className="mx-auto mb-2 opacity-60" />
            No invocable skills yet. Mark a skill{' '}
            <code className="font-mono text-text-dim bg-surface-3/60 px-1.5 py-0.5 rounded">user_invocable: true</code>{' '}
            in its frontmatter and run{' '}
            <code className="font-mono text-text-dim bg-surface-3/60 px-1.5 py-0.5 rounded">cc sync</code>.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {visible.map(s => (
                <SkillCard
                  key={s.name}
                  skill={s}
                  isEditing={editing === s.name}
                  onEditOpen={() => setEditing(s.name)}
                  onEditClose={() => setEditing(null)}
                />
              ))}
            </div>
            {overflow && !showAll && (
              <button
                className="mt-3 text-[12px] text-text-dim hover:text-text font-mono"
                onClick={() => setShowAll(true)}
              >
                Show all ({items.length}) →
              </button>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------

function SkillCard({
  skill, isEditing, onEditOpen, onEditClose,
}: {
  skill: SkillRow;
  isEditing: boolean;
  onEditOpen: () => void;
  onEditClose: () => void;
}) {
  const launch = useLaunchSkill();
  const [feedback, setFeedback] = useState<
    { kind: 'success'; taskId: number } | { kind: 'error'; message: string } | null
  >(null);

  // Auto-clear states.
  useEffect(() => {
    if (!feedback) return;
    const t = setTimeout(() => setFeedback(null), feedback.kind === 'success' ? 2500 : 6000);
    return () => clearTimeout(t);
  }, [feedback]);

  async function onLaunch() {
    setFeedback(null);
    try {
      const out = await launch.mutateAsync({ name: skill.name });
      setFeedback({ kind: 'success', taskId: out.task_id });
    } catch (exc) {
      const msg = (exc as Error)?.message || 'Launch failed';
      setFeedback({ kind: 'error', message: msg });
    }
  }

  const title = skill.preset?.title || `Run ${skill.name}`;
  const lastAge = ageSeconds(skill.last_launched_at);
  const avg = skill.avg_cost_usd_30d;

  return (
    <div className="group bg-surface/60 border border-border rounded-xl p-3 hover:border-border-glow transition-colors flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-[15px] font-semibold tracking-tight text-text truncate" title={skill.name}>
            {skill.name}
          </div>
          <div className="text-[12px] text-text-dim truncate" title={title}>{title}</div>
        </div>
        <div className="text-[11px] font-mono text-text-subtle tabular-nums shrink-0 leading-tight text-right">
          {avg != null ? `${fmtUsd(avg)} avg` : '— avg'}
          <div className="text-text-subtle/70 mt-0.5">{skill.launch_count}× run</div>
        </div>
      </div>

      <div className="flex items-center gap-2 mt-auto">
        <div className="text-[11px] text-text-subtle font-mono flex-1 min-w-0 truncate">
          {lastAge != null ? fmtAgeSeconds(lastAge) : 'never'}
        </div>

        <AnimatePresence>
          {feedback && feedback.kind === 'success' && (
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              className="text-[11px] font-mono text-status-green flex items-center gap-1"
              title="Task queued"
            >
              ↗ Task #{feedback.taskId}
            </motion.div>
          )}
        </AnimatePresence>

        <button
          onClick={onEditOpen}
          disabled={isEditing}
          className={cn(
            'inline-flex items-center justify-center h-8 w-8 rounded-lg',
            'text-text-dim hover:text-text hover:bg-surface-2 transition-colors disabled:opacity-40',
          )}
          title="Edit preset"
          aria-label={`Edit preset for ${skill.name}`}
        >
          <Pencil size={13} />
        </button>

        <Button
          size="sm"
          variant="primary"
          leftIcon={
            launch.isPending
              ? <motion.span
                  animate={{ rotate: 360 }}
                  transition={{ duration: 0.9, repeat: Infinity, ease: 'linear' }}
                  className="inline-block w-3 h-3 rounded-full border-2 border-white/60 border-t-transparent"
                />
              : <Play size={12} />
          }
          onClick={onLaunch}
          disabled={launch.isPending}
          data-skill={skill.name}
          data-action="launch-skill"
        >
          {launch.isPending ? 'launching…' : 'launch'}
        </Button>
      </div>

      <AnimatePresence>
        {feedback && feedback.kind === 'error' && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            style={{ overflow: 'hidden' }}
            className="text-[11px] font-mono text-status-red bg-status-red/5 border border-status-red/20 rounded p-1.5 flex items-start gap-1.5"
            role="alert"
          >
            <span className="flex-1 break-words">{feedback.message}</span>
            <button
              onClick={() => setFeedback(null)}
              className="text-status-red/70 hover:text-status-red shrink-0"
              aria-label="Dismiss"
            >
              <X size={11} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {isEditing && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            style={{ overflow: 'hidden' }}
          >
            <PresetEditor skill={skill} onClose={onEditClose} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---------------------------------------------------------------------------

function PresetEditor({ skill, onClose }: { skill: SkillRow; onClose: () => void }) {
  const patch = usePatchSkillPreset();
  const p = skill.preset ?? {};

  const [title, setTitle] = useState(p.title ?? '');
  const [description, setDescription] = useState(p.description ?? '');
  const [model, setModel] = useState(p.model ?? '');
  const [mode, setMode] = useState<TaskMode>((p.execution_mode as TaskMode) ?? 'classic');
  const [priority, setPriority] = useState<number>(p.priority ?? 0);
  const [quadrant, setQuadrant] = useState<TaskQuadrant>((p.quadrant as TaskQuadrant) ?? 'do');
  const [risk, setRisk] = useState<typeof RISKS[number]>(
    (p.risk_level as typeof RISKS[number]) ?? 'low',
  );
  const [requiresApproval, setRequiresApproval] = useState<boolean>(!!p.requires_approval);
  const [dryRun, setDryRun] = useState<boolean>(!!p.dry_run);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    requestAnimationFrame(() => titleRef.current?.focus());
  }, []);

  // Esc cancels.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    const preset: SkillPreset = {};
    if (title.trim()) preset.title = title.trim();
    if (description.trim()) preset.description = description.trim();
    if (model) preset.model = model;
    if (mode) preset.execution_mode = mode;
    if (priority) preset.priority = priority;
    if (quadrant) preset.quadrant = quadrant;
    if (risk) preset.risk_level = risk;
    if (requiresApproval) preset.requires_approval = true;
    if (dryRun) preset.dry_run = true;
    try {
      await patch.mutateAsync({ name: skill.name, preset });
      onClose();
    } catch {
      /* error surfaces via patch.error below */
    }
  }

  async function onClear() {
    try {
      await patch.mutateAsync({ name: skill.name, preset: null });
      onClose();
    } catch {
      /* */
    }
  }

  return (
    <form
      onSubmit={onSave}
      className="mt-2 pt-3 border-t border-border space-y-3"
      data-testid="preset-editor"
      data-skill={skill.name}
    >
      <div className="grid grid-cols-2 gap-2">
        <Label>
          Title
          <Input
            ref={titleRef}
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder={`Run ${skill.name}`}
            className="!h-8 !text-[12px]"
          />
        </Label>
        <Label>
          Model
          <Select
            value={model}
            onChange={e => setModel(e.target.value)}
            className="!h-8 !text-[12px]"
          >
            {MODELS.map(m => <option key={m} value={m}>{m || 'default'}</option>)}
          </Select>
        </Label>
      </div>

      <Label>
        Description
        <Textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder={skill.description || 'Context, constraints, acceptance criteria…'}
          className="!min-h-[64px] !text-[12px]"
        />
      </Label>

      <div className="grid grid-cols-2 gap-2">
        <Label>
          Mode
          <Select
            value={mode}
            onChange={e => setMode(e.target.value as TaskMode)}
            className="!h-8 !text-[12px]"
          >
            {MODES.map(m => (
              <option key={m} value={m}>
                {m === 'classic' ? 'one-shot' : 'interactive'}
              </option>
            ))}
          </Select>
        </Label>
        <Label>
          Priority
          <Input
            type="number" min={0} max={10}
            value={priority}
            onChange={e => setPriority(Number(e.target.value))}
            className="!h-8 !text-[12px]"
          />
        </Label>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Label>
          Quadrant
          <Select
            value={quadrant}
            onChange={e => setQuadrant(e.target.value as TaskQuadrant)}
            className="!h-8 !text-[12px]"
          >
            {QUADRANTS.map(q => <option key={q} value={q}>{q}</option>)}
          </Select>
        </Label>
        <Label>
          Risk
          <Select
            value={risk}
            onChange={e => setRisk(e.target.value as typeof RISKS[number])}
            className="!h-8 !text-[12px]"
          >
            {RISKS.map(r => <option key={r} value={r}>{r}</option>)}
          </Select>
        </Label>
      </div>

      <div className="flex flex-col gap-1.5 pt-1">
        <Switch
          checked={requiresApproval}
          onChange={setRequiresApproval}
          label="Require approval before launch"
        />
        <Switch
          checked={dryRun}
          onChange={setDryRun}
          label="Dry-run (plan only, no side-effects)"
        />
      </div>

      {patch.error && (
        <div className="text-[11px] text-status-red bg-status-red/5 border border-status-red/20 rounded p-1.5 font-mono">
          {(patch.error as Error).message}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button
          type="button"
          size="sm" variant="ghost"
          onClick={onClear}
          disabled={patch.isPending || !skill.preset}
          title="Remove the stored preset and fall back to defaults"
        >
          clear
        </Button>
        <div className="ml-auto flex items-center gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={onClose}>cancel</Button>
          <Button
            type="submit" size="sm" variant="primary"
            disabled={patch.isPending}
          >
            {patch.isPending ? 'saving…' : 'save preset'}
          </Button>
        </div>
      </div>
    </form>
  );
}
