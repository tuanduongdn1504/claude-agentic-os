import { Send, Circle, AlertTriangle } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { useTelegramStatus } from '@/hooks/useQueries';
import { fmtAgeSeconds } from '@/lib/format';
import { cn } from '@/lib/cn';

function ageOf(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso.replace(' ', 'T') + (iso.includes('T') ? '' : 'Z'));
  if (isNaN(t)) return null;
  return (Date.now() - t) / 1000;
}

export function TelegramBridgeStatus() {
  const { data, isLoading } = useTelegramStatus();

  if (isLoading) {
    return (
      <div className="text-[12px] text-text-subtle px-3 py-2 border border-border/40 rounded-lg bg-surface-2/30">
        checking telegram bridge…
      </div>
    );
  }
  if (!data) return null;

  if (!data.configured) {
    return (
      <div className="flex items-center gap-2 text-[12px] text-text-subtle px-3 py-2 border border-border/40 rounded-lg bg-surface-2/30">
        <Send size={12} className="opacity-50" />
        <span>Telegram bridge not configured · run <code className="font-mono text-text-dim">cc setup telegram</code></span>
      </div>
    );
  }

  const lastAge = ageOf(data.last_outbound_at);
  const errorAge = ageOf(data.last_error_at);
  const recentError = data.last_error && errorAge != null && errorAge < 3600;

  let tone: 'green' | 'amber' | 'red';
  let dotColor: string;
  let label: string;
  if (!data.alive) {
    tone = 'red'; dotColor = 'text-status-red'; label = 'down';
  } else if (recentError) {
    tone = 'amber'; dotColor = 'text-status-amber'; label = 'errors';
  } else {
    tone = 'green'; dotColor = 'text-status-green'; label = 'live';
  }

  const byType = data.notified_24h_by_type;
  const breakdown = Object.entries(byType)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${k}${n > 1 ? 's' : ''}`)
    .join(' · ') || 'no pushes';

  return (
    <div className={cn(
      'flex items-center gap-3 text-[12px] px-3 py-2 rounded-lg border',
      tone === 'green' && 'border-border/40 bg-surface-2/30',
      tone === 'amber' && 'border-status-amber/30 bg-status-amber/5',
      tone === 'red'   && 'border-status-red/30 bg-status-red/5',
    )}>
      <div className="flex items-center gap-2">
        <Send size={12} className="text-text-dim" />
        <Circle size={8} className={cn('fill-current', dotColor)} />
        <span className="font-medium text-text">Telegram bridge</span>
        <Badge tone={tone}>{label}</Badge>
      </div>

      <div className="flex items-center gap-3 text-text-subtle font-mono ml-auto">
        <span title="Last outbound notification">
          last push {lastAge != null ? fmtAgeSeconds(lastAge) : 'never'}
        </span>
        <span className="text-text-subtle">·</span>
        <span title="Notifications in last 24h">24h: {breakdown}</span>
        {data.pid != null && (
          <>
            <span className="text-text-subtle">·</span>
            <span title="Bridge PID">pid {data.pid}</span>
          </>
        )}
      </div>

      {recentError && data.last_error && (
        <div className="basis-full flex items-start gap-2 text-status-amber font-mono mt-1.5 text-[11px]">
          <AlertTriangle size={11} className="flex-shrink-0 mt-0.5" />
          <span className="truncate" title={data.last_error}>{data.last_error}</span>
        </div>
      )}
    </div>
  );
}
