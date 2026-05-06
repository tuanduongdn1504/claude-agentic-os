// settings.json + CLAUDE.md health (size, MCP count, hook count, rule count).
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Kicker } from '@/components/ui/Card';
import { Skeleton } from '@/components/ui/Skeleton';
import { Badge } from '@/components/ui/Badge';
import { useContextHealth } from '@/hooks/useQueries';
import { fmtBytes } from '@/lib/format';
import type { ContextFileStats } from '@/lib/types';

export function ContextHealthCard() {
  const { data, isLoading } = useContextHealth();

  return (
    <Card>
      <CardHeader>
        <div>
          <Kicker>Context</Kicker>
          <CardTitle>settings.json + CLAUDE.md</CardTitle>
          <CardDescription>Local files only — no LLM scoring. Pure file stats.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" />
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <FileBlock title="settings.json" stats={data?.settings} kind="settings" />
            <FileBlock title="CLAUDE.md" stats={data?.claude_md} kind="claude" />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function FileBlock({
  title, stats, kind,
}: { title: string; stats?: ContextFileStats; kind: 'settings' | 'claude' }) {
  if (!stats) return null;
  if (!stats.exists) {
    return (
      <div className="bg-surface-2/40 border border-dashed border-border rounded-lg px-4 py-6 text-center">
        <div className="font-mono text-[13px] font-semibold mb-1">{title}</div>
        <div className="text-[12px] text-text-subtle">
          {stats.error ?? 'missing at expected path'}
        </div>
      </div>
    );
  }
  return (
    <div className="bg-surface-2/40 border border-border rounded-lg p-4">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="font-mono text-[13px] font-semibold">{title}</div>
          {stats.path && (
            <div className="font-mono text-[10.5px] text-text-subtle truncate max-w-[260px]" title={stats.path}>
              {stats.path.replace(/^\/Users\/[^/]+/, '~')}
            </div>
          )}
        </div>
        <Badge tone="neutral">{fmtBytes(stats.bytes)}</Badge>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[12px]">
        {kind === 'settings' ? (
          <>
            <KV label="env keys" value={stats.env_keys} />
            <KV label="MCP servers" value={stats.mcp_server_count} />
            <KV label="hooks" value={stats.hook_count} />
            <KV label="lines" value={stats.lines} />
          </>
        ) : (
          <>
            <KV label="lines" value={stats.lines} />
            <KV label="rules" value={stats.rule_count ?? 0} />
          </>
        )}
      </div>
    </div>
  );
}

function KV({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <div className="bg-surface/60 rounded px-2 py-1.5 flex items-center justify-between">
      <span className="kicker">{label}</span>
      <span className="font-mono text-text num">{value == null ? '—' : value}</span>
    </div>
  );
}
