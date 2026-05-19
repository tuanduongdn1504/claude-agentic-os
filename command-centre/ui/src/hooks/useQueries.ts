// Every React Query hook in one place so pollers are visible + tunable.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as api from '@/lib/api';
import type { Range } from '@/lib/api';
import type { SkillPreset } from '@/lib/types';

const POLL_5S = 5_000;
const POLL_10S = 10_000;
const POLL_30S = 30_000;

// -- Top-level --
export const useSummary = () =>
  useQuery({ queryKey: ['summary'], queryFn: api.getSummary, refetchInterval: POLL_30S });
export const useSystemHealth = () =>
  useQuery({ queryKey: ['system-health'], queryFn: api.getSystemHealth, refetchInterval: POLL_10S });
export const useAttention = () =>
  useQuery({ queryKey: ['attention'], queryFn: api.getAttention, refetchInterval: POLL_10S });
export const useSystemPressure = () =>
  useQuery({ queryKey: ['system-pressure'], queryFn: api.getSystemPressure, refetchInterval: POLL_30S });
export const useDispatcherState = () =>
  useQuery({ queryKey: ['dispatcher-state'], queryFn: api.getDispatcherState, refetchInterval: POLL_10S });
export const useTelegramStatus = () =>
  useQuery({ queryKey: ['telegram-status'], queryFn: api.getTelegramStatus, refetchInterval: POLL_30S });

// -- Observability --
export const useUsageTokens = (range: Range) =>
  useQuery({ queryKey: ['usage-tokens', range], queryFn: () => api.getUsageTokens(range), refetchInterval: POLL_30S });
export const useUsageCache = (range: Range) =>
  useQuery({ queryKey: ['usage-cache', range], queryFn: () => api.getUsageCache(range), refetchInterval: POLL_30S });
export const useSessionOutcomes = (range: Range) =>
  useQuery({ queryKey: ['session-outcomes', range], queryFn: () => api.getSessionOutcomes(range), refetchInterval: POLL_30S });
export const useToolLatency = (range: Range) =>
  useQuery({ queryKey: ['tool-latency', range], queryFn: () => api.getToolLatency(range), refetchInterval: POLL_30S });
export const useHookActivity = (range: Range) =>
  useQuery({ queryKey: ['hook-activity', range], queryFn: () => api.getHookActivity(range), refetchInterval: POLL_30S });
export const useProjectBreakdown = (range: Range) =>
  useQuery({ queryKey: ['project-breakdown', range], queryFn: () => api.getProjectBreakdown(range), refetchInterval: POLL_30S });
export const useAgentFanout = (range: Range) =>
  useQuery({ queryKey: ['agent-fanout', range], queryFn: () => api.getAgentFanout(range), refetchInterval: POLL_30S });
export const useEditDecisions = (range: Range) =>
  useQuery({ queryKey: ['edit-decisions', range], queryFn: () => api.getEditDecisions(range), refetchInterval: POLL_30S });
export const useProductivity = (range: Range) =>
  useQuery({ queryKey: ['productivity', range], queryFn: () => api.getProductivity(range), refetchInterval: POLL_30S });
export const useSummarySparklines = () =>
  useQuery({ queryKey: ['summary-sparklines'], queryFn: api.getSummarySparklines, refetchInterval: POLL_30S });
export const useActivityHeatmap = (range: Range = '30d') =>
  useQuery({ queryKey: ['activity-heatmap', range], queryFn: () => api.getActivityHeatmap(range), refetchInterval: POLL_30S });

// -- Sessions --
export const useSessions = (range: Range = '7d') =>
  useQuery({ queryKey: ['sessions', range], queryFn: () => api.listSessions({ range, limit: 50 }), refetchInterval: POLL_30S });
export const useLiveSessions = () =>
  useQuery({ queryKey: ['live-sessions'], queryFn: api.liveSessions, refetchInterval: POLL_5S });
export const useSessionDetails = (id: string | null) =>
  useQuery({
    queryKey: ['session-details', id], enabled: !!id,
    queryFn: () => api.sessionDetails(id!),
    refetchInterval: id ? POLL_10S : false,
  });
export const useSessionFailures = (range: Range = '30d') =>
  useQuery({ queryKey: ['session-failures', range], queryFn: () => api.sessionFailures(range), refetchInterval: POLL_30S });

// -- MCP --
export const useMcpServers = (range: Range = '7d') =>
  useQuery({ queryKey: ['mcp-servers', range], queryFn: () => api.getMcpServers(range), refetchInterval: POLL_30S });
export const useMcpTools = (server: string | null, range: Range = '7d') =>
  useQuery({
    queryKey: ['mcp-tools', server, range], enabled: !!server,
    queryFn: () => api.getMcpTools(server!, range),
    refetchInterval: POLL_30S,
  });

// -- Skills --
export const useSkills = () =>
  useQuery({ queryKey: ['skills'], queryFn: api.listSkills, refetchInterval: POLL_30S });
export const useSkillEconomics = (range: Range = '30d') =>
  useQuery({ queryKey: ['skill-economics', range], queryFn: () => api.skillsEconomics(range), refetchInterval: POLL_30S });
export const useContextHealth = () =>
  useQuery({ queryKey: ['context-health'], queryFn: api.getContextHealth, refetchInterval: POLL_30S });

// -- HITL --
export const useDecisions = (status?: 'pending' | 'answered') =>
  useQuery({ queryKey: ['decisions', status], queryFn: () => api.listDecisions(status), refetchInterval: POLL_5S });
export const useInbox = (unread: 0 | 1 = 0) =>
  useQuery({ queryKey: ['inbox', unread], queryFn: () => api.listInbox({ unread, max_age_days: 30 }), refetchInterval: POLL_10S });

// -- Tasks --
export const useTasks = (p: { status?: string; quadrant?: string } = {}) =>
  useQuery({ queryKey: ['tasks', p], queryFn: () => api.listTasks(p), refetchInterval: POLL_5S });

// -- Schedules --
export const useSchedules = () =>
  useQuery({ queryKey: ['schedules'], queryFn: api.listSchedules, refetchInterval: POLL_30S });

// -- Mutations --
export function useEmergencyStop() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.emergencyStop,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['attention'] }); qc.invalidateQueries({ queryKey: ['summary'] }); qc.invalidateQueries({ queryKey: ['tasks'] }); },
  });
}
export function useEmergencyResume() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.emergencyResume, onSuccess: () => qc.invalidateQueries({ queryKey: ['attention'] }) });
}
export function useManualSync() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.postSync, onSuccess: () => qc.invalidateQueries() });
}
export function useAnswerDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: number; answer: string }) => api.answerDecision(args.id, args.answer),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['decisions'] }); qc.invalidateQueries({ queryKey: ['tasks'] }); },
  });
}
export function useMarkInboxRead() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.markInboxRead, onSuccess: () => qc.invalidateQueries({ queryKey: ['inbox'] }) });
}
export function useReplyInbox() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: number; body: string }) => api.replyInbox(args.id, args.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['inbox'] }),
  });
}
export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createTask, onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }) });
}
export function useApproveTask() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.approveTask, onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }) });
}
export function useRerunTask() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.rerunTask, onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }) });
}
export function useDeleteTask() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.deleteTask, onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }) });
}
export function useTriggerDispatcher() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.triggerDispatcher, onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }) });
}
export function useCreateSchedule() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createSchedule, onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }) });
}
export function useDeleteSchedule() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.deleteSchedule, onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }) });
}
export function usePatchSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: number; p: Partial<api.NewSchedule> }) => api.patchSchedule(args.id, args.p),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedules'] }),
  });
}
export function usePatchSkillAutonomy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { name: string; level: 'auto' | 'review' | 'manual' }) =>
      api.patchSkillAutonomy(args.name, args.level),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  });
}
// v0.6.0 launcher mutations.
export function usePatchSkillPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { name: string; preset: SkillPreset | null }) =>
      api.patchSkillPreset(args.name, args.preset),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['skills'] }),
  });
}
export function useLaunchSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { name: string; description_override?: string }) =>
      api.launchSkill(args.name, { description_override: args.description_override }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tasks'] });
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['dispatcher-state'] });
    },
  });
}
export function usePostLiveMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; body: string }) => api.postLiveMessage(args.id, args.body),
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ['session-details', v.id] }),
  });
}
