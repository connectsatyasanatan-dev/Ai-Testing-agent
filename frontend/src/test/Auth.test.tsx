import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Login from "@/pages/Login";
import Register from "@/pages/Register";
import * as apiClient from "@/lib/api-client";

vi.mock("@/lib/api-client", async () => {
  const actual = await vi.importActual<typeof apiClient>("@/lib/api-client");
  return {
    ...actual,
    login: vi.fn().mockResolvedValue({
      id: "u-1",
      email: "admin@example.com",
      full_name: "Admin User",
      is_active: true,
    }),
    register: vi.fn().mockResolvedValue({
      id: "u-2",
      email: "alice@example.com",
      full_name: "Alice Developer",
      is_active: true,
    }),
    getCurrentUser: vi.fn().mockResolvedValue(null),
  };
});

function renderWithClient(initialPath = "/login") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/" element={<div>Dashboard Protected</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Auth Flow — Login & Register Pages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders login form with title, branding, and input fields", () => {
    renderWithClient("/login");

    expect(screen.getByRole("heading", { name: /Sign in to your account/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Email address/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Password/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign In/i })).toBeInTheDocument();
  });

  it("toggles password visibility on show/hide click", () => {
    renderWithClient("/login");

    const passwordInput = screen.getByLabelText(/^Password/i) as HTMLInputElement;
    const toggleButton = screen.getByRole("button", { name: /Show password/i });

    expect(passwordInput.type).toBe("password");
    fireEvent.click(toggleButton);
    expect(passwordInput.type).toBe("text");
    fireEvent.click(toggleButton);
    expect(passwordInput.type).toBe("password");
  });

  it("quick-fills credentials when demo pills are clicked", () => {
    renderWithClient("/login");

    const adminPill = screen.getByRole("button", { name: /Admin/i });
    fireEvent.click(adminPill);

    const emailInput = screen.getByLabelText(/Email address/i) as HTMLInputElement;
    const passwordInput = screen.getByLabelText(/^Password/i) as HTMLInputElement;

    expect(emailInput.value).toBe("admin@example.com");
    expect(passwordInput.value).toBe("AdminPassword123!");
  });

  it("submits login form and calls api login", async () => {
    renderWithClient("/login");

    const emailInput = screen.getByLabelText(/Email address/i);
    const passwordInput = screen.getByLabelText(/^Password/i);
    const submitBtn = screen.getByRole("button", { name: /Sign In/i });

    fireEvent.change(emailInput, { target: { value: "engineer@example.com" } });
    fireEvent.change(passwordInput, { target: { value: "SecurePass123!" } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(apiClient.login).toHaveBeenCalledWith(
        expect.objectContaining({
          email: "engineer@example.com",
          password: "SecurePass123!",
        }),
        expect.anything()
      );
    });
  });

  it("renders register page and validates password mismatch", async () => {
    renderWithClient("/register");

    expect(screen.getByRole("heading", { name: /Create your account/i })).toBeInTheDocument();

    const nameInput = screen.getByLabelText(/Full name/i);
    const emailInput = screen.getByLabelText(/Work email/i);
    const passInput = screen.getByLabelText(/Password \(min 8 chars\)/i);
    const confirmInput = screen.getByLabelText(/Confirm password/i);
    const submitBtn = screen.getByRole("button", { name: /Create Account/i });

    fireEvent.change(nameInput, { target: { value: "Alice Developer" } });
    fireEvent.change(emailInput, { target: { value: "alice@example.com" } });
    fireEvent.change(passInput, { target: { value: "Password123!" } });
    fireEvent.change(confirmInput, { target: { value: "DifferentPass123!" } });
    fireEvent.click(submitBtn);

    expect(await screen.findByText(/Passwords do not match/i)).toBeInTheDocument();
    expect(apiClient.register).not.toHaveBeenCalled();
  });

  it("submits register form successfully when passwords match", async () => {
    renderWithClient("/register");

    const nameInput = screen.getByLabelText(/Full name/i);
    const emailInput = screen.getByLabelText(/Work email/i);
    const passInput = screen.getByLabelText(/Password \(min 8 chars\)/i);
    const confirmInput = screen.getByLabelText(/Confirm password/i);
    const submitBtn = screen.getByRole("button", { name: /Create Account/i });

    fireEvent.change(nameInput, { target: { value: "Alice Developer" } });
    fireEvent.change(emailInput, { target: { value: "alice@example.com" } });
    fireEvent.change(passInput, { target: { value: "Password123!" } });
    fireEvent.change(confirmInput, { target: { value: "Password123!" } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(apiClient.register).toHaveBeenCalledWith(
        expect.objectContaining({
          full_name: "Alice Developer",
          email: "alice@example.com",
          password: "Password123!",
        }),
        expect.anything()
      );
    });
  });
});
