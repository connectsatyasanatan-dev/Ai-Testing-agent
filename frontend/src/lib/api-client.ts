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
}

async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { allowErrorResponse = false, ...init } = options;

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
    const err = body as { error?: { code?: string; message?: string } };
    throw new ApiError(
      response.status,
      err.error?.code ?? "UNKNOWN",
      err.error?.message ?? `HTTP ${response.status} ${response.statusText}`,
    );
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return null as unknown as T;
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
