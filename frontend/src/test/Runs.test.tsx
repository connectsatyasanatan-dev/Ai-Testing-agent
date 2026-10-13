import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Runs from "@/pages/Runs";
import * as apiClient from "@/lib/api-client";
import type { RunListResponse } from "@/types/run";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Project", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    listPlans: vi.fn().mockResolvedValue({
      items: [
        { id: "pl-1", plan_id: "p1", name: "Smoke Test Plan", version: 1, adapter_type: "http_api" },
        { id: "pl-2", plan_id: "p2", name: "Web UI Login Plan", version: 1, adapter_type: "web" },
      ],
      total: 2,
    }),
    listRuns: vi.fn(),
  };
});

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/projects/proj-1/runs"]}>
        <Routes>
          <Route path="/projects/:projectId/runs" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Runs Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading spinner while runs are loading", () => {
    vi.mocked(apiClient.listRuns).mockImplementation(() => new Promise(() => {}));
    renderWithClient(<Runs />);
    expect(screen.getByText(/Loading test runs…/i)).toBeInTheDocument();
  });

  it("renders error state when listRuns fails", async () => {
    vi.mocked(apiClient.listRuns).mockRejectedValue(new Error("Network connection lost"));
    renderWithClient(<Runs />);
    expect(await screen.findByText(/Failed to retrieve runs/i)).toBeInTheDocument();
    expect(screen.getByText(/Network connection lost/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();
  });

  it("renders empty state when no runs match filter criteria", async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });
    renderWithClient(<Runs />);
    expect(await screen.findByText(/No test runs yet/i)).toBeInTheDocument();
  });

  it("renders populated runs table with status badges and inspect button", async () => {
    const mockRunsData: RunListResponse = {
      items: [
        {
          id: "run-aaa-111",
          plan_id: "p1",
          plan_name: "Smoke Test Plan",
          plan_version: 1,
          environment_id: "env-local",
          project_id: "proj-1",
          status: "passed",
          requested_by: "user-1",
          worker_id: "worker-1",
          idempotency_key: null,
          started_at: "2026-10-13T11:00:00Z",
          finished_at: "2026-10-13T11:00:05Z",
          created_at: "2026-10-13T11:00:00Z",
          adapter_type: "http_api",
          step_attempts: [
            {
              id: "st-1",
              run_id: "run-aaa-111",
              step_id: "step_check_ok",
              attempt_number: 1,
              status: "passed",
              error_code: null,
              error_message: null,
              started_at: "2026-10-13T11:00:00Z",
              finished_at: "2026-10-13T11:00:05Z",
            },
          ],
        },
        {
          id: "run-bbb-222",
          plan_id: "p2",
          plan_name: "Web UI Login Plan",
          plan_version: 2,
          environment_id: "env-local",
          project_id: "proj-1",
          status: "failed",
          requested_by: "user-1",
          worker_id: "worker-1",
          idempotency_key: null,
          started_at: "2026-10-13T11:05:00Z",
          finished_at: "2026-10-13T11:05:10Z",
          created_at: "2026-10-13T11:05:00Z",
          adapter_type: "web",
          step_attempts: [
            {
              id: "st-2",
              run_id: "run-bbb-222",
              step_id: "step_submit_login",
              attempt_number: 1,
              status: "failed",
              error_code: "ASSERTION_FAILED",
              error_message: "Locator '#dashboard' not visible",
              started_at: "2026-10-13T11:05:00Z",
              finished_at: "2026-10-13T11:05:10Z",
            },
          ],
        },
      ],
      total: 2,
      limit: 20,
      offset: 0,
    };

    vi.mocked(apiClient.listRuns).mockResolvedValue(mockRunsData);
    renderWithClient(<Runs />);

    expect(await screen.findAllByText("Smoke Test Plan")).toBeTruthy();
    expect(screen.getAllByText("Web UI Login Plan").length).toBeGreaterThan(0);
    expect(screen.getByText("✓ Passed")).toBeInTheDocument();
    expect(screen.getByText("✕ Failed")).toBeInTheDocument();
    expect(screen.getByText(/1\/1 steps/i)).toBeInTheDocument();
    expect(screen.getByText(/Failed: step_submit_login/i)).toBeInTheDocument();
  });

  it("displays live polling indicator when active in-flight runs exist", async () => {
    const activeRunsData: RunListResponse = {
      items: [
        {
          id: "run-active-999",
          plan_id: "p2",
          plan_name: "Web UI Login Plan",
          plan_version: 1,
          environment_id: "env-local",
          project_id: "proj-1",
          status: "running",
          requested_by: "user-1",
          worker_id: "worker-1",
          idempotency_key: null,
          started_at: "2026-10-13T11:00:00Z",
          finished_at: null,
          created_at: "2026-10-13T11:00:00Z",
          adapter_type: "web",
          step_attempts: [],
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    };

    vi.mocked(apiClient.listRuns).mockResolvedValue(activeRunsData);
    renderWithClient(<Runs />);

    expect(await screen.findByText(/Live polling active/i)).toBeInTheDocument();
  });

  it("filters runs by status when status select changes", async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      items: [],
      total: 0,
      limit: 20,
      offset: 0,
    });

    renderWithClient(<Runs />);
    await screen.findByRole("search");

    const statusSelect = screen.getByLabelText(/Filter by Status/i);
    fireEvent.change(statusSelect, { target: { value: "failed" } });

    expect(apiClient.listRuns).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ status: "failed" })
    );
  });

  it("paginates runs when page size and navigation buttons are clicked", async () => {
    vi.mocked(apiClient.listRuns).mockResolvedValue({
      items: Array.from({ length: 20 }, (_, i) => ({
        id: `run-${i}`,
        plan_id: "p1",
        plan_name: `Plan ${i}`,
        plan_version: 1,
        environment_id: "env",
        project_id: "proj-1",
        status: "passed",
        requested_by: "u",
        worker_id: null,
        idempotency_key: null,
        started_at: null,
        finished_at: null,
        created_at: "2026-10-13T10:00:00Z",
        step_attempts: [],
      })),
      total: 45,
      limit: 20,
      offset: 0,
    });

    renderWithClient(<Runs />);

    expect(await screen.findByText(/Showing 20 of 45 runs \(Page 1 of 3\)/i)).toBeInTheDocument();

    const nextButton = screen.getByRole("button", { name: /Next page/i });
    expect(nextButton).not.toBeDisabled();
    fireEvent.click(nextButton);

    expect(apiClient.listRuns).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ offset: 20 })
    );
  });
});
