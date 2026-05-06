import { AnimatePresence, motion } from 'framer-motion';
import { ChevronRight } from 'lucide-react';
import { ReactNode, useCallback, useEffect, useId, useState } from 'react';
import { cn } from '@/lib/cn';

const STORAGE_PREFIX = 'cc:section:';

function useStickyOpen(id: string, initial: boolean): [boolean, (b: boolean) => void] {
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + id);
      if (raw === 'open') return true;
      if (raw === 'closed') return false;
    } catch { /* storage blocked — non-fatal */ }
    return initial;
  });
  const set = useCallback((b: boolean) => {
    setOpen(b);
    try { localStorage.setItem(STORAGE_PREFIX + id, b ? 'open' : 'closed'); } catch { /* */ }
  }, [id]);
  return [open, set];
}

export function CollapsibleSection({
  id, title, subtitle, summary, defaultOpen = true, children, right, className,
}: {
  id: string;
  title: ReactNode;
  subtitle?: ReactNode;
  summary?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  right?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useStickyOpen(id, defaultOpen);
  const contentId = useId();
  useEffect(() => { /* nothing, keeps linter quiet about effect import */ }, [open]);

  return (
    <section className={cn('space-y-4', className)} aria-labelledby={`${contentId}-h`}>
      <header className="flex items-center gap-3">
        <button
          onClick={() => setOpen(!open)}
          className={cn(
            'flex items-center gap-2 py-1 pr-2 -ml-1 rounded-md',
            'text-text-dim hover:text-text transition-colors',
          )}
          aria-expanded={open}
          aria-controls={contentId}
        >
          <motion.span
            animate={{ rotate: open ? 90 : 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            className="inline-flex"
          >
            <ChevronRight size={16} />
          </motion.span>
          <span id={`${contentId}-h`} className="kicker !text-text-dim">
            {title}
          </span>
        </button>
        {subtitle && <span className="text-[12px] text-text-subtle">{subtitle}</span>}
        {!open && summary && (
          <span className="ml-2 text-[12px] text-text-subtle font-mono">{summary}</span>
        )}
        {right && <div className="ml-auto">{right}</div>}
      </header>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={contentId}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
            style={{ overflow: 'hidden' }}
          >
            <div className="space-y-4">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
