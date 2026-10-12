/**
 * App.tsx — root component with React Router.
 *
 * Routes grow as UI tasks are completed:
 *   /            → Dashboard (placeholder, becomes project list in P4-02)
 *   *            → NotFound
 *
 * P4-01 adds authentication guard and the full AppShell layout.
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { queryClient } from "@/lib/query-client";
import Dashboard from "@/pages/Dashboard";
import NotFound from "@/pages/NotFound";

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/"  element={<Dashboard />} />
          <Route path="*"  element={<NotFound />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
