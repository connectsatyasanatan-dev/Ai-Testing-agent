/**
 * useEnvironments.ts — React Query hooks for environment management.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createEnvironment,
  deleteEnvironment,
  getEnvironment,
  listEnvironments,
  updateEnvironment,
} from "@/lib/api-client";
import type {
  CreateEnvironmentInput,
  Environment,
  EnvironmentListResponse,
  UpdateEnvironmentInput,
} from "@/types/environment";

export function useEnvironments(projectId: string, includeArchived = false) {
  return useQuery<EnvironmentListResponse, Error>({
    queryKey: ["environments", projectId, includeArchived],
    queryFn: () => listEnvironments(projectId, includeArchived),
    enabled: Boolean(projectId),
  });
}

export function useEnvironment(projectId: string, environmentId: string) {
  return useQuery<Environment, Error>({
    queryKey: ["environment", projectId, environmentId],
    queryFn: () => getEnvironment(projectId, environmentId),
    enabled: Boolean(projectId && environmentId),
  });
}

export function useCreateEnvironment(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateEnvironmentInput) => createEnvironment(projectId, data),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["environments", projectId] });
    },
  });
}

export function useUpdateEnvironment(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      environmentId,
      data,
    }: {
      environmentId: string;
      data: UpdateEnvironmentInput;
    }) => updateEnvironment(projectId, environmentId, data),
    onSuccess: (_, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["environments", projectId] });
      void queryClient.invalidateQueries({
        queryKey: ["environment", projectId, variables.environmentId],
      });
    },
  });
}

export function useDeleteEnvironment(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (environmentId: string) => deleteEnvironment(projectId, environmentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["environments", projectId] });
    },
  });
}
