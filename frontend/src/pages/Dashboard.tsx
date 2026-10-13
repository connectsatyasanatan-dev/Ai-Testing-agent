import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
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
import { useDashboard } from "@/hooks/useDashboard";
import { useProjects } from "@/hooks/useProjects";
import styles from "./Dashboard.module.css";

function formatTimestamp(isoString?: string | null): string {
  if (!isoString) return "—";
  try {
    const date = new Date(isoString);
    return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return isoString;
  }
}

function formatDuration(startedAt?: string | null, finishedAt?: string | null): string {
  if (!startedAt || !finishedAt) return "—";
  try {
    const start = new Date(startedAt).getTime();
    const finish = new Date(finishedAt).getTime();
    const diff = Math.max(0, Math.round((finish - start) / 1000));
    return `${diff}s`;
  } catch {
    return "—";
  }
}

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

/* ── Inline SVG Icons for clean professional look ────────────────────────── */

function LayersIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="12 2 2 7 12 12 22 7 12 2" />
      <polyline points="2 17 12 22 22 17" />
      <polyline points="2 12 12 17 22 12" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}

function GlobeIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

function ZapIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
    </svg>
  );
}

export default function Dashboard() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const navigate = useNavigate();
  const { data: projectsData, isLoading: isProjectsLoading } = useProjects();
  const projects = projectsData?.items ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState(false);

  const activeProjectId =
    urlProjectId ||
    (projects.find((p) => p.id === selectedProjectId)?.id ?? projects[0]?.id ?? "");

  const currentProject = projects.find((p) => p.id === activeProjectId);

  const { data: metrics, isLoading, error, refetch } = useDashboard(activeProjectId);

  const isAuthError =
    error?.message?.toLowerCase().includes("authentication") ||
    (error as { status?: number })?.status === 401;

  const totalRuns = metrics?.total_runs ?? 0;
  const passedRuns = metrics?.status_counts?.passed ?? 0;
  const failedRuns = metrics?.status_counts?.failed ?? 0;
  const activeRuns = (metrics?.status_counts?.running ?? 0) + (metrics?.status_counts?.queued ?? 0);
  const otherRuns = Math.max(0, totalRuns - passedRuns - failedRuns);

  const passRate = totalRuns > 0 ? Math.round((passedRuns / totalRuns) * 100) : 0;
  const passedPct = totalRuns > 0 ? (passedRuns / totalRuns) * 100 : 0;
  const failedPct = totalRuns > 0 ? (failedRuns / totalRuns) * 100 : 0;
  const otherPct = totalRuns > 0 ? (otherRuns / totalRuns) * 100 : 0;

  return (
    <AppShell
      header={
        <Header
          currentProjectId={activeProjectId}
          onSelectProject={(newId) => setSelectedProjectId(newId)}
        />
      }
      sidebar={<Sidebar currentProjectId={activeProjectId} />}
    >
      <div className={styles.page}>
        {/* Header row */}
        <div className={styles.pageHeader}>
          <div className={styles.titleGroup}>
            <h1 className={styles.title}>
              {currentProject ? currentProject.name : "Test Dashboard"}
            </h1>
            <p className={styles.subtitle}>
              {currentProject
                ? `Real-time execution status and quality metrics for ${currentProject.name}`
                : "Real-time execution status and enterprise test analytics"}
            </p>
          </div>
          <div className={styles.headerActions}>
            {activeProjectId && (
              <>
                <Link to={`/projects/${activeProjectId}/runs`}>
                  <Button variant="primary" size="sm">
                    View All Runs →
                  </Button>
                </Link>
                <Link to={`/projects/${activeProjectId}/environments`}>
                  <Button variant="secondary" size="sm">
                    Environments
                  </Button>
                </Link>
              </>
            )}
            <Button
              variant={activeProjectId ? "outline" : "primary"}
              size="sm"
              onClick={() => setIsCreateProjectOpen(true)}
            >
              + New Project
            </Button>
          </div>
        </div>

        {/* Loading state */}
        {isLoading && <Spinner fullPage label="Loading dashboard metrics…" />}

        {/* Zero projects state */}
        {!isProjectsLoading && projects.length === 0 && (
          <div style={{ marginTop: "var(--space-6)" }}>
            <Card>
              <CardBody>
                <EmptyState
                  icon="🚀"
                  title="No Test Projects Found"
                  description="Create your first workspace project to start configuring environments, authoring test plans, and orchestrating autonomous AI test agents."
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

        {/* Error state */}
        {error && (
          <ErrorMessage
            title="Failed to load dashboard metrics"
            message={error.message}
          >
            <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
              {isAuthError && (
                <Button variant="primary" size="sm" onClick={() => navigate("/login")}>
                  Sign in
                </Button>
              )}
              <Button variant="secondary" size="sm" onClick={() => void refetch()}>
                Retry
              </Button>
            </div>
          </ErrorMessage>
        )}

        {/* Populated dashboard content */}
        {metrics && (
          <>
            {/* Stat Cards Grid */}
            <div className={styles.statsGrid}>
              <div className={styles.statCard}>
                <div className={styles.statMeta}>Test Plans</div>
                <div className={styles.statValue}>{metrics.total_plans}</div>
                <div className={styles.statDelta}>Configured test suites</div>
              </div>

              <div className={styles.statCard}>
                <div className={styles.statMeta}>Total Runs</div>
                <div className={styles.statValue}>{metrics.total_runs}</div>
                <div className={styles.statDelta}>Lifetime executions</div>
              </div>

              <div className={styles.statCard}>
                <div className={styles.statMeta}>Passed</div>
                <div className={`${styles.statValue} ${styles.passed}`}>{passedRuns}</div>
                <div className={styles.statDelta}>
                  {totalRuns > 0 ? `${passRate}% pass rate` : "No runs yet"}
                </div>
              </div>

              <div className={styles.statCard}>
                <div className={styles.statMeta}>Failed</div>
                <div className={`${styles.statValue} ${styles.failed}`}>{failedRuns}</div>
                <div className={styles.statDelta}>
                  {failedRuns > 0 ? "Requires review" : "Zero failures"}
                </div>
              </div>

              <div className={styles.statCard}>
                <div className={styles.statMeta}>Active / In-Flight</div>
                <div className={`${styles.statValue} ${activeRuns > 0 ? styles.running : ""}`}>
                  {activeRuns > 0 && <span className={styles.liveDot} />}
                  {activeRuns}
                </div>
                <div className={styles.statDelta}>
                  {activeRuns > 0 ? "Executing now" : "Workers idle"}
                </div>
              </div>
            </div>

            {/* Health / Pass Rate Card */}
            {totalRuns > 0 && (
              <div className={styles.passRateSection}>
                <div className={styles.passRateHeader}>
                  <span className={styles.passRateLabel}>Execution Pass Rate</span>
                  <span className={styles.passRateValue}>{passRate}% Passed</span>
                </div>
                <div
                  className={styles.progressBar}
                  role="progressbar"
                  aria-valuenow={passRate}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Pass rate: ${passRate}%`}
                >
                  <div
                    className={`${styles.progressSegment} ${styles.segmentPassed}`}
                    style={{ width: `${passedPct}%` }}
                  />
                  <div
                    className={`${styles.progressSegment} ${styles.segmentFailed}`}
                    style={{ width: `${failedPct}%` }}
                  />
                  <div
                    className={`${styles.progressSegment} ${styles.segmentOther}`}
                    style={{ width: `${otherPct}%` }}
                  />
                </div>
                <div className={styles.progressLegend}>
                  <div className={styles.legendItem}>
                    <span className={styles.legendDot} style={{ background: "var(--color-passed)" }} />
                    Passed ({passedRuns})
                  </div>
                  <div className={styles.legendItem}>
                    <span className={styles.legendDot} style={{ background: "var(--color-failed)" }} />
                    Failed ({failedRuns})
                  </div>
                  {otherRuns > 0 && (
                    <div className={styles.legendItem}>
                      <span className={styles.legendDot} style={{ background: "var(--color-text-subtle)" }} />
                      Other ({otherRuns})
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Split Content Grid: Adapter Breakdown & Recent Activity */}
            <div className={styles.contentGrid}>
              {/* Adapter Breakdown */}
              <div className={styles.panel}>
                <div className={styles.panelHeader}>
                  <h2 className={styles.panelTitle}>Test Adapters</h2>
                  <span className={styles.subtitle}>Supported targets</span>
                </div>
                <div className={styles.panelBody}>
                  {Object.keys(metrics.adapter_breakdown).length === 0 ? (
                    <EmptyState
                      icon={<LayersIcon />}
                      title="No adapter data yet"
                      description="Create plans using HTTP API or Web UI adapters."
                    />
                  ) : (
                    <div className={styles.adapterList}>
                      {Object.entries(metrics.adapter_breakdown).map(([adapter, count]) => (
                        <div key={adapter} className={styles.adapterRow}>
                          <span className={styles.adapterName}>
                            {adapter === "web" ? (
                              <>
                                <GlobeIcon /> Web (Playwright)
                              </>
                            ) : (
                              <>
                                <ZapIcon /> HTTP API
                              </>
                            )}
                          </span>
                          <span className={styles.adapterCount}>{count} plans</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Recent Activity Timeline */}
              <div className={styles.panel}>
                <div className={styles.panelHeader}>
                  <h2 className={styles.panelTitle}>Recent Activity</h2>
                  <span className={styles.subtitle}>Audit & timeline events</span>
                </div>
                <div className={styles.panelBody}>
                  {metrics.recent_activity.length === 0 ? (
                    <EmptyState
                      icon={<PlayIcon />}
                      title="No execution events yet"
                      description="Timeline events will be streamed as runs execute."
                    />
                  ) : (
                    <div className={styles.activityList}>
                      {metrics.recent_activity.map((event) => (
                        <div key={event.id} className={styles.activityItem}>
                          <span className={styles.activityTime}>
                            {formatTimestamp(event.timestamp)}
                          </span>
                          <span className={styles.activityMessage}>
                            {event.message || event.event_type}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Recent Runs Table */}
            <div className={styles.panel}>
              <div className={styles.panelHeader}>
                <h2 className={styles.panelTitle}>Recent Test Runs</h2>
                <Link to={`/projects/${activeProjectId}/runs`} className={styles.panelAction}>
                  View full history →
                </Link>
              </div>
              <div>
                {metrics.recent_runs.length === 0 ? (
                  <div style={{ padding: "var(--space-6)" }}>
                    <EmptyState
                      icon={<PlayIcon />}
                      title="No test runs yet"
                      description="Execute your first test plan to inspect run results."
                    />
                  </div>
                ) : (
                  <div className={styles.tableWrapper}>
                    <table className={styles.table} aria-label="Recent Test Runs Table">
                      <thead>
                        <tr>
                          <th scope="col">Status</th>
                          <th scope="col">Plan</th>
                          <th scope="col">Adapter</th>
                          <th scope="col">Triggered</th>
                          <th scope="col">Duration</th>
                          <th scope="col" style={{ textAlign: "right" }}>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {metrics.recent_runs.map((run) => (
                          <tr key={run.id}>
                            <td>
                              <Badge variant={statusVariant(run.status)}>
                                {getStatusAccessibleLabel(run.status)}
                              </Badge>
                            </td>
                            <td>
                              <Link
                                to={`/runs/${run.id}`}
                                className={styles.runLink}
                                aria-label={`View details for run ${run.id}`}
                              >
                                {run.plan_name || `Plan ${run.plan_id.substring(0, 8)}`} (v
                                {run.plan_version})
                              </Link>
                            </td>
                            <td>
                              <Badge variant="default">
                                {run.adapter_type?.toUpperCase() || "HTTP"}
                              </Badge>
                            </td>
                            <td className={styles.timestamp}>{formatTimestamp(run.created_at)}</td>
                            <td className={styles.duration}>
                              {formatDuration(run.started_at, run.finished_at)}
                            </td>
                            <td style={{ textAlign: "right" }}>
                              <Link to={`/runs/${run.id}`}>
                                <Button variant="secondary" size="xs">
                                  Details
                                </Button>
                              </Link>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      <CreateProjectModal
        isOpen={isCreateProjectOpen}
        onClose={() => setIsCreateProjectOpen(false)}
      />
    </AppShell>
  );
}
