import { AlertTriangle, DollarSign, MessageSquare, Wrench } from 'lucide-react';
import { ReactNode } from 'react';
import { useSummary, useSummarySparklines } from '@/hooks/useQueries';
import { Card } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Sparkline } from '@/components/ui/Sparkline';
import { fmtCount, fmtUsd } from '@/lib/format';

type Tone = 'blue' | 'purple' | 'green' | 'amber';

const TONE_HEX: Record<Tone, string> = {
  blue:   '#4d7cff',
  purple: '#8b5cf6',
  green:  '#10b981',
  amber:  '#f59e0b',
};

function Tile({
  kicker, value, sub, icon, tone = 'blue', loading, spark,
}: {
  kicker: string;
  value: ReactNode;
  sub?: ReactNode;
  icon: ReactNode;
  tone?: Tone;
  loading?: boolean;
  spark?: number[];
}) {
  const toneBg = {
    blue:   'from-accent-blue/15 to-accent-blue/0',
    purple: 'from-accent-purple/15 to-accent-purple/0',
    green:  'from-status-green/15 to-status-green/0',
    amber:  'from-status-amber/15 to-status-amber/0',
  }[tone];
  const toneText = {
    blue: 'text-accent-blue', purple: 'text-accent-purple',
    green: 'text-status-green', amber: 'text-status-amber',
  }[tone];
  const sparkColor = TONE_HEX[tone];

  return (
    <Card className="relative overflow-hidden">
      <div className={`pointer-events-none absolute -top-12 -right-12 w-40 h-40 rounded-full blur-2xl bg-gradient-to-br ${toneBg}`} />
      <div className="px-5 py-4">
        <div className="flex items-start justify-between">
          <div className="kicker">{kicker}</div>
          <span className={`${toneText}`}>{icon}</span>
        </div>
        <div className="mt-3 flex items-end justify-between gap-3">
          <div className="text-[30px] font-semibold tracking-tight num leading-none">
            {loading ? <Skeleton className="h-[30px] w-24" /> : value}
          </div>
          {spark && spark.length > 0 && (
            <Sparkline
              data={spark}
              width={84}
              height={28}
              stroke={sparkColor}
              fill={`${sparkColor}33` /* 20% alpha */}
              className="opacity-90"
            />
          )}
        </div>
        {sub && <div className="mt-2 text-[12px] text-text-subtle font-mono">{sub}</div>}
      </div>
    </Card>
  );
}

export function KpiRow() {
  const { data, isLoading } = useSummary();
  const { data: spark } = useSummarySparklines();
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <Tile loading={isLoading} kicker="Sessions · today" icon={<MessageSquare size={16} />} tone="blue"
        value={fmtCount(data?.sessions_today)}
        spark={spark?.sessions}
        sub={`${fmtCount(data?.tools_today)} tool calls · last 24h trend →`} />
      <Tile loading={isLoading} kicker="Tokens · today" icon={<Wrench size={16} />} tone="purple"
        value={fmtCount(data?.effective_tokens_today)}
        spark={spark?.tokens}
        sub="input + output (excl. cache) · last 24h →" />
      <Tile loading={isLoading} kicker="Cost · today" icon={<DollarSign size={16} />} tone="green"
        value={fmtUsd(data?.cost_usd_today)}
        spark={spark?.cost_usd}
        sub="derived from model prices · last 24h →" />
      <Tile loading={isLoading} kicker="Errors · today" icon={<AlertTriangle size={16} />}
        tone={data && data.errors_today > 0 ? 'amber' : 'green'}
        value={fmtCount(data?.errors_today)}
        spark={spark?.errors}
        sub={data && data.errors_today > 0 ? 'check Attention bar' : 'all clear · last 24h →'} />
    </div>
  );
}
