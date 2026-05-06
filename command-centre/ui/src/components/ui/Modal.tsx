// Lightweight modal dialog for quick prompts (decision answer, etc.).
import { AnimatePresence, motion } from 'framer-motion';
import { X } from 'lucide-react';
import { ReactNode, useEffect } from 'react';
import { cn } from '@/lib/cn';

export function Modal({
  open, onClose, title, children, footer, widthClass = 'w-[min(520px,92vw)]',
}: {
  open: boolean; onClose: () => void;
  title?: ReactNode; children: ReactNode; footer?: ReactNode; widthClass?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/55 z-[60]" onClick={onClose} />
          <motion.div role="dialog" aria-modal
            initial={{ opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className={cn(
              'fixed left-1/2 top-[18vh] -translate-x-1/2 z-[61]',
              'bg-surface-2 border border-border-glow rounded-2xl shadow-2xl overflow-hidden',
              widthClass,
            )}>
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
              <div className="text-sm font-semibold">{title}</div>
              <button onClick={onClose} aria-label="Close"
                className="p-1 rounded-md text-text-dim hover:text-text hover:bg-surface-3"><X size={16} /></button>
            </div>
            <div className="px-5 py-4">{children}</div>
            {footer && <div className="px-5 py-3 border-t border-border flex items-center justify-end gap-2">{footer}</div>}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
