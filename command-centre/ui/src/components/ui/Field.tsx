// Minimal form primitives — label + input/textarea/select, no external deps.
import { cn } from '@/lib/cn';
import {
  forwardRef, InputHTMLAttributes, ReactNode,
  SelectHTMLAttributes, TextareaHTMLAttributes,
} from 'react';

export function Label({ children, hint, className }: { children: ReactNode; hint?: ReactNode; className?: string }) {
  return (
    <label className={cn('flex flex-col gap-1.5 text-[12.5px]', className)}>
      <span className="flex items-center justify-between">
        <span className="kicker !text-text-dim">{children}</span>
        {hint && <span className="text-[11px] text-text-subtle">{hint}</span>}
      </span>
    </label>
  );
}

const BASE = 'bg-surface border border-border rounded-lg px-3 py-2 text-[13px] text-text ' +
             'placeholder:text-text-subtle focus:border-border-glow focus:outline-none';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input(props, ref) {
    return <input ref={ref} {...props} className={cn(BASE, 'h-9', props.className)} />;
  },
);
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea(props, ref) {
    return <textarea ref={ref} {...props} className={cn(BASE, 'leading-relaxed min-h-[96px]', props.className)} />;
  },
);
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select(props, ref) {
    return <select ref={ref} {...props} className={cn(BASE, 'h-9 pr-8', props.className)} />;
  },
);

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode }) {
  return (
    <label className="inline-flex items-center gap-2 text-[12.5px] cursor-pointer">
      <span className={cn(
        'w-9 h-5 rounded-full border border-border transition-colors relative',
        checked ? 'bg-accent-blue/50' : 'bg-surface-3',
      )}>
        <span className={cn(
          'absolute top-[2px] w-3.5 h-3.5 rounded-full bg-text transition-all',
          checked ? 'left-[18px]' : 'left-[2px]',
        )} />
      </span>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} className="sr-only" />
      <span>{label}</span>
    </label>
  );
}
