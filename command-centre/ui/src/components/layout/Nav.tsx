import { Link, useMatchRoute } from '@tanstack/react-router';
import { Activity, Gauge, Layers, History, Inbox } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { cn } from '@/lib/cn';

const ITEMS = [
  { to: '/',          label: 'Command',  icon: Gauge },
  { to: '/activity',  label: 'Activity', icon: Activity },
  { to: '/sessions',  label: 'Sessions', icon: History },
  { to: '/decisions', label: 'Decisions', icon: Inbox },
  { to: '/skills',    label: 'Skills & MCP', icon: Layers },
] as const;

export function Nav() {
  const match = useMatchRoute();
  const { data: pendingDecisions } = useQuery({
    queryKey: ['decisions', 'pending'],
    queryFn: () => api.listDecisions('pending'),
    refetchInterval: 5_000,
  });
  const pendingCount = pendingDecisions?.items.length ?? 0;

  return (
    <nav className="flex items-center gap-1 text-[13px]">
      {ITEMS.map(({ to, label, icon: Icon }) => {
        const isActive = !!match({ to, fuzzy: false });
        const showBadge = to === '/decisions' && pendingCount > 0;
        return (
          <Link
            key={to}
            to={to}
            // v0.6.6: preserve `?embed=1` (and any future root-level
            // search params) across in-app navigation so the operator
            // stays in embed mode while clicking between pages.
            search={(prev) => prev}
            className={cn(
              'inline-flex items-center gap-2 px-3 h-9 rounded-lg transition-colors',
              isActive
                ? 'bg-surface-2 text-text border border-border'
                : 'text-text-dim hover:text-text hover:bg-surface-2/60 border border-transparent',
            )}
          >
            <Icon size={14} />
            {label}
            {showBadge && (
              <span className="num inline-flex items-center justify-center min-w-[18px] h-[18px] px-1.5 rounded-full text-[10px] font-medium bg-status-amber/20 text-status-amber border border-status-amber/30">
                {pendingCount}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
