/**
 * frontend/src/hooks/usePlans.ts
 * ==============================
 * React Query hooks for managing test plans, versioning, review, and approval.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approvePlan,
  archivePlan,
  createPlan,
  createPlanVersion,
  getPlan,
  getPlanVersion,
  listPlans,
  listPlanVersions,
  rejectPlan,
  submitPlanReview,
  updatePlan,
  validatePlan,
  validateProjectPlan,
} from "@/lib/api-client";
import type {
  CreatePlanInput,
  CreatePlanVersionInput,
  PlanFilterParams,
  TestStep,
  UpdatePlanInput,
} from "@/types/plan";

export function usePlans(projectId?: string, params?: PlanFilterParams) {
  return useQuery({
    queryKey: ["plans", projectId, params],
    queryFn: () => listPlans(projectId!, params),
    enabled: Boolean(projectId),
  });
}

export function usePlan(planId?: string) {
  return useQuery({
    queryKey: ["plan", planId],
    queryFn: () => getPlan(planId!),
    enabled: Boolean(planId),
  });
}

export function usePlanVersion(planId?: string, version?: number) {
  return useQuery({
    queryKey: ["plan-version", planId, version],
    queryFn: () => getPlanVersion(planId!, version!),
    enabled: Boolean(planId && version !== undefined),
  });
}

export function usePlanVersions(planId?: string) {
  return useQuery({
    queryKey: ["plan-versions", planId],
    queryFn: () => listPlanVersions(planId!),
    enabled: Boolean(planId),
  });
}

export function useCreatePlan(projectId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreatePlanInput) => createPlan(projectId!, data),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["plans", projectId] });
    },
  });
}

export function useUpdatePlan(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: UpdatePlanInput) => updatePlan(planId!, data),
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
      void qc.invalidateQueries({ queryKey: ["plan-version", planId, updated.version] });
    },
  });
}

export function useCreatePlanVersion(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreatePlanVersionInput) => createPlanVersion(planId!, data),
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
      void qc.invalidateQueries({ queryKey: ["plan-version", planId, updated.version] });
    },
  });
}

export function useSubmitPlanReview(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => submitPlanReview(planId!, version),
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
      void qc.invalidateQueries({ queryKey: ["plan-version", planId, updated.version] });
    },
  });
}

export function useApprovePlan(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => approvePlan(planId!, version),
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
      void qc.invalidateQueries({ queryKey: ["plan-version", planId, updated.version] });
    },
  });
}

export function useRejectPlan(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ version, reason }: { version: number; reason?: string }) =>
      rejectPlan(planId!, version, reason),
    onSuccess: (updated) => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
      void qc.invalidateQueries({ queryKey: ["plan-version", planId, updated.version] });
    },
  });
}

export function useArchivePlan(planId?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version?: number) => archivePlan(planId!, version),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["plan", planId] });
      void qc.invalidateQueries({ queryKey: ["plans"] });
      void qc.invalidateQueries({ queryKey: ["plan-versions", planId] });
    },
  });
}

export function useValidatePlan() {
  return useMutation({
    mutationFn: ({
      planId,
      projectId,
      data,
    }: {
      planId?: string;
      projectId?: string;
      data: { adapter_type: string; steps: TestStep[]; environment_id?: string };
    }) => {
      if (planId) {
        return validatePlan(planId, data);
      }
      return validateProjectPlan(projectId!, data);
    },
  });
}
