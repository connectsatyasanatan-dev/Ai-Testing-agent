/**
 * ProtectedRoute.tsx — Route guard ensuring only authenticated users access the platform.
 * Unauthenticated requests are smoothly redirected to /login with state.from preserved.
 */
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/hooks/useAuth";

interface ProtectedRouteProps {
  children?: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          width: "100vw",
          background: "var(--color-bg)",
          gap: "var(--space-4)",
        }}
      >
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: "var(--radius-lg)",
            background: "linear-gradient(135deg, #6366f1 0%, #3b82f6 100%)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            boxShadow: "0 0 24px rgba(99, 102, 241, 0.4)",
          }}
        >
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="white"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
          </svg>
        </div>
        <Spinner size="md" label="Verifying session..." />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return children ? <>{children}</> : <Outlet />;
}
