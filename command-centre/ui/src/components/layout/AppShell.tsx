import { Outlet, useSearch } from '@tanstack/react-router';
import { Command, Hash } from 'lucide-react';
import { CommandPalette } from './CommandPalette';
import { Nav } from './Nav';
import { useSystemHealth } from '@/hooks/useQueries';
import { StatePill } from '@/components/ui/StatePill';
import { fmtAgeSeconds } from '@/lib/format';
import { EmergencyStopBanner } from '@/components/panels/EmergencyStopBanner';

export function AppShell() {
  const { data: health } = useSystemHealth();
  // v0.6.6: Obsidian embed mode strips dashboard chrome so the operator
  // sees only the data view inside the web-viewer iframe. Nav + Header +
  // EmergencyStopBanner + CommandPalette hidden; AttentionBar (rendered
  // inside pages) survives.
  const search = useSearch({ from: '__root__' });
  const isEmbedded = search.embed === '1' || search.embed === 'true';

  if (isEmbedded) {
    return (
      <div className="app-shell embedded min-h-screen flex flex-col">
        <main className="main embedded flex-1 w-full">
          <Outlet />
        </main>
      </div>
    );
  }

  return (
    <div className="app-shell min-h-screen flex flex-col">
      <header className="sticky top-0 z-30 backdrop-blur-xl bg-bg/70 border-b border-border">
        <div className="max-w-[1280px] mx-auto px-6 h-14 flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <span className="w-7 h-7 rounded-lg bg-gradient-hero grid place-items-center text-white font-mono text-sm shadow-lift">
              <Command size={14} />
            </span>
            <div className="flex flex-col leading-tight">
              <span className="font-semibold tracking-tight text-[13.5px]">Command Centre</span>
              <span
                className="font-mono text-[10.5px] text-text-subtle flex items-center gap-1"
                title={health?.tz ? `server tz: ${health.tz}` : undefined}
              >
                <Hash size={10} /> GMT+7 · up {health ? fmtAgeSeconds(health.uptime_s) : '…'}
              </span>
            </div>
          </div>
          <Nav />
          <div className="ml-auto flex items-center gap-2">
            <StatePill tone={health?.ok ? 'ok' : 'error'}>
              {health?.ok ? 'server ok' : 'server unreachable'}
            </StatePill>
            <button
              onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true }))}
              className="inline-flex items-center gap-2 px-2.5 h-8 rounded-md bg-surface-2 border border-border
                         text-text-dim hover:text-text hover:border-border-glow text-[12px]"
              aria-label="Open command palette"
            >
              <Command size={12} />
              <span className="font-mono tracking-wide">⌘K</span>
            </button>
          </div>
        </div>
      </header>
      <main className="main flex-1 max-w-[1280px] w-full mx-auto px-6 py-8 space-y-6">
        <EmergencyStopBanner />
        <Outlet />
      </main>
      <footer className="max-w-[1280px] mx-auto w-full px-6 py-8 text-[11.5px] text-text-subtle font-mono flex items-center gap-4 opacity-80">
        <span>local · 127.0.0.1:8765</span>
        <span>·</span>
        <span>no cloud · no account · no outbound telemetry</span>
      </footer>
      <CommandPalette />
    </div>
  );
}
