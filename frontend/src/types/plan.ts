/**
 * frontend/src/types/plan.ts
 * ==========================
 * Canonical TypeScript types for declarative Test Plans, Test Steps,
 * Assertions, Review/Approval lifecycles, and Validation.
 */

export type PlanStatus =
  | "draft"
  | "pending_review"
  | "approved"
  | "rejected"
  | "archived";

export type AdapterType = "http_api" | "web";

export type IdempotencyClass =
  | "safe"
  | "idempotent"
  | "non_idempotent"
  | "unknown";

export type RiskLevel = "low" | "medium" | "high" | "critical";

export type AssertionType =
  // HTTP assertions
  | "status_code"
  | "json_path_equals"
  | "json_path_exists"
  | "json_path_matches"
  | "json_schema"
  | "header_equals"
  | "header_exists"
  | "response_time_under"
  // Web assertions
  | "url_contains"
  | "url_equals"
  | "title_contains"
  | "title_equals"
  | "text_visible"
  | "element_visible"
  | "element_hidden";

export interface Assertion {
  type: AssertionType;
  value?: string | number | boolean;
  path?: string;
  schema?: Record<string, unknown>;
  message?: string;
}

export interface RetryPolicy {
  max_attempts: number;
  backoff_seconds?: number;
  retryable_status_codes?: number[];
  allow_non_idempotent_retry?: boolean;
}

export interface TestStep {
  step_id: string;
  step_order?: number;
  adapter_type: string;
  action: string;
  inputs: Record<string, unknown>;
  assertions: Assertion[];
  timeout_seconds: number;
  retry_policy: RetryPolicy;
  idempotency_class: IdempotencyClass;
  risk_level: RiskLevel;
  description?: string | null;
}

export interface PlanSummary {
  id: string;
  plan_id: string;
  project_id: string;
  name: string;
  version: number;
  adapter_type: string;
  status: PlanStatus;
  description: string | null;
  requires_approval: boolean;
  created_by: string;
  approved_by: string | null;
  approved_at: string | null;
  created_at: string;
  updated_at: string;
  steps?: TestStep[];
}

export interface Plan extends PlanSummary {
  steps: TestStep[];
}

export interface PlanListResponse {
  items: PlanSummary[];
  total: number;
}

export interface PlanVersionSummary {
  version: number;
  status: PlanStatus;
  name?: string | null;
  step_count?: number;
  approved_by?: string | null;
  approved_at?: string | null;
  created_at: string;
}

export interface PlanVersionsResponse {
  plan_id: string;
  versions: PlanVersionSummary[];
}

export interface ValidationIssue {
  field?: string | null;
  step_id?: string | null;
  message: string;
  severity: "error" | "warning";
}

export interface PlanValidationResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  issues: ValidationIssue[];
}

export interface CreatePlanInput {
  name: string;
  adapter_type: string;
  description?: string;
  steps: TestStep[];
}

export interface UpdatePlanInput {
  name?: string;
  description?: string;
  steps: TestStep[];
}

export interface CreatePlanVersionInput {
  name?: string;
  description?: string;
  steps: TestStep[];
}

export interface PlanFilterParams {
  status?: PlanStatus;
  search?: string;
}

export interface GeneratePlanInput {
  intent: string;
  adapter_type: AdapterType;
  environment_id?: string | null;
  spec_content?: string | null;
}

export interface BlockedStep {
  step_id: string;
  action: string;
  reason: string;
  severity: string;
}

export interface GeneratePlanResponse {
  status: string;
  plan_id: string;
  name: string;
  version: number;
  adapter_type: string;
  environment_id?: string | null;
  steps: TestStep[];
  blocked_steps: BlockedStep[];
  requires_approval: boolean;
  message: string;
}

