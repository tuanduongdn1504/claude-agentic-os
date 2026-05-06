import { cn } from '@/lib/cn';
import { ReactNode } from 'react';

type Tone = 'ok' | 'warn' | 'error' | 'idle' | 'info';

const DOT: Record<Tone, string> = {
  ok:    'bg-status-green shadow-[0_0_8px_rgba(16,185,129,0.55)]',
  warn:  'bg-status-amber shadow-[0_0_8px_rgba(245,158,11,0.55)]',
  error: 'bg-status-red shadow-[0_0_8px_rgba(239,68,68,0.6)]',
  idle:  'bg-text-subtle',
  info:  'bg-accent-blue shadow-[0_0_8px_rgba(77,124,255,0.55)]',
};

export function StatePill({ tone, children, className }: {
  tone: Tone; children?: ReactNode; className?: string;
}) {
  return (
    <span className={cn(
      'inline-flex items-center gap-2 px-2.5 py-1 rounded-md text-[12px]',
      'bg-surface-2 border border-border text-text-dim font-mono',
      className,
    )}>
      <span className={cn('w-1.5 h-1.5 rounded-full', DOT[tone])} />
      {children}
    </span>
  );
}
