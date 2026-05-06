// Right-side slide-out drawer. ESC to close, focus trap, aria-modal.
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import {
  KeyboardEvent, ReactNode, useCallback, useEffect, useRef,
} from 'react';
import { cn } from '@/lib/cn';

export function Sheet({
  open, onClose, title, children, widthClass = 'w-[460px]',
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  widthClass?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const prevFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    prevFocusRef.current = document.activeElement as HTMLElement | null;
    // Focus first focusable in panel.
    requestAnimationFrame(() => {
      const el = panelRef.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      (el ?? panelRef.current)?.focus();
    });
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    };
    window.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
      prevFocusRef.current?.focus?.();
    };
  }, [open, onClose]);

  // Trap focus within the panel (Tab / Shift+Tab).
  const onKeyDown = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'Tab' || !panelRef.current) return;
    const nodes = panelRef.current.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }, []);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/55 z-40" onClick={onClose}
          />
          <motion.div
            ref={panelRef}
            role="dialog" aria-modal
            initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 260, damping: 30 }}
            className={cn(
              'fixed top-0 right-0 bottom-0 z-50',
              'bg-surface border-l border-border shadow-2xl',
              'flex flex-col focus:outline-none',
              widthClass,
            )}
            tabIndex={-1}
            onKeyDown={onKeyDown}
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <div className="text-sm font-semibold">{title}</div>
              <button onClick={onClose} className="p-1 rounded-md text-text-dim hover:text-text hover:bg-surface-2"
                aria-label="Close">
                <X size={16} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto">{children}</div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
