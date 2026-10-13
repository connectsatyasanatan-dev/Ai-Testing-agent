import { useQuery } from "@tanstack/react-query";
import { listProjects, getProject } from "@/lib/api-client";
import type { Project, ProjectListResponse } from "@/types/project";

export function useProjects() {
  return useQuery<ProjectListResponse, Error>({
    queryKey: ["projects"],
    queryFn: listProjects,
    staleTime: 30_000,
  });
}

export function useProject(projectId: string) {
  return useQuery<Project, Error>({
    queryKey: ["projects", projectId],
    queryFn: () => getProject(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}
