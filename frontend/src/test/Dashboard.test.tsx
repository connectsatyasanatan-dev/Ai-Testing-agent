import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "@/pages/Dashboard";
import * as apiClient from "@/lib/api-client";
import type { DashboardMetrics } from "@/types/run";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Project", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    getDashboardMetrics: vi.fn(),
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
      <MemoryRouter initialEntries={["/projects/proj-1"]}>
        <Routes>
          <Route path="/projects/:projectId" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Dashboard Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders loading spinner while metrics are being fetched", () => {
    vi.mocked(apiClient.getDashboardMetrics).mockImplementation(
      () => new Promise(() => {}) // never resolves
    );

    renderWithClient(<Dashboard />);
    expect(screen.getByText(/Loading dashboard metrics…/i)).toBeInTheDocument();
  });

  it("renders error message and retry button when metrics call fails", async () => {
    vi.mocked(apiClient.getDashboardMetrics).mockRejectedValue(
      new Error("Database connection timed out")
    );

    renderWithClient(<Dashboard />);
    expect(await screen.findByText(/Failed to load dashboard metrics/i)).toBeInTheDocument();
    expect(screen.getByText(/Database connection timed out/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/i })).toBeInTheDocument();
  });

  it("renders empty state when project has no runs and no plans", async () => {
    const emptyMetrics: DashboardMetrics = {
      project_id: "proj-1",
      total_plans: 0,
      total_runs: 0,
      status_counts: {
        passed: 0,
        failed: 0,
        running: 0,
        queued: 0,
        blocked: 0,
        inconclusive: 0,
        cancelled: 0,
        skipped: 0,
      },
      adapter_breakdown: {},
      recent_runs: [],
      recent_activity: [],
    };
    vi.mocked(apiClient.getDashboardMetrics).mockResolvedValue(emptyMetrics);

    renderWithClient(<Dashboard />);
    expect(await screen.findByText(/No adapter data yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No execution events yet/i)).toBeInTheDocument();
    expect(screen.getByText(/No test runs yet/i)).toBeInTheDocument();
  });

  it("renders populated dashboard with stats, adapters, and recent runs", async () => {
    const populatedMetrics: DashboardMetrics = {
      project_id: "proj-1",
      total_plans: 3,
      total_runs: 10,
      status_counts: {
        passed: 8,
        failed: 2,
        running: 0,
        queued: 0,
        blocked: 0,
        inconclusive: 0,
        cancelled: 0,
        skipped: 0,
      },
      adapter_breakdown: {
        http_api: 2,
        web: 1,
      },
      recent_runs: [
        {
          id: "run-101",
          plan_id: "p-web",
          plan_name: "Web Checkout Flow",
          plan_version: 1,
          environment_id: "env-staging",
          project_id: "proj-1",
          status: "passed",
          requested_by: "user-1",
          worker_id: "worker-playwright",
          idempotency_key: null,
          started_at: "2026-10-13T10:00:00Z",
          finished_at: "2026-10-13T10:00:15Z",
          created_at: "2026-10-13T10:00:00Z",
          adapter_type: "web",
          step_attempts: [],
        },
      ],
      recent_activity: [
        {
          id: "event-1",
          run_id: "run-101",
          step_id: null,
          attempt_id: null,
          event_type: "run_passed",
          timestamp: "2026-10-13T10:00:15Z",
          message: "Run completed with status passed",
          event_metadata: null,
        },
      ],
    };
    vi.mocked(apiClient.getDashboardMetrics).mockResolvedValue(populatedMetrics);

    renderWithClient(<Dashboard />);

    // Stat cards
    expect(await screen.findByText("3")).toBeInTheDocument(); // total plans
    expect(screen.getByText("10")).toBeInTheDocument(); // total runs
    expect(screen.getByText("8")).toBeInTheDocument(); // passed
    expect(screen.getByText("2")).toBeInTheDocument(); // failed

    // Pass rate banner
    expect(screen.getByText(/80% Passed/i)).toBeInTheDocument();

    // Adapter breakdown
    expect(screen.getByText(/Web \(Playwright\)/i)).toBeInTheDocument();
    expect(screen.getByText(/HTTP API/i)).toBeInTheDocument();

    // Recent activity
    expect(screen.getByText(/Run completed with status passed/i)).toBeInTheDocument();

    // Recent run in table
    expect(screen.getByText(/Web Checkout Flow/i)).toBeInTheDocument();
    expect(screen.getByText("✓ Passed")).toBeInTheDocument();
  });
});
