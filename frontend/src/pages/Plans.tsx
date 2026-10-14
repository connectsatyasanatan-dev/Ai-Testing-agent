import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { AIGeneratePlanModal } from "@/components/plans/AIGeneratePlanModal";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import { usePlans } from "@/hooks/usePlans";
import { useProjects } from "@/hooks/useProjects";
import type { PlanStatus, PlanSummary } from "@/types/plan";
import { Badge } from "@/components/ui/Badge";
import styles from "./Plans.module.css";

function SparklesIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

const STATUS_FILTERS: Array<{ id: string; label: string }> = [
  { id: "all", label: "All Plans" },
  { id: "draft", label: "Draft" },
  { id: "pending_review", label: "Pending Review" },
  { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
  { id: "archived", label: "Archived" },
];

function getStatusBadge(status: PlanStatus) {
  switch (status) {
    case "approved":
      return <Badge variant="approved">Approved</Badge>;
    case "pending_review":
      return <Badge variant="pending">Review Pending</Badge>;
    case "rejected":
      return <Badge variant="rejected">Rejected</Badge>;
    case "archived":
      return <Badge variant="archived">Archived</Badge>;
    case "draft":
    default:
      return <Badge variant="draft">Draft</Badge>;
  }
}

export default function Plans() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const navigate = useNavigate();
  const { data: projectList } = useProjects();
  const currentProjectId = urlProjectId || projectList?.items[0]?.id;

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [isGenerateModalOpen, setIsGenerateModalOpen] = useState(false);

  const {
    data: plansData,
    isLoading,
    isError,
    error,
    refetch,
  } = usePlans(currentProjectId, {
    search: search.trim() || undefined,
    status: statusFilter !== "all" ? (statusFilter as PlanStatus) : undefined,
  });

  const getCreatePath = () =>
    currentProjectId ? `/projects/${currentProjectId}/plans/new` : "/plans/new";

  const getPlanDetailPath = (planId: string) =>
    currentProjectId ? `/projects/${currentProjectId}/plans/${planId}` : `/plans/${planId}`;

  const getPlanEditPath = (planId: string) =>
    currentProjectId ? `/projects/${currentProjectId}/plans/${planId}/edit` : `/plans/${planId}/edit`;

  return (
    <AppShell
      header={
        <Header
          currentProjectId={currentProjectId}
          onSelectProject={(newId) => navigate(`/projects/${newId}/plans`)}
        />
      }
      sidebar={<Sidebar currentProjectId={currentProjectId} />}
    >
      <div className={styles.container}>
        <div className={styles.header}>
          <div className={styles.headerContent}>
            <h1>Test Plans</h1>
            <p className={styles.subtitle}>
              Declarative test plans with immutable versioning, review workflows, and approval gates.
            </p>
          </div>
          <div className={styles.headerActions}>
            <Link
              to={currentProjectId ? `/projects/${currentProjectId}/missions/new` : "/missions/new"}
              className={styles.createButton}
              style={{ backgroundColor: "#4f46e5", borderColor: "#4338ca", color: "#fff" }}
              id="plan-mission-button"
            >
              <span>🚀</span>
              <span>Autonomous Mission</span>
            </Link>
            <button
              type="button"
              className={styles.aiGenerateButton}
              onClick={() => setIsGenerateModalOpen(true)}
              id="ai-generate-plan-button"
            >
              <SparklesIcon />
              <span>Generate with AI</span>
            </button>
            <Link to={getCreatePath()} className={styles.createButton} id="create-plan-button">
              <PlusIcon />
              <span>New Test Plan</span>
            </Link>
          </div>
        </div>

        <div className={styles.toolbar}>
          <div className={styles.searchBox}>
            <SearchIcon />
            <input
              id="plan-search-input"
              type="text"
              placeholder="Search plans by name or description..."
              className={styles.searchInput}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className={styles.filterGroup} role="tablist" aria-label="Plan status filters">
            {STATUS_FILTERS.map((f) => {
              const active = statusFilter === f.id;
              return (
                <button
                  key={f.id}
                  id={`filter-${f.id}`}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={`${styles.filterTab} ${active ? styles.filterTabActive : ""}`}
                  onClick={() => setStatusFilter(f.id)}
                >
                  {f.label}
                </button>
              );
            })}
          </div>
        </div>

        {isLoading && <Spinner label="Loading test plans..." />}

        {isError && (
          <ErrorMessage
            title="Failed to load test plans"
            message={error instanceof Error ? error.message : "Failed to load plans."}
          >
            <Button variant="secondary" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </ErrorMessage>
        )}

        {!isLoading && !isError && plansData && plansData.items.length === 0 && (
          <EmptyState
            title="No test plans found"
            description={
              search || statusFilter !== "all"
                ? "No test plans match the current search or status filter."
                : "Get started by building your first declarative test plan."
            }
            action={
              <Link to={getCreatePath()} className={styles.createButton}>
                Create Plan
              </Link>
            }
          />
        )}

        {!isLoading && !isError && plansData && plansData.items.length > 0 && (
          <div className={styles.tableCard}>
            <div className={styles.tableWrapper}>
              <table className={styles.table} aria-label="Test plans list">
                <thead>
                  <tr>
                    <th>Plan Name</th>
                    <th>Adapter</th>
                    <th>Version</th>
                    <th>Status</th>
                    <th>Steps</th>
                    <th>Approval Info</th>
                    <th>Updated</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {plansData.items.map((plan: PlanSummary) => (
                    <tr key={plan.id} id={`plan-row-${plan.plan_id}`}>
                      <td>
                        <Link to={getPlanDetailPath(plan.plan_id)} className={styles.planName}>
                          {plan.name}
                        </Link>
                        {plan.description && (
                          <div className={styles.planDesc} title={plan.description}>
                            {plan.description}
                          </div>
                        )}
                      </td>
                      <td>
                        <span className={styles.adapterBadge}>
                          {plan.adapter_type === "web" ? "🌐 Web (Playwright)" : "⚡ HTTP API"}
                        </span>
                      </td>
                      <td>
                        <span className={styles.versionBadge}>v{plan.version}</span>
                      </td>
                      <td>{getStatusBadge(plan.status)}</td>
                      <td>
                        <span>{plan.steps?.length ?? "—"}</span>
                      </td>
                      <td>
                        {plan.status === "approved" && plan.approved_at ? (
                          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-muted)" }}>
                            Approved on {new Date(plan.approved_at).toLocaleDateString()}
                          </span>
                        ) : plan.status === "pending_review" ? (
                          <span style={{ fontSize: "var(--font-size-xs)", color: "#eab308" }}>
                            Awaiting sign-off
                          </span>
                        ) : (
                          <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-subtle)" }}>
                            Not approved
                          </span>
                        )}
                      </td>
                      <td style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-muted)" }}>
                        {new Date(plan.updated_at || plan.created_at).toLocaleDateString()}
                      </td>
                      <td>
                        <div className={styles.actionsCell} style={{ justifyContent: "flex-end" }}>
                          <Link
                            to={getPlanDetailPath(plan.plan_id)}
                            className={styles.actionBtn}
                            id={`view-plan-${plan.plan_id}`}
                          >
                            Inspect
                          </Link>
                          <Link
                            to={getPlanEditPath(plan.plan_id)}
                            className={`${styles.actionBtn} ${styles.actionBtnPrimary}`}
                            id={`edit-plan-${plan.plan_id}`}
                          >
                            {plan.status === "approved" ? "New Version" : "Edit"}
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <AIGeneratePlanModal
        projectId={currentProjectId || ""}
        isOpen={isGenerateModalOpen}
        onClose={() => setIsGenerateModalOpen(false)}
        onSuccess={() => { void refetch(); }}
      />
    </AppShell>
  );
}
