import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlanEditor from "@/pages/PlanEditor";
import * as apiClient from "@/lib/api-client";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Project", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    getPlan: vi.fn(),
    createPlan: vi.fn(),
    updatePlan: vi.fn(),
    validateProjectPlan: vi.fn(),
    validatePlan: vi.fn(),
  };
});

function renderWithClient(ui: React.ReactElement, initialRoute = "/projects/proj-1/plans/new") {
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
          <Route path="/projects/:projectId/plans/new" element={ui} />
          <Route path="/projects/:projectId/plans/:planId/edit" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("PlanEditor Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders plan creation form with default first step", () => {
    renderWithClient(<PlanEditor />);

    expect(screen.getByText("Create New Test Plan")).toBeInTheDocument();
    expect(screen.getByLabelText(/Plan Name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Adapter Target/i)).toBeInTheDocument();
    expect(screen.getByText(/Test Steps \(1\)/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("step_1")).toBeInTheDocument();
  });

  it("allows adding a new step", () => {
    renderWithClient(<PlanEditor />);

    const addStepBtn = screen.getByRole("button", { name: /Add Step/i });
    fireEvent.click(addStepBtn);

    expect(screen.getByText(/Test Steps \(2\)/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("step_2")).toBeInTheDocument();
  });

  it("triggers server-side validation and displays feedback", async () => {
    vi.mocked(apiClient.validateProjectPlan).mockResolvedValue({
      valid: false,
      errors: ["Duplicate step identifier 'step_1'."],
      warnings: ["Non-idempotent step without retry override."],
      issues: [
        {
          field: "step_id",
          step_id: "step_1",
          message: "Duplicate step identifier 'step_1'.",
          severity: "error",
        },
      ],
    });

    renderWithClient(<PlanEditor />);

    const validateBtn = screen.getByRole("button", { name: /Validate Plan/i });
    fireEvent.click(validateBtn);

    expect(await screen.findByText(/Validation found 1 error/i)).toBeInTheDocument();
    expect(screen.getByText("Duplicate step identifier 'step_1'.")).toBeInTheDocument();
  });

  it("shows immutability warning banner when editing an approved plan", async () => {
    vi.mocked(apiClient.getPlan).mockResolvedValue({
      id: "p1-row",
      plan_id: "plan-1",
      project_id: "proj-1",
      name: "Approved Core Plan",
      version: 1,
      adapter_type: "http_api",
      status: "approved" as const,
      description: "Immutable baseline",
      requires_approval: true,
      created_by: "user-1",
      approved_by: "user-2",
      approved_at: "2026-10-12T10:00:00Z",
      created_at: "2026-10-12T09:00:00Z",
      updated_at: "2026-10-12T10:00:00Z",
      steps: [
        {
          step_id: "step_1",
          step_order: 1,
          adapter_type: "http_api",
          action: "get",
          inputs: { path: "/health" },
          assertions: [],
          timeout_seconds: 30,
          retry_policy: { max_attempts: 1 },
          idempotency_class: "safe" as const,
          risk_level: "low" as const,
          description: "",
        },
      ],
    });

    renderWithClient(<PlanEditor />, "/projects/proj-1/plans/plan-1/edit");

    expect(await screen.findByText(/Approved Plan Immutability:/i)).toBeInTheDocument();
    expect(
      screen.getByText(/Saving your changes will automatically create a new draft version/i)
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save as New Version/i })).toBeInTheDocument();
  });
});
