import { cn } from '@/lib/cn';
import { ReactNode } from 'react';

export type BadgeTone = 'neutral' | 'green' | 'amber' | 'red' | 'cyan' | 'purple' | 'blue';

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-surface-3 text-text-dim border-border',
  green:   'bg-status-green/10 text-status-green border-status-green/30',
  amber:   'bg-status-amber/10 text-status-amber border-status-amber/30',
  red:     'bg-status-red/10 text-status-red border-status-red/30',
  cyan:    'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/30',
  purple:  'bg-accent-purple/10 text-accent-purple border-accent-purple/30',
  blue:    'bg-accent-blue/10 text-accent-blue border-accent-blue/30',
};

export function Badge({ children, tone = 'neutral', className }: {
  children?: ReactNode; tone?: BadgeTone; className?: string;
}) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-mono uppercase tracking-wide',
      TONES[tone], className,
    )}>
      {children}
    </span>
  );
}
