// MCP servers table with per-server tool drill-down.
import { Fragment, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useMcpServers, useMcpTools } from '@/hooks/useQueries';
import { RangePicker } from './TokenUsageCard';
import type { Range } from '@/lib/api';
import { fmtCount, fmtMs, fmtPct } from '@/lib/format';

export function MCPPanel() {
  const [range, setRange] = useState<Range>('7d');
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, isLoading } = useMcpServers(range);
  const items = data?.items ?? [];

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>MCP servers</Kicker>
          <CardTitle>Per-server + tool drill-down</CardTitle>
          <CardDescription>Calls, errors, p50/p95/max. Click a row to expand tools.</CardDescription>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="text-[13px] text-text-subtle text-center py-6">no MCP calls in this range</div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full text-[12.5px]">
              <thead className="kicker bg-surface-2/40">
                <tr className="text-left">
                  <th className="py-2 px-3 font-normal"></th>
                  <th className="py-2 px-3 font-normal">server</th>
                  <th className="py-2 px-3 font-normal text-right">tools</th>
                  <th className="py-2 px-3 font-normal text-right">calls</th>
                  <th className="py-2 px-3 font-normal text-right">err rate</th>
                  <th className="py-2 px-3 font-normal text-right">p50</th>
                  <th className="py-2 px-3 font-normal text-right">p95</th>
                  <th className="py-2 px-3 font-normal text-right">max</th>
                </tr>
              </thead>
              <tbody>
                {items.map(s => {
                  const open = expanded === s.server;
                  return (
                    <Fragment key={s.server}>
                      <tr
                        className="border-t border-border hover:bg-surface-2/40 cursor-pointer"
                        onClick={() => setExpanded(open ? null : s.server)}
                      >
                        <td className="py-2 px-3 text-text-dim">
                          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                        </td>
                        <td className="py-2 px-3 font-mono">{s.server}</td>
                        <td className="py-2 px-3 text-right num">{s.tool_count}</td>
                        <td className="py-2 px-3 text-right num">{fmtCount(s.calls)}</td>
                        <td className="py-2 px-3 text-right">
                          <Badge tone={s.error_rate > 0.05 ? 'red' : s.error_rate > 0.01 ? 'amber' : 'green'}>
                            {fmtPct(s.error_rate)}
                          </Badge>
                        </td>
                        <td className="py-2 px-3 text-right num">{fmtMs(s.p50_ms)}</td>
                        <td className="py-2 px-3 text-right num">{fmtMs(s.p95_ms)}</td>
                        <td className="py-2 px-3 text-right num">{fmtMs(s.max_ms)}</td>
                      </tr>
                      {open && (
                        <tr>
                          <td colSpan={8} className="p-0 bg-surface/40">
                            <MCPToolsRow server={s.server} range={range} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MCPToolsRow({ server, range }: { server: string; range: Range }) {
  const { data, isLoading } = useMcpTools(server, range);
  const items = data?.items ?? [];
  return (
    <div className="px-8 py-3 border-t border-border">
      {isLoading ? (
        <Skeleton className="h-10 w-full" />
      ) : items.length === 0 ? (
        <div className="text-[12px] text-text-subtle py-2">no tool calls recorded</div>
      ) : (
        <table className="w-full text-[11.5px]">
          <thead className="kicker">
            <tr className="text-left">
              <th className="py-1 font-normal">tool</th>
              <th className="py-1 font-normal text-right">calls</th>
              <th className="py-1 font-normal text-right">err rate</th>
              <th className="py-1 font-normal text-right">p50</th>
              <th className="py-1 font-normal text-right">p95</th>
              <th className="py-1 font-normal text-right">max</th>
            </tr>
          </thead>
          <tbody>
            {items.map(t => (
              <tr key={t.tool} className="border-t border-border/50">
                <td className="py-1 font-mono">{t.tool}</td>
                <td className="py-1 text-right num">{fmtCount(t.calls)}</td>
                <td className="py-1 text-right num">{fmtPct(t.error_rate)}</td>
                <td className="py-1 text-right num">{fmtMs(t.p50_ms)}</td>
                <td className="py-1 text-right num">{fmtMs(t.p95_ms)}</td>
                <td className="py-1 text-right num">{fmtMs(t.max_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
