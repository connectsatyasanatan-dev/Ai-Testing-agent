/**
 * src/lib/api-client.ts
 * =====================
 * Typed HTTP client. All API calls go through here — never scattered fetch()
 * calls in components. One place to add auth headers, logging, retry logic.
 *
 * Current state: only /health is live (P0-01).
 * Add methods as API routes are implemented (P0-05, P1-*).
 */

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
// Empty string = same origin. Vite dev server proxies /api/* to FastAPI.
// In production set VITE_API_URL to the absolute API origin.

// ── Core fetch wrapper ────────────────────────────────────────────────────────

interface FetchOptions extends RequestInit {
  /** When true, return the response even if status >= 400 (no throw). */
  allowErrorResponse?: boolean;
  /** Response parse type: default is json */
  responseType?: "json" | "text";
}

async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { allowErrorResponse = false, responseType = "json", ...init } = options;

  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",   // always send session cookies
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok && !allowErrorResponse) {
    const body = await response.json().catch(() => ({})) as Record<string, unknown>;
    const err = body as { error?: { code?: string; message?: string }; detail?: unknown };
    let message = err.error?.message;
    if (!message && err.detail) {
      if (Array.isArray(err.detail)) {
        message = err.detail
          .map((d: { msg?: string; loc?: string[] }) => d.msg || JSON.stringify(d))
          .join("; ");
      } else if (typeof err.detail === "string") {
        message = err.detail;
      }
    }
    throw new ApiError(
      response.status,
      err.error?.code ?? (response.status === 422 ? "VALIDATION_ERROR" : "UNKNOWN"),
      message ?? `HTTP ${response.status} ${response.statusText}`,
    );
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return null as unknown as T;
  }

  if (responseType === "text" || response.headers.get("content-type")?.includes("text/markdown") || response.headers.get("content-type")?.includes("text/plain")) {
    return response.text() as unknown as T;
  }

  return response.json() as Promise<T>;
}

// ── Error type ────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isUnauthorized(): boolean  { return this.status === 401; }
  get isForbidden(): boolean     { return this.status === 403; }
  get isNotFound(): boolean      { return this.status === 404; }
  get isConflict(): boolean      { return this.status === 409; }
  get isValidation(): boolean    { return this.status === 422; }
  get isPlanNotApproved(): boolean { return this.code === "PLAN_NOT_APPROVED"; }
  get isAiUnavailable(): boolean   { return this.code === "AI_PROVIDER_NOT_CONFIGURED"; }
}

// ── Health ────────────────────────────────────────────────────────────────────

export interface HealthResponse { status: string; version: string; }

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

// ── Authentication ────────────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  email: string;
  full_name?: string | null;
  is_active: boolean;
}

export function login(credentials: { email: string; password: string }): Promise<AuthUser> {
  return apiFetch<AuthUser>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify(credentials),
  });
}

export function register(data: { email: string; password: string; full_name?: string }): Promise<AuthUser> {
  return apiFetch<AuthUser>("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function logout(): Promise<{ message: string }> {
  return apiFetch<{ message: string }>("/api/v1/auth/logout", {
    method: "POST",
  });
}

export async function getCurrentUser(): Promise<AuthUser | null> {
  try {
    return await apiFetch<AuthUser>("/api/v1/auth/me");
  } catch (err) {
    if (err instanceof ApiError && err.isUnauthorized) {
      return null;
    }
    throw err;
  }
}

export interface ApiTokenItem {
  id: string;
  name: string;
  token_prefix: string;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
  is_revoked: boolean;
}

export interface CreatedTokenResult {
  id: string;
  name: string;
  token: string;
  token_prefix: string;
  expires_at: string | null;
  created_at: string;
}

export function listApiTokens(): Promise<{ items: ApiTokenItem[] }> {
  return apiFetch<{ items: ApiTokenItem[] }>("/api/v1/auth/tokens");
}

export function createApiToken(data: { name: string; expires_in_days?: number }): Promise<CreatedTokenResult> {
  return apiFetch<CreatedTokenResult>("/api/v1/auth/tokens", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function revokeApiToken(tokenId: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>(`/api/v1/auth/tokens/${tokenId}`, {
    method: "DELETE",
  });
}

// ── Environments ─────────────────────────────────────────────────────────────

import type {
  CreateEnvironmentInput,
  Environment,
  EnvironmentListResponse,
  UpdateEnvironmentInput,
} from "@/types/environment";

export function listEnvironments(
  projectId: string,
  includeArchived = false
): Promise<EnvironmentListResponse> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiFetch<EnvironmentListResponse>(
    `/api/v1/projects/${projectId}/environments${query}`
  );
}

export function getEnvironment(
  projectId: string,
  environmentId: string
): Promise<Environment> {
  return apiFetch<Environment>(
    `/api/v1/projects/${projectId}/environments/${environmentId}`
  );
}

export function createEnvironment(
  projectId: string,
  data: CreateEnvironmentInput
): Promise<Environment> {
  return apiFetch<Environment>(
    `/api/v1/projects/${projectId}/environments`,
    {
      method: "POST",
      body: JSON.stringify(data),
    }
  );
}

export function updateEnvironment(
  projectId: string,
  environmentId: string,
  data: UpdateEnvironmentInput
): Promise<Environment> {
  return apiFetch<Environment>(
    `/api/v1/projects/${projectId}/environments/${environmentId}`,
    {
      method: "PATCH",
      body: JSON.stringify(data),
    }
  );
}

export function deleteEnvironment(
  projectId: string,
  environmentId: string
): Promise<void> {
  return apiFetch<void>(
    `/api/v1/projects/${projectId}/environments/${environmentId}`,
    {
      method: "DELETE",
    }
  );
}

// ── Projects ──────────────────────────────────────────────────────────────────

import type { Project, ProjectListResponse } from "@/types/project";

export function listProjects(): Promise<ProjectListResponse> {
  return apiFetch<ProjectListResponse>("/api/v1/projects");
}

export function getProject(projectId: string): Promise<Project> {
  return apiFetch<Project>(`/api/v1/projects/${projectId}`);
}

export function createProject(data: { name: string; description?: string }): Promise<Project> {
  return apiFetch<Project>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ── Dashboard Metrics ─────────────────────────────────────────────────────────

import type {
  ArtifactDownloadResponse,
  DashboardMetrics,
  EvidenceArtifact,
  Run,
  RunEvent,
  RunFilterParams,
  RunListResponse,
} from "@/types/run";

export function getDashboardMetrics(projectId: string): Promise<DashboardMetrics> {
  return apiFetch<DashboardMetrics>(`/api/v1/projects/${projectId}/dashboard`);
}

// ── Test Runs ─────────────────────────────────────────────────────────────────

export function listRuns(
  projectId: string,
  params?: RunFilterParams
): Promise<RunListResponse> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.plan_id) query.set("plan_id", params.plan_id);
  if (params?.adapter_type) query.set("adapter_type", params.adapter_type);
  if (params?.from_date) query.set("from_date", params.from_date);
  if (params?.to_date) query.set("to_date", params.to_date);
  if (params?.sort_by) query.set("sort_by", params.sort_by);
  if (params?.sort_order) query.set("sort_order", params.sort_order);
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));

  const qs = query.toString();
  return apiFetch<RunListResponse>(
    `/api/v1/projects/${projectId}/runs${qs ? `?${qs}` : ""}`
  );
}

export function getRun(runId: string): Promise<Run> {
  return apiFetch<Run>(`/api/v1/runs/${runId}`);
}

export function getRunEvents(runId: string): Promise<RunEvent[]> {
  return apiFetch<RunEvent[]>(`/api/v1/runs/${runId}/events`);
}

export function cancelRun(runId: string, reason?: string): Promise<Run> {
  return apiFetch<Run>(`/api/v1/runs/${runId}/cancel`, {
    method: "POST",
    body: JSON.stringify(reason ? { reason } : {}),
  });
}

export function createRun(
  planId: string,
  data: {
    environment_id: string;
    suite_type?: "smoke" | "regression" | "all";
    idempotency_key?: string;
  }
): Promise<Run> {
  return apiFetch<Run>(`/api/v1/plans/${planId}/runs`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getRunEvaluation(runId: string): Promise<MissionEvaluationReport> {
  return apiFetch<MissionEvaluationReport>(`/api/v1/runs/${runId}/evaluation`);
}

export function getRunReport(runId: string): Promise<string> {
  return apiFetch<string>(`/api/v1/runs/${runId}/export/report`, {
    responseType: "text",
  });
}

export function getRunExecutionLog(runId: string): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>(`/api/v1/runs/${runId}/export/log`);
}


export function listRunArtifacts(
  runId: string,
  includeExpired = false
): Promise<EvidenceArtifact[]> {
  const qs = includeExpired ? "?include_expired=true" : "";
  return apiFetch<EvidenceArtifact[]>(`/api/v1/runs/${runId}/artifacts${qs}`);
}

export function getRunArtifact(
  runId: string,
  artifactId: string
): Promise<ArtifactDownloadResponse> {
  return apiFetch<ArtifactDownloadResponse>(
    `/api/v1/runs/${runId}/artifacts/${artifactId}`
  );
}

export function getArtifactDownloadUrl(
  runId: string,
  artifactId: string
): string {
  return `${API_BASE}/api/v1/runs/${runId}/artifacts/${artifactId}?download=true`;
}

// ── Plans ─────────────────────────────────────────────────────────────────────

import type {
  CreatePlanInput,
  CreatePlanVersionInput,
  GeneratePlanInput,
  GeneratePlanResponse,
  Plan,
  PlanFilterParams,
  PlanListResponse,
  PlanValidationResponse,
  PlanVersionsResponse,
  TestStep,
  UpdatePlanInput,
} from "@/types/plan";

export function listPlans(
  projectId: string,
  params?: PlanFilterParams
): Promise<PlanListResponse> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.search) query.set("search", params.search);
  const qs = query.toString();
  return apiFetch<PlanListResponse>(
    `/api/v1/projects/${projectId}/plans${qs ? `?${qs}` : ""}`
  );
}

export function getPlan(planId: string): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}`);
}

export function getPlanVersion(planId: string, version: number): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/versions/${version}`);
}

export function listPlanVersions(planId: string): Promise<PlanVersionsResponse> {
  return apiFetch<PlanVersionsResponse>(`/api/v1/plans/${planId}/versions`);
}

export function createPlan(
  projectId: string,
  data: CreatePlanInput
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/projects/${projectId}/plans`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function updatePlan(
  planId: string,
  data: UpdatePlanInput
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function updatePlanVersion(
  planId: string,
  version: number,
  data: UpdatePlanInput
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/versions/${version}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export function createPlanVersion(
  planId: string,
  data: CreatePlanVersionInput
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/versions`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function validateProjectPlan(
  projectId: string,
  data: { adapter_type: string; steps: TestStep[]; environment_id?: string }
): Promise<PlanValidationResponse> {
  return apiFetch<PlanValidationResponse>(
    `/api/v1/projects/${projectId}/plans/validate`,
    {
      method: "POST",
      body: JSON.stringify(data),
    }
  );
}

export function validatePlan(
  planId: string,
  data?: { adapter_type: string; steps: TestStep[]; environment_id?: string }
): Promise<PlanValidationResponse> {
  return apiFetch<PlanValidationResponse>(`/api/v1/plans/${planId}/validate`, {
    method: "POST",
    body: data ? JSON.stringify(data) : undefined,
  });
}

export function submitPlanReview(
  planId: string,
  version: number
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/submit-review`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

export function approvePlan(
  planId: string,
  version: number
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/approve`, {
    method: "POST",
    body: JSON.stringify({ version }),
  });
}

export function rejectPlan(
  planId: string,
  version: number,
  reason?: string
): Promise<Plan> {
  return apiFetch<Plan>(`/api/v1/plans/${planId}/reject`, {
    method: "POST",
    body: JSON.stringify({ version, reason }),
  });
}

export function archivePlan(
  planId: string,
  version?: number
): Promise<{ message: string; plan_id: string; version?: number }> {
  return apiFetch<{ message: string; plan_id: string; version?: number }>(
    `/api/v1/plans/${planId}/archive`,
    {
      method: "POST",
      body: JSON.stringify({ version }),
    }
  );
}

export function generatePlan(
  projectId: string,
  data: GeneratePlanInput
): Promise<GeneratePlanResponse> {
  return apiFetch<GeneratePlanResponse>(
    `/api/v1/projects/${projectId}/plans/generate`,
    {
      method: "POST",
      body: JSON.stringify(data),
    }
  );
}

// ── Project Members & Audit (P4-02 & P6-02) ───────────────────────────────────

export interface ProjectMember {
  id: string;
  user_id: string;
  email: string;
  full_name: string | null;
  role: "owner" | "maintainer" | "viewer";
  created_at: string;
}

export interface ProjectMemberListResponse {
  items: ProjectMember[];
  total: number;
}

export function listProjectMembers(
  projectId: string
): Promise<ProjectMemberListResponse> {
  return apiFetch<ProjectMemberListResponse>(`/api/v1/projects/${projectId}/members`);
}

export function addProjectMember(
  projectId: string,
  data: { email: string; role: "owner" | "maintainer" | "viewer" }
): Promise<ProjectMember> {
  return apiFetch<ProjectMember>(`/api/v1/projects/${projectId}/members`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function removeProjectMember(
  projectId: string,
  userId: string
): Promise<void> {
  return apiFetch<void>(`/api/v1/projects/${projectId}/members/${userId}`, {
    method: "DELETE",
  });
}

export interface AuditEventItem {
  id: string;
  event_type: string;
  actor_id: string | null;
  project_id: string | null;
  resource_type: string | null;
  resource_id: string | null;
  timestamp: string;
  metadata_json: Record<string, unknown> | null;
  metadata_redacted: boolean;
}

export interface AuditEventListResponse {
  items: AuditEventItem[];
  total: number;
}

export function listProjectAuditEvents(
  projectId: string,
  params?: { limit?: number; offset?: number; event_type?: string }
): Promise<AuditEventListResponse> {
  const query = new URLSearchParams();
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.event_type) query.set("event_type", params.event_type);
  const qs = query.toString();
  return apiFetch<AuditEventListResponse>(
    `/api/v1/projects/${projectId}/audit-events${qs ? `?${qs}` : ""}`
  );
}

// ── Autonomous Multi-Agent Missions (Task 9) ──────────────────────────────────

import type {
  MissionDAGPlan,
  MissionEvaluationReport,
  MultiAgentMissionRequest,
} from "@/types/mission";

export function planMission(
  projectId: string,
  data: MultiAgentMissionRequest
): Promise<MissionDAGPlan> {
  return apiFetch<MissionDAGPlan>(`/api/v1/projects/${projectId}/missions/plan`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function getMissionDAG(missionId: string): Promise<MissionDAGPlan> {
  return apiFetch<MissionDAGPlan>(`/api/v1/missions/${missionId}/dag`);
}

export function approveMission(
  missionId: string,
  comment?: string
): Promise<{ mission_id: string; status: string; approved_at: string }> {
  return apiFetch<{ mission_id: string; status: string; approved_at: string }>(
    `/api/v1/missions/${missionId}/approve`,
    {
      method: "POST",
      body: JSON.stringify(comment ? { comment } : {}),
    }
  );
}



