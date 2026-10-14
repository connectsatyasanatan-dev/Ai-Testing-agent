import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveMission,
  createRun,
  getMissionDAG,
  getRunEvaluation,
  getRunExecutionLog,
  getRunReport,
  planMission,
} from "@/lib/api-client";
import type {
  MissionDAGPlan,
  MissionEvaluationReport,
  MultiAgentMissionRequest,
  SuiteType,
} from "@/types/mission";
import type { Run } from "@/types/run";

export function useMissionDAG(missionId?: string) {
  return useQuery<MissionDAGPlan, Error>({
    queryKey: ["missions", missionId, "dag"],
    queryFn: () => getMissionDAG(missionId!),
    enabled: Boolean(missionId),
    staleTime: 10_000,
  });
}

export function usePlanMission(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation<MissionDAGPlan, Error, MultiAgentMissionRequest>({
    mutationFn: (input) => planMission(projectId, input),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "plans"] });
      queryClient.setQueryData(["missions", data.mission_id, "dag"], data);
    },
  });
}

export function useApproveMission(missionId?: string) {
  const queryClient = useQueryClient();
  return useMutation<{ mission_id: string; status: string; approved_at: string }, Error, string | undefined>({
    mutationFn: (comment) => approveMission(missionId!, comment),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["missions", missionId, "dag"] });
      void queryClient.invalidateQueries({ queryKey: ["plans"] });
    },
  });
}

export function useCreateRun(planId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    Run,
    Error,
    { environment_id: string; suite_type?: SuiteType; idempotency_key?: string }
  >({
    mutationFn: (data) => createRun(planId, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useRunEvaluation(runId?: string, isTerminal = false) {
  return useQuery<MissionEvaluationReport, Error>({
    queryKey: ["runs", runId, "evaluation"],
    queryFn: () => getRunEvaluation(runId!),
    enabled: Boolean(runId && isTerminal),
    staleTime: 60_000,
    retry: 2,
  });
}

export function useRunReport(runId?: string, enabled = false) {
  return useQuery<string, Error>({
    queryKey: ["runs", runId, "export", "report"],
    queryFn: () => getRunReport(runId!),
    enabled: Boolean(runId && enabled),
    staleTime: 60_000,
  });
}

export function useRunExecutionLog(runId?: string, enabled = false) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["runs", runId, "export", "log"],
    queryFn: () => getRunExecutionLog(runId!),
    enabled: Boolean(runId && enabled),
    staleTime: 60_000,
  });
}
