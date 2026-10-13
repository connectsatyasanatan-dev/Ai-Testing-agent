import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Plans from "@/pages/Plans";
import PlanDetail from "@/pages/PlanDetail";
import * as apiClient from "@/lib/api-client";
import type { PlanListResponse } from "@/types/plan";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Project", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    listPlans: vi.fn(),
    getPlan: vi.fn(),
    getPlanVersion: vi.fn(),
    listPlanVersions: vi.fn(),
    approvePlan: vi.fn(),
    submitPlanReview: vi.fn(),
    rejectPlan: vi.fn(),
    archivePlan: vi.fn(),
  };
});

function renderWithClient(ui: React.ReactElement, initialRoute = "/projects/proj-1/plans") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialRoute]}>
        <Routes>
          <Route path="/projects/:projectId/plans/:planId" element={ui} />
          <Route path="/projects/:projectId/plans" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Plans Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockPlansResponse: PlanListResponse = {
    items: [
      {
        id: "p1-row",
        plan_id: "plan-1",
        project_id: "proj-1",
        name: "Login Smoke Test",
        version: 1,
        adapter_type: "http_api",
        status: "approved",
        description: "Verify login endpoint returns 200",
        requires_approval: true,
        created_by: "user-1",
        approved_by: "user-2",
        approved_at: "2026-10-12T10:00:00Z",
        created_at: "2026-10-12T09:00:00Z",
        updated_at: "2026-10-12T10:00:00Z",
        steps: [],
      },
      {
        id: "p2-row",
        plan_id: "plan-2",
        project_id: "proj-1",
        name: "Web Checkout Flow",
        version: 2,
        adapter_type: "web",
        status: "draft",
        description: "Browser test with Playwright",
        requires_approval: true,
        created_by: "user-1",
        approved_by: null,
        approved_at: null,
        created_at: "2026-10-13T08:00:00Z",
        updated_at: "2026-10-13T08:00:00Z",
        steps: [],
      },
    ],
    total: 2,
  };

  it("renders plan list items with adapter and version badges", async () => {
    vi.mocked(apiClient.listPlans).mockResolvedValue(mockPlansResponse);
    renderWithClient(<Plans />);

    expect(await screen.findByText("Login Smoke Test")).toBeInTheDocument();
    expect(screen.getByText("Web Checkout Flow")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText(/⚡ HTTP API/i)).toBeInTheDocument();
    expect(screen.getByText(/🌐 Web \(Playwright\)/i)).toBeInTheDocument();
  });

  it("filters plans when status filter button is clicked", async () => {
    vi.mocked(apiClient.listPlans).mockResolvedValue(mockPlansResponse);
    renderWithClient(<Plans />);

    await screen.findByText("Login Smoke Test");
    const draftFilter = screen.getByRole("tab", { name: "Draft" });
    fireEvent.click(draftFilter);

    expect(apiClient.listPlans).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ status: "draft" })
    );
  });

  it("renders empty state when no plans match", async () => {
    vi.mocked(apiClient.listPlans).mockResolvedValue({ items: [], total: 0 });
    renderWithClient(<Plans />);

    expect(await screen.findByText("No test plans found")).toBeInTheDocument();
    expect(screen.getByText("Create Plan")).toBeInTheDocument();
  });
});

describe("PlanDetail Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockPlanDetail = {
    id: "p1-row",
    plan_id: "plan-1",
    project_id: "proj-1",
    name: "Detailed Plan",
    version: 1,
    adapter_type: "http_api",
    status: "pending_review" as const,
    description: "Full plan under review",
    requires_approval: true,
    created_by: "user-1",
    approved_by: null,
    approved_at: null,
    created_at: "2026-10-12T09:00:00Z",
    updated_at: "2026-10-12T09:00:00Z",
    steps: [
      {
        step_id: "step_login",
        step_order: 1,
        adapter_type: "http_api",
        action: "post",
        inputs: { path: "/api/login", method: "POST" },
        assertions: [{ type: "status_code" as const, value: 200 }],
        timeout_seconds: 30,
        retry_policy: { max_attempts: 1 },
        idempotency_class: "safe" as const,
        risk_level: "low" as const,
        description: "Submit credentials",
      },
    ],
  };

  it("renders review and approve action buttons for pending_review plans", async () => {
    vi.mocked(apiClient.getPlan).mockResolvedValue(mockPlanDetail);
    vi.mocked(apiClient.getPlanVersion).mockResolvedValue(mockPlanDetail);
    vi.mocked(apiClient.listPlanVersions).mockResolvedValue({
      plan_id: "plan-1",
      versions: [{ version: 1, status: "pending_review", created_at: "2026-10-12T09:00:00Z" }],
    });

    renderWithClient(<PlanDetail />, "/projects/proj-1/plans/plan-1");

    expect(await screen.findByText("Detailed Plan")).toBeInTheDocument();
    expect(screen.getByText(/Review Pending/i)).toBeInTheDocument();
    expect(screen.getByText("step_login")).toBeInTheDocument();

    // Check approval and reject buttons are visible
    expect(screen.getByRole("button", { name: /Approve Plan/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reject/i })).toBeInTheDocument();
  });
});
