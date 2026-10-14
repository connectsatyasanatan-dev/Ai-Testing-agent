import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MissionPlanner from "@/pages/MissionPlanner";
import MissionDetail from "@/pages/MissionDetail";
import { DAGView } from "@/components/missions/DAGView";
import { MissionEvaluationView } from "@/components/missions/MissionEvaluationView";
import * as apiClient from "@/lib/api-client";
import type { MissionDAGPlan, MissionEvaluationReport } from "@/types/mission";
import type { Plan } from "@/types/plan";
import type { Run } from "@/types/run";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Project", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    listEnvironments: vi.fn().mockResolvedValue({
      items: [
        { id: "env-staging", project_id: "proj-1", name: "Staging", base_url: "https://staging.test.local" },
        { id: "env-prod", project_id: "proj-1", name: "Production", base_url: "https://app.test.local" },
      ],
      total: 2,
    }),
    getCurrentUser: vi.fn().mockResolvedValue({
      id: "u1",
      email: "qa.lead@enterprise.local",
      full_name: "QA Lead",
      is_active: true,
    }),
    planMission: vi.fn(),
    getMissionDAG: vi.fn(),
    approveMission: vi.fn(),
    getPlan: vi.fn(),
    createRun: vi.fn(),
    getRunEvaluation: vi.fn(),
    getRunReport: vi.fn(),
    getRunExecutionLog: vi.fn(),
  };
});

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
}

const mockDAGPlan: MissionDAGPlan = {
  mission_id: "mission-dag-456",
  name: "Autonomous E2E Checkout Mission",
  description: "Validates user checkout, inventory reservation, and payment processing.",
  max_parallel_tasks: 4,
  requires_approval: true,
  tasks: [
    {
      task_id: "task-auth-01",
      name: "Authenticate Test Operator",
      assigned_role: "api_specialist",
      adapter_type: "http_api",
      priority: "P0",
      is_smoke: true,
      is_regression: true,
      depends_on: [],
      requirement_refs: ["REQ-AUTH-01"],
      steps: [{ action: "http_request" }, { action: "assert_status" }],
      timeout_seconds: 60,
      exports: {
        token: { json_path: "$.token", is_secret: true },
      },
    },
    {
      task_id: "task-ui-checkout",
      name: "Complete Cart Checkout Flow",
      assigned_role: "ui_tester",
      adapter_type: "web",
      priority: "P0",
      is_smoke: true,
      is_regression: false,
      depends_on: ["task-auth-01"],
      requirement_refs: ["REQ-CHECKOUT-01"],
      steps: [{ action: "navigate" }, { action: "click" }, { action: "assert_text" }],
      timeout_seconds: 120,
    },
    {
      task_id: "task-eval-audit",
      name: "Audit Post-Run Metrics",
      assigned_role: "evaluator",
      adapter_type: "http_api",
      priority: "P2",
      is_smoke: false,
      is_regression: true,
      depends_on: ["task-ui-checkout"],
      requirement_refs: ["REQ-AUDIT-02"],
      steps: [{ action: "http_request" }],
      timeout_seconds: 30,
    },
  ],
};

const mockPlanDraft = {
  id: "p-456",
  plan_id: "mission-dag-456",
  project_id: "proj-1",
  name: "Autonomous E2E Checkout Mission",
  description: "Validates user checkout, inventory reservation, and payment processing.",
  status: "draft",
  version: 1,
  adapter_type: "multi_agent_dag",
  steps: [],
  requires_approval: true,
  approved_by: null,
  approved_at: null,
  created_by: "system",
  created_at: "2026-10-14T08:00:00Z",
  updated_at: "2026-10-14T08:00:00Z",
} as unknown as Plan;

const mockPlanApproved = {
  ...mockPlanDraft,
  status: "approved",
  approved_by: "qa.lead@enterprise.local",
  approved_at: "2026-10-14T09:00:00Z",
} as unknown as Plan;

const mockEvaluation: MissionEvaluationReport = {
  mission_id: "mission-dag-456",
  run_id: "run-dag-789",
  verdict: "passed",
  evaluated_at: "2026-10-14T10:00:00Z",
  total_tasks: 3,
  passed_tasks: 3,
  failed_tasks: 0,
  blocked_tasks: 0,
  skipped_tasks: 0,
  cancelled_tasks: 0,
  inconclusive_tasks: 0,
  executive_summary: "All 3 critical journey tests completed without assertion errors.",
  requirement_coverage: [
    {
      requirement_id: "REQ-AUTH-01",
      status: "PASSED",
      tested_by_tasks: ["task-auth-01"],
      notes: "Authenticated with status 200",
    },
    {
      requirement_id: "REQ-CHECKOUT-01",
      status: "PASSED",
      tested_by_tasks: ["task-ui-checkout"],
      notes: "Cart checkout completed within threshold",
    },
  ],
  root_cause_analysis: [],
};

describe("TASK 9: Mission Planning, DAG Review, Approval and Monitoring", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("MissionPlanner (Creation Form & Validation)", () => {
    it("renders mission planning form with input controls and upload zones", () => {
      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/new"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/new" element={<MissionPlanner />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      expect(screen.getByRole("heading", { name: /Plan Autonomous Multi-Agent Mission/i })).toBeInTheDocument();
      expect(screen.getByLabelText(/Upload requirements\.md file/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Upload openapi\.json file/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Upload scenarios\.json file/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Target URL 1/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Environment\*/i)).toBeInTheDocument();
    });

    it("rejects file uploads exceeding maximum client size limits", async () => {
      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/new"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/new" element={<MissionPlanner />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Create an oversized file > 64KB for requirements.md
      const oversizedBlob = new Uint8Array(65 * 1024);
      const oversizedFile = new File([oversizedBlob], "requirements.md", { type: "text/markdown" });

      const fileInput = screen.getByLabelText(/Upload requirements\.md file/i);
      fireEvent.change(fileInput, { target: { files: [oversizedFile] } });

      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(/exceeds maximum size limit/i);
      });
    });

    it("rejects invalid file extension types", async () => {
      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/new"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/new" element={<MissionPlanner />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      const invalidFile = new File(["binary-exe"], "malware.exe", { type: "application/x-msdownload" });
      const fileInput = screen.getByLabelText(/Upload requirements\.md file/i);
      fireEvent.change(fileInput, { target: { files: [invalidFile] } });

      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(/invalid file type/i);
      });
    });

    it("validates required fields before sending plan mission request", async () => {
      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/new"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/new" element={<MissionPlanner />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Wait for environments to load
      await waitFor(() => {
        expect(screen.getByDisplayValue(/Staging/i)).toBeInTheDocument();
      });

      const submitBtn = screen.getByRole("button", { name: /Decompose Mission DAG →/i });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(screen.getByRole("alert")).toHaveTextContent(/Requirements specification content cannot be empty/i);
      });
    });

    it("submits valid mission configuration and triggers planMission API", async () => {
      vi.mocked(apiClient.planMission).mockResolvedValueOnce({
        mission_id: "mission-dag-456",
        name: "Order Processing Mission",
        description: "Decomposed test plan",
        tasks: [],
      } as unknown as MissionDAGPlan);

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/new"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/new" element={<MissionPlanner />} />
              <Route path="/projects/:projectId/missions/:missionId" element={<div>DAG Review Page</div>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      // Wait for environments to load
      await waitFor(() => {
        expect(screen.getByDisplayValue(/Staging/i)).toBeInTheDocument();
      });

      // Fill valid requirements
      const reqTextarea = screen.getByLabelText(/Requirements Content/i);
      fireEvent.change(reqTextarea, {
        target: { value: "# Requirements\nREQ-01: Critical checkout path." },
      });

      // Fill valid target URL
      const targetUrlInput = screen.getByLabelText(/Target URL 1/i);
      fireEvent.change(targetUrlInput, {
        target: { value: "https://staging.example.com" },
      });

      const submitBtn = screen.getByRole("button", { name: /Decompose Mission DAG →/i });
      fireEvent.click(submitBtn);

      await waitFor(() => {
        expect(apiClient.planMission).toHaveBeenCalledTimes(1);
        expect(apiClient.planMission).toHaveBeenCalledWith(
          "proj-1",
          expect.objectContaining({
            requirements_content: "# Requirements\nREQ-01: Critical checkout path.",
            target_urls: ["https://staging.example.com"],
          })
        );
      });
    });
  });

  describe("MissionDetail & DAGView (Draft Review, Approval & Execution Dispatch)", () => {
    it("renders mission DAG tasks, roles, priority badges and dependency edges", async () => {
      vi.mocked(apiClient.getMissionDAG).mockResolvedValue(mockDAGPlan);
      vi.mocked(apiClient.getPlan).mockResolvedValue(mockPlanDraft);

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/mission-dag-456"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/:missionId" element={<MissionDetail />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      await waitFor(() => {
        expect(screen.getByText("Autonomous E2E Checkout Mission")).toBeInTheDocument();
      });

      // Verify draft gate banner and action button
      expect(screen.getByText(/Draft Mission Plan:/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Approve Mission DAG/i })).toBeInTheDocument();

      // Verify task cards rendered with roles and priorities
      expect(screen.getAllByText("task-auth-01").length).toBeGreaterThan(0);
      expect(screen.getAllByText("task-ui-checkout").length).toBeGreaterThan(0);
      expect(screen.getAllByText("task-eval-audit").length).toBeGreaterThan(0);

      // Verify priority badges
      expect(screen.getAllByText("P0").length).toBeGreaterThan(0);
      expect(screen.getByText("P2")).toBeInTheDocument();

      // Verify dependencies badge
      expect(screen.getByText("Root Task (None)")).toBeInTheDocument();
    });

    it("triggers server approval action when operator approves draft mission", async () => {
      vi.mocked(apiClient.getMissionDAG).mockResolvedValue(mockDAGPlan);
      vi.mocked(apiClient.getPlan).mockResolvedValue(mockPlanDraft);
      vi.mocked(apiClient.approveMission).mockResolvedValueOnce({
        mission_id: "mission-dag-456",
        status: "approved",
        approved_at: "2026-10-14T10:10:00Z",
      });

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/mission-dag-456"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/:missionId" element={<MissionDetail />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Approve Mission DAG/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: /Approve Mission DAG/i }));

      await waitFor(() => {
        expect(apiClient.approveMission).toHaveBeenCalledWith(
          "mission-dag-456",
          "Approved via Web Console"
        );
      });
    });

    it("displays execution modal for approved mission with suite filtering (smoke, regression, all)", async () => {
      vi.mocked(apiClient.getMissionDAG).mockResolvedValue(mockDAGPlan);
      vi.mocked(apiClient.getPlan).mockResolvedValue(mockPlanApproved);
      vi.mocked(apiClient.createRun).mockResolvedValueOnce({
        id: "run-dag-789",
        plan_id: "mission-dag-456",
        plan_name: "Autonomous E2E Checkout Mission",
        plan_version: 1,
        project_id: "proj-1",
        environment_id: "env-staging",
        status: "queued",
        created_at: "2026-10-14T10:15:00Z",
      } as unknown as Run);

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MemoryRouter initialEntries={["/projects/proj-1/missions/mission-dag-456"]}>
            <Routes>
              <Route path="/projects/:projectId/missions/:missionId" element={<MissionDetail />} />
              <Route path="/runs/:runId" element={<div>Run Monitor Page</div>} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>
      );

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Execute Test Run\.\.\./i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: /Execute Test Run\.\.\./i }));

      // Wait for modal to render
      await waitFor(() => {
        expect(screen.getByText("Execute Mission Test Run")).toBeInTheDocument();
      });

      expect(screen.getByText("Test Suite Selection:")).toBeInTheDocument();

      // Select Smoke Suite option
      const smokeOption = screen.getByText("Smoke Only (Critical P0 Journeys)");
      fireEvent.click(smokeOption);

      // Click Confirm Dispatch ("Launch Run →")
      const dispatchBtn = screen.getByRole("button", { name: /Launch Run →/i });
      fireEvent.click(dispatchBtn);

      await waitFor(() => {
        expect(apiClient.createRun).toHaveBeenCalledWith("mission-dag-456", {
          environment_id: "env-staging",
          suite_type: "smoke",
        });
      });
    });

    it("filters tasks interactively inside DAGView by role and query text", () => {
      render(<DAGView plan={mockDAGPlan} />);

      expect(screen.getAllByText("task-auth-01").length).toBeGreaterThan(0);
      expect(screen.getAllByText("task-ui-checkout").length).toBeGreaterThan(0);
      expect(screen.getAllByText("task-eval-audit").length).toBeGreaterThan(0);

      // Search for 'checkout'
      const searchInput = screen.getByLabelText("Filter tasks");
      fireEvent.change(searchInput, { target: { value: "checkout" } });

      expect(screen.getAllByText("task-ui-checkout").length).toBeGreaterThan(0);
      expect(screen.queryByText("task-eval-audit")).not.toBeInTheDocument();
    });
  });

  describe("MissionEvaluationView (Post-Run Evaluation & Report Deliverables)", () => {
    it("renders evaluation verdict badge, task breakdown metrics, and requirement traceability matrix", () => {
      render(
        <QueryClientProvider client={createQueryClient()}>
          <MissionEvaluationView runId="run-dag-789" evaluation={mockEvaluation} />
        </QueryClientProvider>
      );

      expect(screen.getAllByText(/PASSED/i).length).toBeGreaterThan(0);
      expect(screen.getByText("All 3 critical journey tests completed without assertion errors.")).toBeInTheDocument();
      expect(screen.getByText("REQ-AUTH-01")).toBeInTheDocument();
      expect(screen.getByText("REQ-CHECKOUT-01")).toBeInTheDocument();
      expect(screen.getByText("Authenticated with status 200")).toBeInTheDocument();
    });

    it("opens and displays the markdown report preview modal", async () => {
      vi.mocked(apiClient.getRunReport).mockResolvedValueOnce("# Mission Execution Report\n\nAll tests passed successfully.");

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MissionEvaluationView runId="run-dag-789" evaluation={mockEvaluation} />
        </QueryClientProvider>
      );

      const previewBtn = screen.getByRole("button", { name: /View report\.md/i });
      fireEvent.click(previewBtn);

      await waitFor(() => {
        expect(apiClient.getRunReport).toHaveBeenCalledWith("run-dag-789");
        expect(screen.getByText(/All tests passed successfully\./i)).toBeInTheDocument();
      });
    });

    it("opens and displays the execution log JSON preview modal", async () => {
      vi.mocked(apiClient.getRunExecutionLog).mockResolvedValueOnce({
        schema_version: "1.0",
        run_id: "run-dag-789",
        status: "passed",
        tasks: [{ id: "task-auth-01", status: "passed" }],
      });

      render(
        <QueryClientProvider client={createQueryClient()}>
          <MissionEvaluationView runId="run-dag-789" evaluation={mockEvaluation} />
        </QueryClientProvider>
      );

      const previewBtn = screen.getByRole("button", { name: /View execution_log\.json/i });
      fireEvent.click(previewBtn);

      await waitFor(() => {
        expect(apiClient.getRunExecutionLog).toHaveBeenCalledWith("run-dag-789");
        expect(screen.getByText(/"schema_version": "1.0"/i)).toBeInTheDocument();
      });
    });
  });
});
