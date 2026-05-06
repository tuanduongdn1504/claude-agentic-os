import { cn } from '@/lib/cn';
import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leftIcon?: ReactNode;
  rightIcon?: ReactNode;
}

const BASE =
  'inline-flex items-center justify-center gap-2 rounded-lg font-medium ' +
  'transition-all duration-150 whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed ' +
  'active:translate-y-px';

const VARIANTS: Record<Variant, string> = {
  primary:
    'text-white bg-gradient-hero hover:shadow-lift hover:-translate-y-[1px]',
  secondary:
    'bg-surface-2 text-text border border-border hover:border-border-glow hover:bg-surface-3',
  ghost:
    'text-text-dim hover:text-text hover:bg-surface-2',
  danger:
    'bg-status-red/15 text-status-red border border-status-red/35 hover:bg-status-red/25',
};

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-[13px]',
  md: 'h-10 px-4 text-sm',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = 'secondary', size = 'md', leftIcon, rightIcon, children, ...rest },
  ref,
) {
  return (
    <button ref={ref} className={cn(BASE, VARIANTS[variant], SIZES[size], className)} {...rest}>
      {leftIcon}
      {children}
      {rightIcon}
    </button>
  );
});
