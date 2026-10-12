/**
 * environment.ts — TypeScript types for environment management.
 */

export type EnvironmentStatus = "active" | "archived";

export interface Environment {
  id: string;
  project_id: string;
  name: string;
  base_url: string;
  allowlist?: string[] | null;
  secret_refs?: Record<string, string> | null;
  description?: string | null;
  status: EnvironmentStatus;
  created_at: string;
  updated_at: string;
}

export interface EnvironmentListResponse {
  items: Environment[];
  total: number;
}

export interface CreateEnvironmentInput {
  name: string;
  base_url: string;
  allowlist?: string[] | null;
  secret_refs?: Record<string, string> | null;
  description?: string | null;
}

export interface UpdateEnvironmentInput {
  name?: string;
  base_url?: string;
  allowlist?: string[] | null;
  secret_refs?: Record<string, string> | null;
  description?: string | null;
  status?: EnvironmentStatus;
}
