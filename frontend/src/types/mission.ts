/**
 * frontend/src/types/mission.ts
 * =============================
 * Types for multi-agent autonomous testing missions, DAG representation,
 * requirement traceability, and evaluator reports.
 */

export type AgentRole =
  | "supervisor"
  | "api_specialist"
  | "ui_tester"
  | "security_tester"
  | "performance_tester"
  | "evaluator"
  | "general";

export type TaskPriority = "P0" | "P1" | "P2" | "P3";

export type SuiteType = "smoke" | "regression" | "all";

export interface ContextExportDef {
  json_path: string;
  is_secret: boolean;
}

export interface DAGTask {
  task_id: string;
  name: string;
  assigned_role: AgentRole;
  adapter_type: "http_api" | "web";
  depends_on: string[];
  steps: Array<Record<string, unknown>>;
  exports?: Record<string, ContextExportDef>;
  timeout_seconds: number;
  requirement_refs: string[];
  priority: TaskPriority;
  is_smoke: boolean;
  is_regression: boolean;
}

export interface MissionDAGPlan {
  mission_id: string;
  name: string;
  description?: string | null;
  tasks: DAGTask[];
  max_parallel_tasks: number;
  requires_approval: boolean;
  status?: string;
  version?: number;
  created_at?: string;
  created_by?: string;
  approved_at?: string | null;
  approved_by?: string | null;
}

export interface MultiAgentMissionRequest {
  requirements_content: string;
  openapi_spec?: string | null;
  scenarios_content?: string | null;
  target_urls?: string[];
  environment_id: string;
  max_parallel_tasks?: number;
}

export interface RequirementCoverageStatus {
  requirement_id: string;
  status: "PASSED" | "FAILED" | "BLOCKED" | "UNTESTED";
  tested_by_tasks: string[];
  notes?: string | null;
}

export interface RootCauseItem {
  task_id: string;
  status: string;
  cause_type: "confirmed" | "probable" | "unknown";
  classification: string;
  diagnostic: string;
  failed_step_id?: string | null;
  failed_action?: string | null;
  http_status?: number | null;
  failed_assertions?: Array<Record<string, unknown>>;
  evidence_references: string[];
  dependency_chain: string[];
  suggested_debugging_steps: string[];
  is_ai_generated: boolean;
}

export interface MissionEvaluationReport {
  mission_id: string;
  run_id: string;
  verdict: "passed" | "failed" | "blocked" | "cancelled" | "inconclusive";
  total_tasks: number;
  passed_tasks: number;
  failed_tasks: number;
  blocked_tasks: number;
  skipped_tasks: number;
  cancelled_tasks: number;
  inconclusive_tasks: number;
  evaluation_version?: string;
  evaluated_at?: string | null;
  is_idempotent_replay?: boolean;
  requirement_coverage: RequirementCoverageStatus[];
  root_cause_analysis: RootCauseItem[];
  executive_summary: string;
  agent_telemetry?: Record<string, unknown>;
}

export interface CreateMissionRunInput {
  environment_id: string;
  suite_type?: SuiteType;
  idempotency_key?: string;
}
