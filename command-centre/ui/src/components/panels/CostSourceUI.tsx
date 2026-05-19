// v0.6.0 — shared cost-source UI primitives.
//
// Pill: `api` (cyan), `max` (grey/surface), `?` (amber) for unknown.
// Filter dropdown: mirrors the three values plus an "all" default.
//
// Used by SessionsPage's table and TaskBoard's task cards. Both surfaces
// render the same enum, so the visual language stays identical.
import { cn } from '@/lib/cn';
import type { CostSource } from '@/lib/types';

interface PillProps {
  source: CostSource | undefined | null;
  size?: 'sm' | 'xs';
  className?: string;
}

export function CostSourcePill({ source, size = 'sm', className }: PillProps) {
  const value = source ?? 'unknown';
  const isUnknown = value === 'unknown';
  const isApi = value === 'api_pool' || value === 'codex_api';
  const isMax = value === 'max_sub';

  let label: string;
  if (isApi) label = value === 'codex_api' ? 'codex' : 'api';
  else if (isMax) label = 'max';
  else label = '?';

  const tone =
    isApi
      ? 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30'
      : isMax
        ? 'bg-surface-3 text-text-dim border-border'
        : 'bg-status-amber/15 text-status-amber border-status-amber/35';

  const sz = size === 'xs' ? 'h-[18px] px-1.5 text-[10px]' : 'h-[20px] px-1.5 text-[10.5px]';

  return (
    <span
      data-testid="cost-source-pill"
      data-source={value}
      title={isUnknown ? 'Pre-v0.6 row — source not tracked' : value}
      className={cn(
        'inline-flex items-center rounded-md border font-mono uppercase tracking-wide',
        sz, tone, className,
      )}
    >
      {label}
    </span>
  );
}

interface FilterProps {
  value: CostSource | 'all';
  onChange: (v: CostSource | 'all') => void;
  className?: string;
}

const FILTER_OPTIONS: { value: CostSource | 'all'; label: string }[] = [
  { value: 'all',      label: 'all'    },
  { value: 'api_pool', label: 'api'    },
  { value: 'max_sub',  label: 'max'    },
  { value: 'unknown',  label: '?'      },
];

export function CostSourceFilter({ value, onChange, className }: FilterProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Filter by cost source"
      className={cn(
        'inline-flex items-center rounded-lg border border-border bg-surface-2 p-0.5',
        'text-[11px] font-mono uppercase tracking-wide',
        className,
      )}
    >
      {FILTER_OPTIONS.map(opt => {
        const active = value === opt.value;
        return (
          <button
            key={opt.value}
            role="radio"
            aria-checked={active}
            data-testid={`cost-source-filter-${opt.value}`}
            onClick={() => onChange(opt.value)}
            className={cn(
              'px-2.5 h-6 rounded-md transition-colors',
              active ? 'bg-surface-3 text-text' : 'text-text-dim hover:text-text',
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
