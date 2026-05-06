// Minimal hover tooltip — 120ms delay, positioned below the trigger.
import { AnimatePresence, motion } from 'framer-motion';
import { ReactNode, useRef, useState } from 'react';

export function Tooltip({ content, children, delay = 120 }: {
  content: ReactNode; children: ReactNode; delay?: number;
}) {
  const [show, setShow] = useState(false);
  const timer = useRef<number | null>(null);
  const open = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setShow(true), delay);
  };
  const close = () => {
    if (timer.current) window.clearTimeout(timer.current);
    setShow(false);
  };
  return (
    <span className="relative inline-flex" onMouseEnter={open} onMouseLeave={close}
          onFocus={open} onBlur={close}>
      {children}
      <AnimatePresence>
        {show && (
          <motion.span
            initial={{ opacity: 0, y: -2 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -2 }}
            transition={{ duration: 0.12 }}
            role="tooltip"
            className="absolute top-full left-1/2 -translate-x-1/2 mt-2 px-2 py-1 whitespace-nowrap
                       bg-surface-3 border border-border rounded-md text-[11.5px] text-text shadow-lg z-[60]
                       pointer-events-none"
          >
            {content}
          </motion.span>
        )}
      </AnimatePresence>
    </span>
  );
}
