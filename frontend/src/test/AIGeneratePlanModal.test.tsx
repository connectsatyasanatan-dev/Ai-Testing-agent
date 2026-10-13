import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AIGeneratePlanModal } from "@/components/plans/AIGeneratePlanModal";
import * as apiClient from "@/lib/api-client";
import { ApiError } from "@/lib/api-client";
import type { GeneratePlanResponse } from "@/types/plan";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    generatePlan: vi.fn(),
    listEnvironments: vi.fn().mockResolvedValue({
      items: [{ id: "env-1", name: "Staging", base_url: "https://staging.api.test" }],
      total: 1,
    }),
  };
});

function renderModal(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("AIGeneratePlanModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders modal form, safety notice, and input controls", () => {
    renderModal(
      <AIGeneratePlanModal
        projectId="proj-1"
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    expect(screen.getByText("✨ AI-Assisted Test Plan Generator")).toBeInTheDocument();
    expect(screen.getByText(/Human Approval Gate Enforced/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Target Adapter/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Testing Objective/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate Draft Plan/i })).toBeInTheDocument();
  });

  it("displays success preview and approval gate requirement when generation succeeds", async () => {
    const mockResponse: GeneratePlanResponse = {
      status: "draft",
      plan_id: "plan-ai-1",
      name: "AI Generated Health Plan",
      version: 1,
      adapter_type: "http_api",
      steps: [
        {
          step_id: "step_1",
          adapter_type: "http_api",
          action: "get",
          inputs: { path: "/health" },
          assertions: [{ type: "status_code", value: 200 }],
          timeout_seconds: 15,
          retry_policy: { max_attempts: 1 },
          idempotency_class: "safe",
          risk_level: "low",
          description: "Verify service health",
        },
      ],
      blocked_steps: [],
      requires_approval: true,
      message: "Draft generated.",
    };

    vi.mocked(apiClient.generatePlan).mockResolvedValueOnce(mockResponse);

    renderModal(
      <AIGeneratePlanModal
        projectId="proj-1"
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    const intentInput = screen.getByLabelText(/Testing Objective/i);
    fireEvent.change(intentInput, { target: { value: "Verify health check endpoint" } });

    const submitBtn = screen.getByRole("button", { name: /Generate Draft Plan/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText("AI Generated Health Plan")).toBeInTheDocument();
    });

    expect(screen.getByText(/Draft \(Unapproved\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Approval Required/i)).toBeInTheDocument();
    expect(screen.getByText("step_1")).toBeInTheDocument();
    expect(screen.getByText("get")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Inspect & Edit in Plan Editor/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /View Plan & Review/i })).toBeInTheDocument();
  });

  it("displays prominent warning when policy blocks dangerous or unallowlisted steps", async () => {
    const mockResponseWithBlocked: GeneratePlanResponse = {
      status: "draft",
      plan_id: "plan-ai-2",
      name: "Partially Sanitized Plan",
      version: 1,
      adapter_type: "http_api",
      steps: [
        {
          step_id: "step_1",
          adapter_type: "http_api",
          action: "get",
          inputs: { path: "/items" },
          assertions: [],
          timeout_seconds: 10,
          retry_policy: { max_attempts: 1 },
          idempotency_class: "safe",
          risk_level: "low",
        },
      ],
      blocked_steps: [
        {
          step_id: "bad_step",
          action: "eval",
          reason: "Step contains forbidden executable code token 'eval('",
          severity: "error",
        },
      ],
      requires_approval: true,
      message: "Draft generated.",
    };

    vi.mocked(apiClient.generatePlan).mockResolvedValueOnce(mockResponseWithBlocked);

    renderModal(
      <AIGeneratePlanModal
        projectId="proj-1"
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    fireEvent.change(screen.getByLabelText(/Testing Objective/i), {
      target: { value: "Run safe and dangerous actions" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Generate Draft Plan/i }));

    await waitFor(() => {
      expect(screen.getByText(/Blocked Steps \(1\)/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Step contains forbidden executable code token 'eval\('/i)).toBeInTheDocument();
  });

  it("displays helpful configuration banner when AI provider is not configured (HTTP 503)", async () => {
    vi.mocked(apiClient.generatePlan).mockRejectedValueOnce(
      new ApiError(503, "AI_PROVIDER_NOT_CONFIGURED", "AI plan generation is not available")
    );

    renderModal(
      <AIGeneratePlanModal
        projectId="proj-1"
        isOpen={true}
        onClose={vi.fn()}
      />
    );

    fireEvent.change(screen.getByLabelText(/Testing Objective/i), {
      target: { value: "Test without configured AI key" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Generate Draft Plan/i }));

    await waitFor(() => {
      expect(screen.getByText(/AI Provider Not Configured/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/Manual test plan authoring remains fully operational/i)).toBeInTheDocument();
  });
});
