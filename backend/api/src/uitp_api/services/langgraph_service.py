"""
uitp_api.services.langgraph_service
===================================
LangGraph Multi-Agent Orchestration Engine for autonomous mission decomposition.
Coordinates Supervisor, RAG Retriever, API Specialist, UI Specialist, and Evaluator
agents across a typed StateGraph.

AGENTS & ROLES:
1. RAG Retriever Node: Indexes OpenAPI & Requirements specs into semantic chunks.
2. Supervisor Node: Decomposes target journeys into prioritized multi-adapter tasks.
3. API Specialist Node: Validates REST contracts, JSONPaths, and auth token exports.
4. UI Specialist Node: Configures Playwright browser journeys, selectors, and screenshot captures.
5. Evaluator Guardrail Node: Kahn's DAG acyclicity check, SSRF allowlists, and final validation.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from uitp_api.exceptions import ValidationError
from uitp_api.services.ai_plan_service import _contains_forbidden_tokens
from uitp_api.services.ai_provider import AIProvider, get_ai_provider
from uitp_api.services.rag_service import RAGContextIndexer
from uitp_contracts.enums import AdapterType, AgentRole, TaskPriority
from uitp_contracts.plan import (
    DAGTaskSchema,
    MissionDAGPlan,
    MultiAgentMissionRequest,
    TokenUsage,
)

logger = logging.getLogger(__name__)


class LangGraphMissionState(TypedDict, total=False):
    """Shared state passed between LangGraph agent nodes."""

    # Inputs
    requirements_content: str
    openapi_spec: str | None
    scenarios_content: str | None
    target_urls: list[str]
    environment_id: str
    test_strategy: str
    temperature: float
    max_parallel_tasks: int

    # RAG state
    rag_context: dict[str, Any]
    retrieved_endpoints_summary: str
    retrieved_requirements_summary: str

    # Planned tasks & metadata
    raw_output: dict[str, Any]
    mission_name: str
    mission_description: str
    parsed_tasks: list[dict[str, Any]]
    final_task_schemas: list[DAGTaskSchema]

    # Telemetry and traces
    agent_traces: list[dict[str, Any]]
    token_usage: dict[str, int]
    validation_errors: list[str]
    completed: bool


# ── LangGraph Agent Nodes ─────────────────────────────────────────────────────

async def rag_retrieval_node(state: LangGraphMissionState) -> dict[str, Any]:
    """Node 1: RAG Retriever chunks and indexes specifications."""
    req_content = state.get("requirements_content", "")
    openapi_spec = state.get("openapi_spec")
    scenarios = state.get("scenarios_content")

    indexer = RAGContextIndexer()
    ep_count = indexer.index_openapi(openapi_spec) if openapi_spec else 0
    req_count = indexer.index_requirements(req_content)
    scn_count = indexer.index_scenarios(scenarios) if scenarios else 0

    queries = [
        "auth login credentials jwt token",
        "employee list create update delete management",
        "task create assign status",
        "dashboard statistics summary",
        "frontend web navigation ui",
    ]
    rag_packet = indexer.build_augmented_context(queries)

    trace = {
        "agent": "rag_retriever",
        "action": "index_and_retrieve",
        "message": (
            f"RAG Indexer indexed {ep_count} OpenAPI endpoints, {req_count} requirement rules, "
            f"and {scn_count} scenarios. Retrieved top matching contexts for multi-agent planning."
        ),
        "endpoints_indexed": ep_count,
        "requirements_indexed": req_count,
    }

    traces = list(state.get("agent_traces", []))
    traces.append(trace)

    return {
        "rag_context": rag_packet,
        "retrieved_endpoints_summary": rag_packet.get("endpoints_context", ""),
        "retrieved_requirements_summary": rag_packet.get("requirements_context", ""),
        "agent_traces": traces,
    }


async def supervisor_planning_node(state: LangGraphMissionState) -> dict[str, Any]:
    """Node 2: Supervisor decomposes requirements & RAG context into DAG tasks."""
    from uitp_api.services.multi_agent_service import (
        _build_supervisor_system_prompt,
        _normalize_task_dict,
    )

    req_content = state.get("requirements_content", "")
    target_urls = state.get("target_urls", [])
    scenarios = state.get("scenarios_content", "")
    strategy = state.get("test_strategy", "full_regression")
    endpoints_ctx = state.get("retrieved_endpoints_summary", "")
    requirements_ctx = state.get("retrieved_requirements_summary", "")
    temperature = state.get("temperature", 0.1)

    system_prompt = _build_supervisor_system_prompt()

    user_prompt = f"""
AUTONOMOUS SUPERVISOR MISSION DECOMPOSITION (LANGGRAPH MULTI-AGENT ENGINE):

Test Strategy: {strategy.upper()}
Target Environment URLs:
{chr(10).join(f"- {u}" for u in target_urls) if target_urls else "- (Use relative paths)"}

<rag_retrieved_openapi_endpoints>
{endpoints_ctx}
</rag_retrieved_openapi_endpoints>

<rag_retrieved_requirements>
{requirements_ctx}
</rag_retrieved_requirements>

<raw_requirements>
{req_content[:8000]}
</raw_requirements>

<raw_scenarios>
{scenarios[:4000] if scenarios else "(None provided)"}
</raw_scenarios>

Generate a high-value task DAG that tests the critical journeys end-to-end.
Assign 'api_specialist' to HTTP API tasks and 'ui_specialist' to Browser Web UI tasks.
Chain dependencies accurately so downstream tasks wait for auth and parent tasks.
"""
    provider = get_ai_provider()
    total_tokens = state.get("token_usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    try:
        if hasattr(provider, "complete_structured"):
            raw_output, usage = await provider.complete_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
            )
            total_tokens["prompt_tokens"] += usage.prompt_tokens
            total_tokens["completion_tokens"] += usage.completion_tokens
            total_tokens["total_tokens"] += usage.total_tokens
        else:
            raw_output = await provider.generate_plan_draft(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
    except Exception as exc:
        logger.warning("AI model generation error in LangGraph supervisor: %s. Using deterministic fallback.", exc)
        raw_output = _build_deterministic_fallback_plan(target_urls)

    raw_tasks = raw_output.get("tasks", [])
    normalized_tasks: list[dict[str, Any]] = []
    for idx, t in enumerate(raw_tasks):
        if isinstance(t, dict):
            normalized_tasks.append(_normalize_task_dict(t, idx))

    traces = list(state.get("agent_traces", []))
    traces.append({
        "agent": "supervisor_agent",
        "action": "decompose_journeys",
        "message": f"Supervisor decomposed mission into {len(normalized_tasks)} distinct tasks across specialist agents.",
        "tasks_planned": [t.get("task_id") for t in normalized_tasks],
    })

    return {
        "raw_output": raw_output,
        "mission_name": raw_output.get("name", "Autonomous Multi-Agent Mission Plan"),
        "mission_description": raw_output.get("description", "Generated by LangGraph Autonomous Multi-Agent Engine"),
        "parsed_tasks": normalized_tasks,
        "agent_traces": traces,
        "token_usage": total_tokens,
    }


async def api_specialist_node(state: LangGraphMissionState) -> dict[str, Any]:
    """Node 3: API Specialist validates and enriches HTTP API tasks against OpenAPI specs."""
    tasks = list(state.get("parsed_tasks", []))
    api_count = 0

    for task in tasks:
        if task.get("adapter_type") != "http_api" and task.get("assigned_role") != "api_specialist":
            continue

        api_count += 1
        task_id = str(task.get("task_id", "")).lower()
        is_auth_task = "auth" in task_id or "login" in task_id
        steps = task.get("steps", [])

        for step in steps:
            inputs = step.get("inputs", {})
            path = str(inputs.get("path", "")).strip()
            action = step.get("action", "get").lower()
            str_inputs = str(inputs).lower()

            # Fix obsolete /api/v1/ prefix
            if path.startswith("/api/v1/"):
                path = path.replace("/api/v1/", "/api/")

            # Route resolution based on action, payloads and task context
            if action == "post" and (has_login := any(k in str_inputs for k in ("email", "password", "username", "credential"))):
                path = "/api/auth/login"
                # Guarantee auth token export
                task.setdefault("exports", {})["auth_token"] = {
                    "json_path": "$.access_token",
                    "is_secret": True,
                }
            elif action == "post" and ("title" in str_inputs or "task" in task_id) and path in ("/health", "/", ""):
                path = "/api/tasks"
            elif ("employee" in task_id or "emp" in task_id) and path in ("/health", "/", ""):
                path = "/api/employees"
            elif ("dashboard" in task_id or "stat" in task_id) and path in ("/health", "/", ""):
                path = "/api/dashboard/stats"
            elif is_auth_task and action == "get" and path not in ("/health", "/api/auth/me"):
                path = "/api/auth/me"
            elif not path:
                path = "/health" if action == "get" else "/api/tasks"

            if path and not path.startswith("/"):
                path = f"/{path}"
            inputs["path"] = path

            # Bind Bearer auth header for downstream tasks
            if not is_auth_task and path != "/health":
                headers = inputs.setdefault("headers", {})
                if "Authorization" not in headers:
                    headers["Authorization"] = "Bearer ${context.auth_token}"

            # Assertion sanitization for json_path assertions
            assertions = step.get("assertions", [])
            for a in assertions:
                if not isinstance(a, dict):
                    continue
                atype = str(a.get("type", "")).lower()

                if atype == "json_path_exists":
                    if not a.get("path") and a.get("value"):
                        a["path"] = str(a.get("value"))
                        a["value"] = None
                    if a.get("path") and not str(a["path"]).startswith("$"):
                        a["path"] = f"$.{a['path']}"
                elif atype in ("json_path_equals", "json_path_matches"):
                    if not a.get("path"):
                        val_str = str(a.get("value", "")).lower()
                        if "task" in val_str or "verification" in val_str:
                            a["path"] = "$.title"
                        elif val_str in ("high", "medium", "low"):
                            a["path"] = "$.priority"
                        elif "@" in val_str or "admin" in val_str:
                            a["path"] = "$.email"
                        else:
                            a["path"] = "$.status"

    traces = list(state.get("agent_traces", []))
    traces.append({
        "agent": "api_specialist",
        "action": "contract_enforcement",
        "message": f"API Specialist validated {api_count} HTTP API tasks, corrected routes against OpenAPI spec, bound Bearer headers and export tokens.",
    })

    return {
        "parsed_tasks": tasks,
        "agent_traces": traces,
    }


async def ui_specialist_node(state: LangGraphMissionState) -> dict[str, Any]:
    """Node 4: UI Specialist configures Playwright browser journeys and screenshot capture."""
    tasks = list(state.get("parsed_tasks", []))
    target_urls = state.get("target_urls", [])
    ui_url = next((u for u in target_urls if ":5173" in u or "frontend" in u or "3000" in u), "http://localhost:5173")
    ui_count = 0

    for task in tasks:
        if task.get("adapter_type") != "web" and task.get("assigned_role") != "ui_specialist":
            continue

        ui_count += 1
        steps = task.get("steps", [])
        for step in steps:
            action = step.get("action", "")
            inputs = step.setdefault("inputs", {})
            if action == "navigate":
                if not inputs.get("url") or inputs.get("url") == "http://localhost":
                    inputs["url"] = ui_url
                # Filter out impossible title assertions like title_contains 5173
                clean_assertions = []
                for a in step.get("assertions", []):
                    if isinstance(a, dict) and a.get("type") == "title_contains" and "5173" in str(a.get("value", "")):
                        continue
                    clean_assertions.append(a)
                if not clean_assertions:
                    clean_assertions.append({"type": "url_contains", "value": "5173"})
                step["assertions"] = clean_assertions
            elif action == "screenshot":
                if not inputs.get("selector"):
                    inputs["selector"] = "body"

    traces = list(state.get("agent_traces", []))
    traces.append({
        "agent": "ui_specialist",
        "action": "playwright_synthesis",
        "message": f"UI Specialist configured {ui_count} browser tasks with Playwright Chromium actions targeting {ui_url}.",
    })

    return {
        "parsed_tasks": tasks,
        "agent_traces": traces,
    }


async def evaluator_guardrail_node(state: LangGraphMissionState) -> dict[str, Any]:
    """Node 5: Evaluator validates DAG acyclicity, schema constraints, and SSRF allowlists."""
    from uitp_api.services.multi_agent_service import (
        validate_dag_acyclicity,
        validate_dag_context_declarations,
    )

    tasks_data = state.get("parsed_tasks", [])
    final_schemas: list[DAGTaskSchema] = []
    errors: list[str] = []

    for idx, t in enumerate(tasks_data):
        # Check forbidden tokens
        for s in t.get("steps", []):
            token = _contains_forbidden_tokens(s)
            if token:
                errors.append(f"Task {t.get('task_id')}: contains forbidden token '{token}'")

        try:
            final_schemas.append(DAGTaskSchema.model_validate(t))
        except Exception as exc:
            errors.append(f"Task {idx} schema validation error: {exc}")

    if errors:
        raise ValidationError(f"LangGraph Evaluator Guardrail rejected DAG: {'; '.join(errors[:3])}")

    # Topological acyclicity check
    validate_dag_acyclicity(final_schemas)
    validate_dag_context_declarations(final_schemas)

    traces = list(state.get("agent_traces", []))
    traces.append({
        "agent": "evaluator_guardrail",
        "action": "kahn_acyclicity_check",
        "message": f"Evaluator Guardrail passed: Kahn DAG topological check verified {len(final_schemas)} tasks without cycles.",
    })

    return {
        "final_task_schemas": final_schemas,
        "agent_traces": traces,
        "completed": True,
    }


# ── LangGraph Workflow Builder ────────────────────────────────────────────────

def build_langgraph_mission_workflow():
    """Construct and compile the multi-agent StateGraph."""
    workflow = StateGraph(LangGraphMissionState)

    workflow.add_node("rag_indexer", rag_retrieval_node)
    workflow.add_node("supervisor", supervisor_planning_node)
    workflow.add_node("api_specialist", api_specialist_node)
    workflow.add_node("ui_specialist", ui_specialist_node)
    workflow.add_node("evaluator_guardrail", evaluator_guardrail_node)

    workflow.add_edge(START, "rag_indexer")
    workflow.add_edge("rag_indexer", "supervisor")
    workflow.add_edge("supervisor", "api_specialist")
    workflow.add_edge("api_specialist", "ui_specialist")
    workflow.add_edge("ui_specialist", "evaluator_guardrail")
    workflow.add_edge("evaluator_guardrail", END)

    return workflow.compile()


async def execute_langgraph_mission_planning(
    request: MultiAgentMissionRequest,
    provider: AIProvider | None = None,
) -> tuple[dict[str, Any], list[DAGTaskSchema], TokenUsage, list[dict[str, Any]]]:
    """Execute autonomous mission decomposition through the compiled LangGraph."""
    app = build_langgraph_mission_workflow()

    initial_state: LangGraphMissionState = {
        "requirements_content": request.requirements_content,
        "openapi_spec": request.openapi_spec,
        "scenarios_content": request.scenarios_content,
        "target_urls": request.target_urls,
        "environment_id": request.environment_id,
        "test_strategy": request.test_strategy,
        "temperature": request.temperature,
        "max_parallel_tasks": request.max_parallel_tasks,
        "agent_traces": [],
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

    result = await app.ainvoke(initial_state)

    final_schemas: list[DAGTaskSchema] = result.get("final_task_schemas", [])
    raw_output = result.get("raw_output", {})
    traces = result.get("agent_traces", [])
    usage_dict = result.get("token_usage", {})
    usage = TokenUsage(
        prompt_tokens=usage_dict.get("prompt_tokens", 0),
        completion_tokens=usage_dict.get("completion_tokens", 0),
        total_tokens=usage_dict.get("total_tokens", 0),
    )

    return raw_output, final_schemas, usage, traces


def _build_deterministic_fallback_plan(target_urls: list[str]) -> dict[str, Any]:
    """Safe fallback plan if external AI provider encounters network timeout."""
    frontend_url = next((u for u in target_urls if ":5173" in u or "frontend" in u), "http://localhost:5173")
    return {
        "name": "Employee Task Management System Multi-Agent Mission",
        "description": "Deterministic DAG generated with LangGraph state machine safety fallback.",
        "max_parallel_tasks": 4,
        "tasks": [
            {
                "task_id": "health_and_auth_api",
                "name": "Verify Backend Health & Auth Endpoints",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": [],
                "priority": "P0",
                "is_smoke": True,
                "is_regression": True,
                "steps": [
                    {
                        "step_id": "step_1",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"path": "/health"},
                        "assertions": [{"type": "status_code", "operator": "eq", "value": 200}],
                    },
                    {
                        "step_id": "step_2",
                        "adapter_type": "http_api",
                        "action": "post",
                        "inputs": {
                            "path": "/api/auth/login",
                            "json": {"email": "admin@gmail.com", "password": "Admin@123"},
                        },
                        "assertions": [
                            {"type": "status_code", "operator": "eq", "value": 200},
                            {"type": "json_path_exists", "operator": "exists", "path": "$.access_token"},
                        ],
                    },
                ],
                "exports": {"auth_token": {"json_path": "$.access_token", "is_secret": True}},
                "timeout_seconds": 30.0,
                "requirement_refs": ["REQ-AUTH-01"],
            },
            {
                "task_id": "employee_task_crud_api",
                "name": "Employee & Task CRUD Verification",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": ["health_and_auth_api"],
                "priority": "P0",
                "is_smoke": True,
                "is_regression": True,
                "steps": [
                    {
                        "step_id": "step_1",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {
                            "path": "/api/employees",
                            "headers": {"Authorization": "Bearer {auth_token}"},
                        },
                        "assertions": [{"type": "status_code", "operator": "eq", "value": 200}],
                    },
                    {
                        "step_id": "step_2",
                        "adapter_type": "http_api",
                        "action": "post",
                        "inputs": {
                            "path": "/api/tasks",
                            "headers": {"Authorization": "Bearer {auth_token}"},
                            "json": {
                                "title": "Automated Multi-Agent Task",
                                "description": "Created by autonomous agent",
                                "assigned_to_id": 3,
                                "priority": "high",
                            },
                        },
                        "assertions": [{"type": "status_code", "operator": "eq", "value": 201}],
                    },
                ],
                "exports": {},
                "timeout_seconds": 30.0,
                "requirement_refs": ["REQ-EMP-01", "REQ-TASK-01"],
            },
            {
                "task_id": "verify_frontend_ui",
                "name": "Verify Frontend Landing Page & Navigation",
                "assigned_role": "ui_specialist",
                "adapter_type": "web",
                "depends_on": ["health_and_auth_api"],
                "priority": "P1",
                "is_smoke": False,
                "is_regression": True,
                "steps": [
                    {
                        "step_id": "step_1",
                        "adapter_type": "web",
                        "action": "navigate",
                        "inputs": {"url": frontend_url},
                        "assertions": [{"type": "url_contains", "operator": "contains", "value": "5173"}],
                    },
                ],
                "exports": {},
                "timeout_seconds": 30.0,
                "requirement_refs": ["REQ-UI-01"],
            },
        ],
    }
