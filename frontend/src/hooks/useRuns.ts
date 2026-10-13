import { useQuery } from "@tanstack/react-query";
import { listPlans, listRuns } from "@/lib/api-client";
import type { PlanListResponse } from "@/types/plan";
import type { RunFilterParams, RunListResponse } from "@/types/run";

export function useRuns(projectId: string, params: RunFilterParams) {
  return useQuery<RunListResponse, Error>({
    queryKey: ["projects", projectId, "runs", params],
    queryFn: () => listRuns(projectId, params),
    enabled: Boolean(projectId),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const hasActive = data.items.some(
        (r) => r.status === "running" || r.status === "queued"
      );
      return hasActive ? 4_000 : false;
    },
  });
}

export function usePlans(projectId: string) {
  return useQuery<PlanListResponse, Error>({
    queryKey: ["projects", projectId, "plans"],
    queryFn: () => listPlans(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}
