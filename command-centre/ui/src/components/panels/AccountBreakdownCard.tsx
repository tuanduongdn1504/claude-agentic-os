// Multi-account split for today's spend. Driven by /api/summary.by_account,
// which is populated by sync_sessions tagging each session with an account_id
// from the entrypoint proxy in ~/.command-centre/data/accounts.json (and from
// the SessionStart hook hints once two+ accounts have been observed).
//
// Renders one row per configured account. Sums to the same totals as the
// KpiRow above — if they ever disagree, the proxy mapping needs review.

import { Users } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { useSummary } from '@/hooks/useQueries';
import { fmtCount, fmtUsd } from '@/lib/format';
import type { AccountSummaryRow } from '@/lib/types';

export function AccountBreakdownCard() {
  const { data, isLoading } = useSummary();
  const accounts = data?.by_account;
  const entries: [string, AccountSummaryRow][] = accounts
    ? Object.entries(accounts).sort(([a], [b]) => a.localeCompare(b))
    : [];

  const totalCost = entries.reduce((s, [, v]) => s + (v.cost_usd || 0), 0);

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Today by account</Kicker>
          <CardTitle className="flex items-center gap-2">
            <Users size={14} className="text-accent-purple" />
            Account split
          </CardTitle>
          <CardDescription>
            Entrypoint-mapped (Desktop / VS Code / CLI). Edit{' '}
            <code className="font-mono text-[11px]">~/.command-centre/data/accounts.json</code>{' '}
            to rename labels or wire OAuth UUIDs from the SessionStart hook.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : entries.length === 0 ? (
          <div className="text-[12px] text-text-subtle py-2">
            No accounts configured. Run a sync and reload.
          </div>
        ) : (
          <div className="space-y-2">
            {entries.map(([id, row]) => {
              const share = totalCost > 0 ? (row.cost_usd / totalCost) * 100 : 0;
              return (
                <div
                  key={id}
                  className="bg-surface-2/40 border border-border rounded-lg px-4 py-3"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-mono text-[13px] font-semibold truncate">
                        {row.label}
                      </div>
                      <div className="font-mono text-[10.5px] text-text-subtle">{id}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-[15px] num">{fmtUsd(row.cost_usd)}</div>
                      <div className="font-mono text-[10.5px] text-text-subtle">
                        {totalCost > 0 ? `${share.toFixed(1)}% of today` : '—'}
                      </div>
                    </div>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-[12px]">
                    <KV label="sessions" value={fmtCount(row.sessions)} />
                    <KV label="tokens" value={fmtCount(row.tokens)} />
                  </div>
                  {totalCost > 0 && (
                    <div className="mt-2 h-1 bg-surface/60 rounded overflow-hidden">
                      <div
                        className="h-full bg-accent-purple/70"
                        style={{ width: `${share}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface/60 rounded px-2 py-1.5 flex items-center justify-between">
      <span className="kicker">{label}</span>
      <span className="font-mono text-text num">{value}</span>
    </div>
  );
}
