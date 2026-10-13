import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Settings from "@/pages/Settings";
import * as apiClient from "@/lib/api-client";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({ status: "ok", version: "0.1.0" }),
    listProjects: vi.fn().mockResolvedValue({
      items: [{ id: "proj-1", name: "Alpha Production", status: "active", owner_id: "u1" }],
      total: 1,
    }),
    listProjectMembers: vi.fn().mockResolvedValue({
      items: [
        {
          id: "m-1",
          user_id: "u1",
          email: "admin@example.com",
          full_name: "Admin User",
          role: "owner",
          created_at: "2026-10-13T10:00:00Z",
        },
        {
          id: "m-2",
          user_id: "u2",
          email: "dev@example.com",
          full_name: "Developer One",
          role: "maintainer",
          created_at: "2026-10-13T11:00:00Z",
        },
      ],
      total: 2,
    }),
    listApiTokens: vi.fn().mockResolvedValue({
      items: [
        {
          id: "token-uuid-1",
          name: "GitHub Actions CI Runner",
          token_prefix: "uitp_live_abc1",
          expires_at: "2026-11-13T10:00:00Z",
          last_used_at: null,
          created_at: "2026-10-13T10:00:00Z",
          is_revoked: false,
        },
      ],
    }),
    listProjectAuditEvents: vi.fn().mockResolvedValue({
      items: [
        {
          id: "audit-1",
          event_type: "role_change",
          actor_id: "u1",
          project_id: "proj-1",
          resource_type: "membership",
          resource_id: "m-2",
          timestamp: "2026-10-13T11:00:00Z",
          metadata_json: { target_email: "dev@example.com", role: "maintainer" },
          metadata_redacted: false,
        },
      ],
      total: 1,
    }),
    addProjectMember: vi.fn().mockResolvedValue({
      id: "m-3",
      user_id: "u3",
      email: "newuser@example.com",
      full_name: null,
      role: "viewer",
      created_at: "2026-10-13T12:00:00Z",
    }),
    removeProjectMember: vi.fn().mockResolvedValue(undefined),
    createApiToken: vi.fn().mockResolvedValue({
      id: "new-token-id",
      name: "New CI Token",
      token: "uitp_live_secret_full_token_987",
      token_prefix: "uitp_live_secr",
      expires_at: "2026-11-13T10:00:00Z",
      created_at: "2026-10-13T12:00:00Z",
    }),
    revokeApiToken: vi.fn().mockResolvedValue({ message: "Token revoked." }),
  };
});

function renderSettings(initialRoute = "/projects/proj-1/settings") {
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
          <Route path="/projects/:projectId/settings" element={<Settings />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Settings Page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders page header and navigation tabs", async () => {
    renderSettings();
    expect(await screen.findByText(/Project Settings & Access/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Team & Access/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /API Tokens \(CI\/CD\)/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Audit Trail/i })).toBeInTheDocument();
  });

  it("renders members table in Team tab", async () => {
    renderSettings();
    expect(await screen.findByText("admin@example.com")).toBeInTheDocument();
    expect(screen.getByText("dev@example.com")).toBeInTheDocument();
    expect(screen.getByText("OWNER")).toBeInTheDocument();
    expect(screen.getByText("MAINTAINER")).toBeInTheDocument();
  });

  it("switches to API Tokens tab and displays tokens", async () => {
    renderSettings();
    const tokensTab = screen.getByRole("tab", { name: /API Tokens \(CI\/CD\)/i });
    fireEvent.click(tokensTab);

    expect(await screen.findByText("GitHub Actions CI Runner")).toBeInTheDocument();
    expect(screen.getByText(/\+ Generate New Token/i)).toBeInTheDocument();
    expect(screen.getByText(/How to use in CI\/CD/i)).toBeInTheDocument();
  });

  it("switches to Audit Trail tab and displays audit events", async () => {
    renderSettings();
    const auditTab = screen.getByRole("tab", { name: /Audit Trail/i });
    fireEvent.click(auditTab);

    expect(await screen.findByText("role_change")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Inspect/i })).toBeInTheDocument();
  });
});
