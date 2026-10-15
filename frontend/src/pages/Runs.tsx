import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { CreateProjectModal } from "@/components/modals/CreateProjectModal";
import { Badge, statusVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import { useProjects } from "@/hooks/useProjects";
import { usePlans, useRuns } from "@/hooks/useRuns";
import type { RunFilterParams } from "@/types/run";
import { formatUtcDateTime, formatUtcDuration } from "@/lib/date";
import styles from "./Runs.module.css";

function formatTimestamp(isoString?: string | null): string {
  return formatUtcDateTime(isoString);
}

const formatDuration = formatUtcDuration;


function getStatusAccessibleLabel(status: string): string {
  switch (status.toLowerCase()) {
    case "passed":
      return "✓ Passed";
    case "failed":
      return "✕ Failed";
    case "running":
      return "⏳ Running";
    case "queued":
      return "⏸ Queued";
    case "blocked":
      return "⛔ Blocked";
    case "inconclusive":
      return "? Inconclusive";
    default:
      return status;
  }
}

export default function Runs() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const { data: projectsData, isLoading: isProjectsLoading } = useProjects();
  const projects = projectsData?.items ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState<boolean>(false);

  const activeProjectId =
    urlProjectId ||
    (projects.find((p) => p.id === selectedProjectId)?.id ?? projects[0]?.id ?? "");

  const currentProject = projects.find((p) => p.id === activeProjectId);

  // Filter & pagination states
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [planFilter, setPlanFilter] = useState<string>("");
  const [adapterFilter, setAdapterFilter] = useState<string>("");
  const [sortBy, setSortBy] = useState<"created_at" | "started_at" | "finished_at" | "status">("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [pageSize, setPageSize] = useState<number>(20);
  const [pageIndex, setPageIndex] = useState<number>(0);

  const filterParams: RunFilterParams = {
    status: statusFilter || undefined,
    plan_id: planFilter || undefined,
    adapter_type: adapterFilter || undefined,
    sort_by: sortBy,
    sort_order: sortOrder,
    limit: pageSize,
    offset: pageIndex * pageSize,
  };

  const { data: runsData, isLoading, error, refetch } = useRuns(activeProjectId, filterParams);
  const { data: plansData } = usePlans(activeProjectId);
  const plans = plansData?.items ?? [];

  const runs = runsData?.items ?? [];
  const totalRuns = runsData?.total ?? 0;
  const totalPages = Math.ceil(totalRuns / pageSize) || 1;

  const hasActiveRuns = runs.some((r) => r.status === "running" || r.status === "queued");

  const hasFiltersApplied = Boolean(statusFilter || planFilter || adapterFilter || pageIndex > 0);

  const clearFilters = () => {
    setStatusFilter("");
    setPlanFilter("");
    setAdapterFilter("");
    setPageIndex(0);
  };

  return (
    <AppShell
      header={
        <Header
          currentProjectId={activeProjectId}
          onSelectProject={(newId) => {
            setSelectedProjectId(newId);
            setPageIndex(0);
          }}
        />
      }
      sidebar={<Sidebar currentProjectId={activeProjectId} />}
    >
      <div className={styles.runsPage}>
        {/* Header row */}
        <div className={styles.headerRow}>
          <div className={styles.titleArea}>
            <h1 className={styles.heading}>Test Runs</h1>
            <p className={styles.subheading}>
              Detailed execution history, logs, and evidence for{" "}
              <strong>{currentProject ? currentProject.name : activeProjectId}</strong>
            </p>
          </div>

          {hasActiveRuns && (
            <div className={styles.liveIndicator} role="status" aria-live="polite">
              <span className={styles.pulseDot} aria-hidden="true" />
              <span>Live polling active (4s)</span>
            </div>
          )}
        </div>

        {/* Filter Toolbar */}
        <div className={styles.filterToolbar} role="search" aria-label="Run filters">
          <div className={styles.filterGroup}>
            <label htmlFor="filter-status" className={styles.filterLabel}>
              Status
            </label>
            <select
              id="filter-status"
              className={styles.select}
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value);
                setPageIndex(0);
              }}
              aria-label="Filter by Status"
            >
              <option value="">All Statuses</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed</option>
              <option value="running">Running</option>
              <option value="queued">Queued</option>
              <option value="blocked">Blocked</option>
              <option value="inconclusive">Inconclusive</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="filter-plan" className={styles.filterLabel}>
              Plan
            </label>
            <select
              id="filter-plan"
              className={styles.select}
              value={planFilter}
              onChange={(e) => {
                setPlanFilter(e.target.value);
                setPageIndex(0);
              }}
              aria-label="Filter by Plan"
            >
              <option value="">All Plans</option>
              {plans.map((p) => (
                <option key={p.plan_id} value={p.plan_id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="filter-adapter" className={styles.filterLabel}>
              Adapter
            </label>
            <select
              id="filter-adapter"
              className={styles.select}
              value={adapterFilter}
              onChange={(e) => {
                setAdapterFilter(e.target.value);
                setPageIndex(0);
              }}
              aria-label="Filter by Adapter"
            >
              <option value="">All Adapters</option>
              <option value="http_api">HTTP API</option>
              <option value="web">Web (Playwright)</option>
            </select>
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="filter-sort" className={styles.filterLabel}>
              Sort By
            </label>
            <select
              id="filter-sort"
              className={styles.select}
              value={sortBy}
              onChange={(e) =>
                setSortBy(
                  e.target.value as
                    | "created_at"
                    | "started_at"
                    | "finished_at"
                    | "status"
                )
              }
              aria-label="Sort By"
            >
              <option value="created_at">Created Date</option>
              <option value="started_at">Started Date</option>
              <option value="finished_at">Finished Date</option>
              <option value="status">Status</option>
            </select>
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="filter-order" className={styles.filterLabel}>
              Order
            </label>
            <select
              id="filter-order"
              className={styles.select}
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as "asc" | "desc")}
              aria-label="Sort Order"
            >
              <option value="desc">Newest First</option>
              <option value="asc">Oldest First</option>
            </select>
          </div>

          {hasFiltersApplied && (
            <div className={styles.clearButtonWrapper}>
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear Filters
              </Button>
            </div>
          )}
        </div>

        {/* Loading state */}
        {isLoading && <Spinner fullPage label="Loading test runs…" />}

        {/* Error state */}
        {error && (
          <ErrorMessage
            title="Failed to retrieve runs"
            message={error.message}
          >
            <Button variant="secondary" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </ErrorMessage>
        )}

        {/* Zero projects state */}
        {!isProjectsLoading && projects.length === 0 && (
          <div style={{ marginTop: "var(--space-4)", marginBottom: "var(--space-6)" }}>
            <Card>
              <CardBody>
                <EmptyState
                  icon="🚀"
                  title="No Projects Available"
                  description="Create your first workspace project to start executing test suites and inspecting test runs."
                  action={
                    <Button variant="primary" onClick={() => setIsCreateProjectOpen(true)}>
                      + Create First Project
                    </Button>
                  }
                />
              </CardBody>
            </Card>
          </div>
        )}

        {/* Runs Content */}
        {projects.length > 0 && !isLoading && !error && (
          <div className={styles.tableCard}>
            {runs.length === 0 ? (
              <EmptyState
                icon="🔍"
                title={hasFiltersApplied ? "No matching runs found" : "No test runs yet"}
                description={
                  hasFiltersApplied
                    ? "Try adjusting your filter criteria to see previous test runs."
                    : "Execute test plans to view execution timeline and step results here."
                }
                action={
                  hasFiltersApplied ? (
                    <Button variant="secondary" size="sm" onClick={clearFilters}>
                      Reset Filters
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <>
                <div className={styles.tableWrapper}>
                  <table className={styles.table} aria-label="Test Runs History Table">
                    <thead>
                      <tr>
                        <th scope="col">Status</th>
                        <th scope="col">Run / Plan</th>
                        <th scope="col">Version</th>
                        <th scope="col">Adapter</th>
                        <th scope="col">Execution Time</th>
                        <th scope="col">Duration</th>
                        <th scope="col">Steps Result</th>
                        <th scope="col">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {runs.map((run) => {
                        const passedSteps = run.step_attempts.filter((s) => s.status === "passed").length;
                        const totalSteps = run.step_attempts.length;
                        const failedStep = run.step_attempts.find((s) => s.status === "failed");

                        return (
                          <tr key={run.id} data-testid={`run-row-${run.id}`}>
                            <td>
                              <Badge variant={statusVariant(run.status)}>
                                {getStatusAccessibleLabel(run.status)}
                              </Badge>
                            </td>
                            <td>
                              <Link
                                to={`/runs/${run.id}`}
                                className={styles.runTitle}
                                aria-label={`View run ${run.id}`}
                              >
                                {run.plan_name || `Plan ${run.plan_id.substring(0, 8)}`}
                              </Link>
                              <div className={styles.runMeta}>ID: {run.id.substring(0, 8)}…</div>
                            </td>
                            <td>v{run.plan_version}</td>
                            <td>
                              <Badge variant="default">
                                {run.adapter_type?.toUpperCase() || "HTTP"}
                              </Badge>
                            </td>
                            <td>{formatTimestamp(run.created_at)}</td>
                            <td>{formatDuration(run.started_at, run.finished_at)}</td>
                            <td>
                              <div className={styles.stepsSummary}>
                                {totalSteps > 0 ? (
                                  <>
                                    {passedSteps}/{totalSteps} steps
                                    {failedStep && (
                                      <div style={{ color: "var(--color-failed)" }}>
                                        Failed: {failedStep.step_id}
                                      </div>
                                    )}
                                  </>
                                ) : (
                                  run.status === "queued" ? "Queued" : "Pending execution"
                                )}
                              </div>
                            </td>
                            <td>
                              <Link to={`/runs/${run.id}`}>
                                <Button variant="secondary" size="sm">
                                  Inspect
                                </Button>
                              </Link>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                {/* Pagination Controls */}
                <div className={styles.paginationBar}>
                  <div className={styles.paginationInfo}>
                    Showing {runs.length} of {totalRuns} runs (Page {pageIndex + 1} of {totalPages})
                  </div>

                  <div className={styles.paginationControls}>
                    <div className={styles.limitSelector}>
                      <label htmlFor="page-size-select">Per page:</label>
                      <select
                        id="page-size-select"
                        className={styles.select}
                        value={pageSize}
                        onChange={(e) => {
                          setPageSize(Number(e.target.value));
                          setPageIndex(0);
                        }}
                      >
                        <option value="10">10</option>
                        <option value="20">20</option>
                        <option value="50">50</option>
                      </select>
                    </div>

                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={pageIndex === 0}
                      onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
                      aria-label="Previous page"
                    >
                      ← Previous
                    </Button>

                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={pageIndex + 1 >= totalPages}
                      onClick={() => setPageIndex((p) => p + 1)}
                      aria-label="Next page"
                    >
                      Next →
                    </Button>
                  </div>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <CreateProjectModal
        isOpen={isCreateProjectOpen}
        onClose={() => setIsCreateProjectOpen(false)}
      />
    </AppShell>
  );
}
