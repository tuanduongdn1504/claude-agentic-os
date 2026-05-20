// ⌘K palette: fuzzy-search pages + "Queue a task" + "Trigger sync".
import { useNavigate } from '@tanstack/react-router';
import { AnimatePresence, motion } from 'framer-motion';
import { Activity, Gauge, Layers, RefreshCw, Search, Zap } from 'lucide-react';
import {
  KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState,
} from 'react';
import { cn } from '@/lib/cn';
import { useManualSync } from '@/hooks/useQueries';

type Action = {
  id: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
  keywords?: string;
};

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  const sync = useManualSync();

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(v => !v);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const close = () => setOpen(false);

  const actions: Action[] = useMemo(() => ([
    // v0.6.6: `search: (prev) => prev` preserves `?embed=1` (and any
    // future root-level search params) across navigation. Palette is
    // hidden in embed mode today, but keeping the idiom uniform means
    // any future panel-internal navigation already matches the spec.
    {
      id: 'nav-command', label: 'Go to Command', icon: <Gauge size={16} />,
      keywords: 'home dashboard index',
      run: () => { nav({ to: '/', search: (prev) => prev }); close(); },
    },
    {
      id: 'nav-activity', label: 'Go to Activity', icon: <Activity size={16} />,
      keywords: 'log firehose heatmap',
      run: () => { nav({ to: '/activity', search: (prev) => prev }); close(); },
    },
    {
      id: 'nav-skills', label: 'Go to Skills & MCP', icon: <Layers size={16} />,
      keywords: 'mcp servers tools',
      run: () => { nav({ to: '/skills', search: (prev) => prev }); close(); },
    },
    {
      id: 'action-sync', label: 'Sync now', icon: <RefreshCw size={16} />,
      keywords: 'reload refresh jsonl', hint: 'scan ~/.claude/projects',
      run: () => { sync.mutate(); close(); },
    },
    {
      id: 'action-queue', label: 'Queue a task', icon: <Zap size={16} />,
      keywords: 'new task dispatcher', hint: 'opens composer on Command',
      run: () => {
        nav({ to: '/', search: (prev) => prev });
        // Composer opening is wired up later on index.tsx via a URL flag.
        setTimeout(() => {
          const btn = document.querySelector<HTMLButtonElement>('[data-action="open-task-composer"]');
          btn?.click();
        }, 60);
        close();
      },
    },
  ]), [nav, sync]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    return actions.filter(a =>
      a.label.toLowerCase().includes(q) ||
      (a.keywords && a.keywords.toLowerCase().includes(q)),
    );
  }, [query, actions]);

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      const a = filtered[active];
      if (a) a.run();
    }
  };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="fixed inset-0 bg-black/60 z-[70]"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={close} />
          <motion.div
            role="dialog" aria-modal aria-label="Command palette"
            initial={{ opacity: 0, y: -8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="fixed left-1/2 top-[18vh] -translate-x-1/2 w-[min(620px,92vw)] z-[71]
                       bg-surface-2 border border-border-glow rounded-2xl shadow-2xl overflow-hidden"
          >
            <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
              <Search size={16} className="text-text-dim" />
              <input
                ref={inputRef}
                value={query}
                onChange={e => { setQuery(e.target.value); setActive(0); }}
                onKeyDown={onInputKey}
                placeholder="Jump to, run action…"
                className="bg-transparent outline-none text-sm flex-1 placeholder:text-text-subtle"
              />
              <kbd className="text-[10.5px] font-mono text-text-subtle px-1.5 py-0.5 rounded bg-surface-3 border border-border">ESC</kbd>
            </div>
            <ul className="max-h-[300px] overflow-y-auto py-1">
              {filtered.length === 0 && (
                <li className="px-4 py-8 text-center text-sm text-text-dim">no matches</li>
              )}
              {filtered.map((a, i) => (
                <li key={a.id}>
                  <button
                    onMouseEnter={() => setActive(i)}
                    onClick={() => a.run()}
                    className={cn(
                      'w-full flex items-center gap-3 px-4 py-2 text-left text-[13.5px]',
                      i === active
                        ? 'bg-surface-3 text-text'
                        : 'text-text-dim hover:bg-surface-3/60',
                    )}
                  >
                    <span className="text-text-dim">{a.icon}</span>
                    <span className="flex-1">{a.label}</span>
                    {a.hint && <span className="text-[11.5px] text-text-subtle">{a.hint}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
