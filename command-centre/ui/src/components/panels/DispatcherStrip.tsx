// One-line strip showing the dispatcher hardening state — slots used,
// daily cost vs cap, risk-gate status. Sits above the TaskBoard.
import { useDispatcherState } from '@/hooks/useQueries';
import { StatePill } from '@/components/ui/StatePill';
import { fmtUsd } from '@/lib/format';
import { Shield, ShieldAlert, ShieldOff } from 'lucide-react';

export function DispatcherStrip() {
  const { data, isLoading } = useDispatcherState();

  if (isLoading || !data) {
    return (
      <div className="flex items-center gap-3 px-4 py-2 bg-surface-2/40 border border-border rounded-lg">
        <span className="text-[12px] text-text-subtle font-mono">loading dispatcher state…</span>
      </div>
    );
  }

  const slotsTone = data.back_pressure ? 'warn' : data.running > 0 ? 'info' : 'idle';
  const costTone =
    data.cost_capped ? 'error'
    : data.daily_cost_cap_usd && data.today_cost_usd / data.daily_cost_cap_usd > 0.8 ? 'warn'
    : 'idle';
  const riskTone = data.hard_risk_gate
    ? data.risk_gated_today > 0 ? 'warn' : 'ok'
    : 'idle';

  return (
    <div className="flex items-center flex-wrap gap-2 px-3 py-2 bg-surface-2/40 border border-border rounded-lg text-[12px]">
      <StatePill tone={slotsTone}>
        slots <span className="font-mono">{data.running}</span>/<span className="font-mono">{data.max_concurrent}</span>
        {data.back_pressure && ' · saturated'}
      </StatePill>

      <StatePill tone={costTone}>
        {data.daily_cost_cap_usd != null ? (
          <>
            today {fmtUsd(data.today_cost_usd)} / cap {fmtUsd(data.daily_cost_cap_usd)}
            {data.cost_capped && ' · capped'}
          </>
        ) : (
          <>today {fmtUsd(data.today_cost_usd)} · no cap</>
        )}
      </StatePill>

      <StatePill tone={riskTone} className="!gap-1.5">
        {data.hard_risk_gate
          ? <Shield size={11} className="text-status-green" />
          : <ShieldOff size={11} className="text-text-subtle" />}
        {data.hard_risk_gate ? 'risk gate on' : 'risk gate off'}
        {data.risk_gated_today > 0 && (
          <>
            <ShieldAlert size={11} className="text-status-amber ml-1" />
            <span className="font-mono">{data.risk_gated_today}</span> gated today
          </>
        )}
      </StatePill>

      <span className="ml-auto text-[11px] text-text-subtle font-mono">
        env: <span className="text-text-dim">MISSION_CONTROL_*</span>
      </span>
    </div>
  );
}
