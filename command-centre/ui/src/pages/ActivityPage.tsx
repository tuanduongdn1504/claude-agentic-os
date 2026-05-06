import { CollapsibleSection } from '@/components/ui/CollapsibleSection';
import { SessionsTable } from '@/components/panels/SessionsTable';
import { UnifiedFailures } from '@/components/panels/UnifiedFailures';
import { OtelFirehose } from '@/components/panels/OtelFirehose';

export default function ActivityPage() {
  return (
    <div className="space-y-6">
      <CollapsibleSection id="activity-sessions" title="All sessions" subtitle="search + filter + timeline" defaultOpen>
        <SessionsTable />
      </CollapsibleSection>

      <CollapsibleSection id="activity-failures" title="Failures" subtitle="errors · rate-limits · api_errors">
        <UnifiedFailures />
      </CollapsibleSection>

      <CollapsibleSection id="activity-firehose" title="Telemetry firehose" subtitle="live OTEL events">
        <OtelFirehose />
      </CollapsibleSection>
    </div>
  );
}
