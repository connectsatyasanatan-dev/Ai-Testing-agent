/**
 * App.tsx — Root component with React Router and ProtectedRoute guards.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { queryClient } from "@/lib/query-client";
import Dashboard from "@/pages/Dashboard";
import Environments from "@/pages/Environments";
import Login from "@/pages/Login";
import NotFound from "@/pages/NotFound";
import PlanDetail from "@/pages/PlanDetail";
import PlanEditor from "@/pages/PlanEditor";
import Plans from "@/pages/Plans";
import Register from "@/pages/Register";
import RunDetail from "@/pages/RunDetail";
import Runs from "@/pages/Runs";
import Settings from "@/pages/Settings";

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          {/* Public Auth Routes */}
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />

          {/* Protected Platform Routes */}
          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects/:projectId" element={<Dashboard />} />
            <Route path="/plans" element={<Plans />} />
            <Route path="/projects/:projectId/plans" element={<Plans />} />
            <Route path="/plans/new" element={<PlanEditor />} />
            <Route path="/projects/:projectId/plans/new" element={<PlanEditor />} />
            <Route path="/plans/:planId" element={<PlanDetail />} />
            <Route path="/projects/:projectId/plans/:planId" element={<PlanDetail />} />
            <Route path="/plans/:planId/edit" element={<PlanEditor />} />
            <Route path="/projects/:projectId/plans/:planId/edit" element={<PlanEditor />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/projects/:projectId/runs" element={<Runs />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/projects/:projectId/runs/:runId" element={<RunDetail />} />
            <Route path="/environments" element={<Environments />} />
            <Route path="/projects/:projectId/environments" element={<Environments />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/projects/:projectId/settings" element={<Settings />} />
          </Route>

          <Route path="*" element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
