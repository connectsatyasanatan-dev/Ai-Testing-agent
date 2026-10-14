import { useQuery } from "@tanstack/react-query";
import {
  getRun,
  getRunArtifact,
  getRunEvents,
  listRunArtifacts,
} from "@/lib/api-client";
import type {
  ArtifactDownloadResponse,
  EvidenceArtifact,
  Run,
  RunEvent,
} from "@/types/run";

export function useRun(runId: string) {
  return useQuery<Run, Error>({
    queryKey: ["runs", runId],
    queryFn: () => getRun(runId),
    enabled: Boolean(runId),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (!data) return false;
      const isRunning = data.status === "running" || data.status === "queued";
      return isRunning ? 3_000 : false;
    },
  });
}

export function useRunEvents(runId: string, isActive = false) {
  return useQuery<RunEvent[], Error>({
    queryKey: ["runs", runId, "events"],
    queryFn: () => getRunEvents(runId),
    enabled: Boolean(runId),
    staleTime: 5_000,
    refetchInterval: isActive ? 4_000 : false,
  });
}

export function useRunArtifacts(runId: string, includeExpired = true) {
  return useQuery<EvidenceArtifact[], Error>({
    queryKey: ["runs", runId, "artifacts", includeExpired],
    queryFn: () => listRunArtifacts(runId, includeExpired),
    enabled: Boolean(runId),
    staleTime: 10_000,
  });
}

export function useRunArtifact(runId: string, artifactId: string | null) {
  return useQuery<ArtifactDownloadResponse, Error>({
    queryKey: ["runs", runId, "artifacts", artifactId],
    queryFn: () => getRunArtifact(runId, artifactId!),
    enabled: Boolean(runId && artifactId),
    staleTime: 60_000,
  });
}
