import { useSystemHealth } from '@/hooks/useQueries';
import { StatePill } from '@/components/ui/StatePill';
import { Card } from '@/components/ui/Card';
import { fmtAgeSeconds, fmtBytes } from '@/lib/format';

type Tone = 'ok' | 'warn' | 'error' | 'idle' | 'info';

function ageTone(age: number | null | undefined, okMax: number, warnMax: number): Tone {
  if (age == null) return 'idle';
  if (age <= okMax) return 'ok';
  if (age <= warnMax) return 'warn';
  return 'error';
}

export function SystemHealthStrip() {
  const { data: h, isLoading, isError } = useSystemHealth();

  return (
    <Card className="px-5 py-3 flex flex-wrap items-center gap-2">
      {isLoading && <span className="text-text-dim text-[13px]">loading system health…</span>}
      {isError && <StatePill tone="error">server unreachable</StatePill>}
      {h && (
        <>
          <StatePill tone={h.ok ? 'ok' : 'error'}>
            uptime {fmtAgeSeconds(h.uptime_s)}
          </StatePill>
          <StatePill tone={h.mem_rss_mb < 300 ? 'ok' : h.mem_rss_mb < 600 ? 'warn' : 'error'}>
            mem {h.mem_rss_mb.toFixed(1)} MB
          </StatePill>
          <StatePill tone={ageTone(h.last_otel_event_age_s, 120, 600)}>
            otel {fmtAgeSeconds(h.last_otel_event_age_s)}
          </StatePill>
          <StatePill tone={ageTone(h.sync_loop_heartbeat_age_s, 180, 600)}>
            sync {fmtAgeSeconds(h.sync_loop_heartbeat_age_s)}
          </StatePill>
          <StatePill tone={ageTone(h.daemon_last_tick_age_s, 180, 600)}>
            daemon {fmtAgeSeconds(h.daemon_last_tick_age_s)}
          </StatePill>
          <StatePill tone={ageTone(h.notifier_last_tick_age_s, 90, 300)}>
            notifier {fmtAgeSeconds(h.notifier_last_tick_age_s)}
          </StatePill>
          <span className="ml-auto font-mono text-[11.5px] text-text-subtle">
            db {fmtBytes(h.db_size_bytes)} · tz {h.tz}
          </span>
        </>
      )}
    </Card>
  );
}
