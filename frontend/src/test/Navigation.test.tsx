import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import RunDetail from "@/pages/RunDetail";
import * as apiClient from "@/lib/api-client";
import type { EvidenceArtifact, Run, RunEvent } from "@/types/run";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [
        { id: "proj-1", name: "Project Alpha", status: "active", owner_id: "u1" },
        { id: "proj-2", name: "Project Beta", status: "active", owner_id: "u1" },
      ],
      total: 2,
    }),
    getRun: vi.fn(),
    getRunEvents: vi.fn(),
    listRunArtifacts: vi.fn(),
    getRunArtifact: vi.fn(),
    getArtifactDownloadUrl: vi.fn((runId: string, artId: string) => `/api/v1/runs/${runId}/artifacts/${artId}?download=true`),
  };
});

function renderWithClient(ui: React.ReactElement, initialPath = "/runs/run-123") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/runs/:runId" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("RunDetail Page and Evidence Viewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockRun: Run = {
    id: "run-123",
    plan_id: "plan-checkout",
    plan_name: "Checkout Smoke Plan",
    plan_version: 3,
    environment_id: "env-production",
    environment_name: "Production API",
    project_id: "proj-1",
    status: "passed",
    requested_by: "user-test",
    worker_id: "worker-rq-01",
    idempotency_key: "idemp-key-99",
    started_at: "2026-10-13T12:00:00Z",
    finished_at: "2026-10-13T12:00:08Z",
    created_at: "2026-10-13T12:00:00Z",
    adapter_type: "web",
    step_attempts: [
      {
        id: "attempt-1",
        run_id: "run-123",
        step_id: "open_cart",
        attempt_number: 1,
        status: "passed",
        action: "navigate",
        duration_ms: 4000,
        error_code: null,
        error_message: null,
        started_at: "2026-10-13T12:00:00Z",
        finished_at: "2026-10-13T12:00:04Z",
      },
      {
        id: "attempt-2",
        run_id: "run-123",
        step_id: "checkout_submit",
        attempt_number: 1,
        status: "passed",
        action: "click",
        duration_ms: 4000,
        error_code: null,
        error_message: null,
        started_at: "2026-10-13T12:00:04Z",
        finished_at: "2026-10-13T12:00:08Z",
      },
    ],
  };

  const mockEvents: RunEvent[] = [
    {
      id: "ev-1",
      run_id: "run-123",
      step_id: null,
      attempt_id: null,
      event_type: "run_started",
      timestamp: "2026-10-13T12:00:00Z",
      message: "Run started for plan 'Checkout Smoke Plan' v3",
      event_metadata: null,
    },
    {
      id: "ev-2",
      run_id: "run-123",
      step_id: "open_cart",
      attempt_id: "attempt-1",
      event_type: "step_passed",
      timestamp: "2026-10-13T12:00:04Z",
      message: "Step 'open_cart' passed",
      event_metadata: null,
    },
  ];

  const mockArtifacts: EvidenceArtifact[] = [
    {
      id: "art-1",
      run_id: "run-123",
      step_attempt_id: "attempt-1",
      type: "screenshot",
      content_type: "image/png",
      checksum: "sha256-abc1234",
      redaction_status: "not_required",
      size_bytes: 45056,
      created_at: "2026-10-13T12:00:04Z",
      retention_until: "2026-11-13T12:00:04Z",
      is_expired: false,
    },
    {
      id: "art-2",
      run_id: "run-123",
      step_attempt_id: "attempt-2",
      type: "response_log",
      content_type: "application/json",
      checksum: "sha256-def5678",
      redaction_status: "redacted",
      size_bytes: 1024,
      created_at: "2026-10-13T12:00:08Z",
      retention_until: "2026-11-13T12:00:08Z",
      is_expired: false,
    },
  ];

  it("renders run detail metadata, step attempts, artifacts, and timeline events", async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(mockRun);
    vi.mocked(apiClient.getRunEvents).mockResolvedValue(mockEvents);
    vi.mocked(apiClient.listRunArtifacts).mockResolvedValue(mockArtifacts);

    renderWithClient(<RunDetail />);

    expect(await screen.findByText("run-123")).toBeInTheDocument();
    expect(screen.getByText("Checkout Smoke Plan (v3)")).toBeInTheDocument();
    expect(screen.getByText("Production API")).toBeInTheDocument();
    expect(screen.getByText("Worker (worker-rq-01)")).toBeInTheDocument();

    // Step attempts and actions
    expect(screen.getByText("open_cart")).toBeInTheDocument();
    expect(screen.getByText("checkout_submit")).toBeInTheDocument();
    expect(screen.getByText("navigate")).toBeInTheDocument();
    expect(screen.getByText("click")).toBeInTheDocument();

    // Timeline events
    expect(screen.getByText("run_started")).toBeInTheDocument();
    expect(screen.getByText("step_passed")).toBeInTheDocument();

    // Captured evidence table
    expect(screen.getByText("Captured Evidence & Artifacts (2)")).toBeInTheDocument();
    expect(screen.getByText(/Screenshot/i)).toBeInTheDocument();
    expect(screen.getByText(/Response Log/i)).toBeInTheDocument();
  });

  it("distinguishes failed assertions, timeouts, and execution errors with clear badges", async () => {
    const failedRun: Run = {
      id: "run-fail-1",
      plan_id: "p-fail",
      plan_name: "Failing Step Plan",
      plan_version: 1,
      environment_id: "env-local",
      project_id: "proj-1",
      status: "failed",
      requested_by: "user-1",
      worker_id: null,
      idempotency_key: null,
      started_at: "2026-10-13T12:00:00Z",
      finished_at: "2026-10-13T12:00:02Z",
      created_at: "2026-10-13T12:00:00Z",
      step_attempts: [
        {
          id: "att-assert",
          run_id: "run-fail-1",
          step_id: "step_assert_heading",
          attempt_number: 1,
          status: "failed",
          action: "get",
          error_code: "ASSERTION_FAILED",
          error_message: "Expected status 200 but received 404",
          failure_category: "assertion_failure",
          assertions: [{ type: "status_code", value: 200, passed: false }],
          started_at: "2026-10-13T12:00:00Z",
          finished_at: "2026-10-13T12:00:01Z",
        },
        {
          id: "att-timeout",
          run_id: "run-fail-1",
          step_id: "step_slow_api",
          attempt_number: 1,
          status: "failed",
          action: "get",
          error_code: "TIMEOUT",
          error_message: "Request exceeded timeout limit of 10s",
          failure_category: "timeout",
          started_at: "2026-10-13T12:00:01Z",
          finished_at: "2026-10-13T12:00:02Z",
        },
      ],
    };

    vi.mocked(apiClient.getRun).mockResolvedValue(failedRun);
    vi.mocked(apiClient.getRunEvents).mockResolvedValue([]);
    vi.mocked(apiClient.listRunArtifacts).mockResolvedValue([]);

    renderWithClient(<RunDetail />, "/runs/run-fail-1");

    expect(await screen.findByText(/Assertion Failure/i)).toBeInTheDocument();
    expect(screen.getByText(/Timeout Exceeded/i)).toBeInTheDocument();
    expect(screen.getByText("status_code: 200")).toBeInTheDocument();
    expect(screen.getByText(/Expected status 200 but received 404/i)).toBeInTheDocument();
  });

  it("opens evidence preview modal and renders structured response viewer", async () => {
    vi.mocked(apiClient.getRun).mockResolvedValue(mockRun);
    vi.mocked(apiClient.getRunEvents).mockResolvedValue([]);
    vi.mocked(apiClient.listRunArtifacts).mockResolvedValue(mockArtifacts);

    const rawResponse = {
      step_id: "checkout_submit",
      request: { method: "POST", url: "https://api.example.com/checkout", headers: { Authorization: "[REDACTED]" } },
      response: { status_code: 200, duration_ms: 154, body: { order_id: "ord-99" } },
      assertions: [{ type: "status_code", passed: true }],
    };
    const b64Json = btoa(JSON.stringify(rawResponse));

    vi.mocked(apiClient.getRunArtifact).mockResolvedValue({
      artifact: mockArtifacts[1],
      inline_content: b64Json,
    });

    renderWithClient(<RunDetail />);

    expect(await screen.findByText("Checkout Smoke Plan (v3)")).toBeInTheDocument();

    // Click Preview on second artifact
    const previewButtons = await screen.findAllByRole("button", { name: /Preview/i });
    fireEvent.click(previewButtons[1]);

    // Modal opens
    expect(await screen.findByRole("dialog", { name: /Evidence Artifact Viewer/i })).toBeInTheDocument();
    expect(screen.getByText(/Safe Download/i)).toBeInTheDocument();
    expect(await screen.findByText(/ord-99/i)).toBeInTheDocument();

    // Close modal
    fireEvent.click(screen.getByRole("button", { name: /Close/i }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders expired-artifact notice when an artifact retention has passed", async () => {
    const expiredArtifact: EvidenceArtifact = {
      id: "art-expired",
      run_id: "run-123",
      type: "response_log",
      content_type: "application/json",
      redaction_status: "redacted",
      size_bytes: 1024,
      created_at: "2026-09-01T10:00:00Z",
      retention_until: "2026-10-01T10:00:00Z",
      is_expired: true,
    };

    vi.mocked(apiClient.getRun).mockResolvedValue(mockRun);
    vi.mocked(apiClient.getRunEvents).mockResolvedValue([]);
    vi.mocked(apiClient.listRunArtifacts).mockResolvedValue([expiredArtifact]);

    renderWithClient(<RunDetail />);

    expect(await screen.findByText(/Expired/i)).toBeInTheDocument();
    expect(screen.getByText("Purged")).toBeInTheDocument();
  });
});
