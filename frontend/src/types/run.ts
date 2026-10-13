export type RunStatus =
  | "queued"
  | "running"
  | "passed"
  | "failed"
  | "blocked"
  | "skipped"
  | "inconclusive"
  | "cancelled";

export interface StepAttempt {
  id: string;
  run_id: string;
  step_id: string;
  attempt_number: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  action?: string | null;
  duration_ms?: number | null;
  assertions?: Array<{ type: string; value?: unknown; expected?: unknown; actual?: unknown; passed?: boolean }> | null;
  failure_category?: "assertion_failure" | "timeout" | "cancellation" | "policy_blocked" | "execution_error" | null;
}

export interface EvidenceArtifact {
  id: string;
  run_id: string;
  step_attempt_id?: string | null;
  type: string;
  checksum?: string | null;
  redaction_status: string;
  retention_until?: string | null;
  created_at: string;
  size_bytes?: number | null;
  content_type?: string | null;
  is_expired?: boolean;
}

export interface ArtifactDownloadResponse {
  artifact: EvidenceArtifact;
  download_url?: string | null;
  inline_content?: string | null;
}

export interface Run {
  id: string;
  plan_id: string;
  plan_version: number;
  environment_id: string;
  project_id: string;
  status: RunStatus;
  requested_by: string;
  worker_id: string | null;
  idempotency_key: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  plan_name?: string | null;
  adapter_type?: string | null;
  environment_name?: string | null;
  step_attempts: StepAttempt[];
}

export interface RunEvent {
  id: string;
  run_id: string;
  step_id: string | null;
  attempt_id: string | null;
  event_type: string;
  timestamp: string;
  message: string | null;
  event_metadata: Record<string, unknown> | null;
}

export interface RunListResponse {
  items: Run[];
  total: number;
  limit: number;
  offset: number;
}

export interface DashboardMetrics {
  project_id: string;
  total_plans: number;
  total_runs: number;
  status_counts: Record<string, number>;
  adapter_breakdown: Record<string, number>;
  recent_runs: Run[];
  recent_activity: RunEvent[];
}

export interface RunFilterParams {
  status?: string;
  plan_id?: string;
  adapter_type?: string;
  from_date?: string;
  to_date?: string;
  sort_by?: "created_at" | "started_at" | "finished_at" | "status";
  sort_order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
