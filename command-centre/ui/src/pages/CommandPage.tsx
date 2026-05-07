import { useState } from 'react';
import { SystemHealthStrip } from '@/components/panels/SystemHealthStrip';
import { KpiRow } from '@/components/panels/KpiRow';
import { AttentionBar } from '@/components/panels/AttentionBar';
import { CollapsibleSection } from '@/components/ui/CollapsibleSection';
import { TokenUsageCard } from '@/components/panels/TokenUsageCard';
import { CacheEfficiencyCard } from '@/components/panels/CacheEfficiencyCard';
import { SessionOutcomesCard } from '@/components/panels/SessionOutcomesCard';
import { ToolLatencyCard } from '@/components/panels/ToolLatencyCard';
import { HookActivityCard } from '@/components/panels/HookActivityCard';
import { ProjectBreakdownCard } from '@/components/panels/ProjectBreakdownCard';
import { AgentFanoutCard } from '@/components/panels/AgentFanoutCard';
import { EditAcceptanceCard } from '@/components/panels/EditAcceptanceCard';
import { ProductivityCard } from '@/components/panels/ProductivityCard';
import { PressurePanel } from '@/components/panels/PressurePanel';
import { LiveSessionsCard } from '@/components/panels/LiveSessionsCard';
import { TaskBoard } from '@/components/panels/TaskBoard';
import { TaskComposer } from '@/components/panels/TaskComposer';
import { SchedulesCard } from '@/components/panels/SchedulesCard';
import { DecisionsCard } from '@/components/panels/DecisionsCard';
import { InboxCard } from '@/components/panels/InboxCard';
import { HeatmapGrid } from '@/components/panels/HeatmapGrid';

export default function CommandPage() {
  const [composerOpen, setComposerOpen] = useState(false);

  // Wire the data-action="open-task-composer" buttons scattered across panels.
  // Delegate click listener at the page root.
  function onRootClick(e: React.MouseEvent<HTMLDivElement>) {
    const target = (e.target as HTMLElement).closest('[data-action="open-task-composer"]');
    if (target) setComposerOpen(true);
  }

  return (
    <div className="space-y-6" onClick={onRootClick}>
      <SystemHealthStrip />
      <AttentionBar />
      <KpiRow />

      <CollapsibleSection id="hitl-live" title="Live + HITL" subtitle="running sessions · decisions · inbox" defaultOpen>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 grid-match">
          <LiveSessionsCard />
          <DecisionsCard />
          <InboxCard />
        </div>
      </CollapsibleSection>

      <CollapsibleSection id="mission-control" title="Mission Control" subtitle="tasks + schedules" defaultOpen>
        <TaskBoard />
        <SchedulesCard />
      </CollapsibleSection>

      <CollapsibleSection id="usage" title="Usage" subtitle="tokens, cache, cost" defaultOpen>
        <TokenUsageCard />
      </CollapsibleSection>

      <CollapsibleSection id="observability-quality" title="Observability · quality" subtitle="cache + outcomes">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 grid-match">
          <CacheEfficiencyCard />
          <SessionOutcomesCard />
        </div>
      </CollapsibleSection>

      <CollapsibleSection id="observability-perf" title="Observability · performance" subtitle="tool + hook latency">
        <div className="space-y-4">
          <ToolLatencyCard />
          <HookActivityCard />
        </div>
      </CollapsibleSection>

      <CollapsibleSection id="observability-flow" title="Observability · flow" subtitle="projects · agents · edits">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 grid-match">
          <ProjectBreakdownCard />
          <AgentFanoutCard />
          <EditAcceptanceCard />
          <ProductivityCard />
        </div>
      </CollapsibleSection>

      <CollapsibleSection id="observability-rhythm" title="Observability · rhythm" subtitle="when you actually work">
        <HeatmapGrid />
      </CollapsibleSection>

      <CollapsibleSection id="system-pressure" title="System pressure" subtitle="retries · compactions · api_errors">
        <PressurePanel />
      </CollapsibleSection>

      <TaskComposer open={composerOpen} onClose={() => setComposerOpen(false)} />
    </div>
  );
}
