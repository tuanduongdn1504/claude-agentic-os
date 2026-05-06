import { cn } from '@/lib/cn';
import { ReactNode } from 'react';

export function Card({ className, children, as: As = 'div' }: {
  className?: string; children?: ReactNode; as?: 'div' | 'section' | 'article';
}) {
  return (
    <As
      className={cn(
        'bg-surface/80 border border-border rounded-2xl backdrop-blur-sm',
        'shadow-[0_1px_0_0_rgba(255,255,255,0.02)_inset]',
        'animate-fade-in',
        className,
      )}
    >
      {children}
    </As>
  );
}

export function CardHeader({ className, children }: { className?: string; children?: ReactNode }) {
  return <div className={cn('flex items-start justify-between gap-4 px-6 pt-5 pb-3', className)}>{children}</div>;
}

export function CardTitle({ className, children }: { className?: string; children?: ReactNode }) {
  return <h3 className={cn('text-[15px] font-semibold tracking-tight text-text', className)}>{children}</h3>;
}

export function CardDescription({ className, children }: { className?: string; children?: ReactNode }) {
  return <p className={cn('text-[13px] text-text-dim leading-relaxed', className)}>{children}</p>;
}

export function CardContent({ className, children }: { className?: string; children?: ReactNode }) {
  return <div className={cn('px-6 pb-5', className)}>{children}</div>;
}

export function CardFooter({ className, children }: { className?: string; children?: ReactNode }) {
  return <div className={cn('px-6 pb-5 pt-2 flex items-center justify-between', className)}>{children}</div>;
}

export function Kicker({ children, className }: { children?: ReactNode; className?: string }) {
  return <div className={cn('kicker mb-1', className)}>{children}</div>;
}
