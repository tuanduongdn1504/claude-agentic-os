import { Link, useMatchRoute } from '@tanstack/react-router';
import { Activity, Gauge, Layers } from 'lucide-react';
import { cn } from '@/lib/cn';

const ITEMS = [
  { to: '/',          label: 'Command',  icon: Gauge },
  { to: '/activity',  label: 'Activity', icon: Activity },
  { to: '/skills',    label: 'Skills & MCP', icon: Layers },
] as const;

export function Nav() {
  const match = useMatchRoute();
  return (
    <nav className="flex items-center gap-1 text-[13px]">
      {ITEMS.map(({ to, label, icon: Icon }) => {
        const isActive = !!match({ to, fuzzy: false });
        return (
          <Link
            key={to}
            to={to}
            className={cn(
              'inline-flex items-center gap-2 px-3 h-9 rounded-lg transition-colors',
              isActive
                ? 'bg-surface-2 text-text border border-border'
                : 'text-text-dim hover:text-text hover:bg-surface-2/60 border border-transparent',
            )}
          >
            <Icon size={14} />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
