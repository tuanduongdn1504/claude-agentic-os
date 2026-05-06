import { CollapsibleSection } from '@/components/ui/CollapsibleSection';
import { MCPPanel } from '@/components/panels/MCPPanel';
import { SkillsRegistry } from '@/components/panels/SkillsRegistry';
import { ContextHealthCard } from '@/components/panels/ContextHealthCard';
import { SkillCostCard } from '@/components/panels/SkillCostCard';

export default function SkillsPage() {
  return (
    <div className="space-y-6">
      <CollapsibleSection id="skills-mcp" title="MCP servers" subtitle="per-server + per-tool latency" defaultOpen>
        <MCPPanel />
      </CollapsibleSection>

      <CollapsibleSection id="skills-registry" title="Skills registry" subtitle="autonomy controls">
        <SkillsRegistry />
      </CollapsibleSection>

      <CollapsibleSection id="skills-context" title="Context health" subtitle="settings.json + CLAUDE.md">
        <ContextHealthCard />
      </CollapsibleSection>

      <CollapsibleSection id="skills-economics" title="Skill economics" subtitle="tokens + cost per skill">
        <SkillCostCard />
      </CollapsibleSection>
    </div>
  );
}
