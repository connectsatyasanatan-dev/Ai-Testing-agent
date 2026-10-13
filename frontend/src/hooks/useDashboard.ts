import { useQuery } from "@tanstack/react-query";
import { getDashboardMetrics } from "@/lib/api-client";
import type { DashboardMetrics } from "@/types/run";

export function useDashboard(projectId: string) {
  return useQuery<DashboardMetrics, Error>({
    queryKey: ["projects", projectId, "dashboard"],
    queryFn: () => getDashboardMetrics(projectId),
    enabled: Boolean(projectId),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const hasActive = data.recent_runs.some(
        (r) => r.status === "running" || r.status === "queued"
      );
      return hasActive ? 4_000 : false;
    },
  });
}
