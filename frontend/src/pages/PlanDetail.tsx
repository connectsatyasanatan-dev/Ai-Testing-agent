import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import {
  useApprovePlan,
  useArchivePlan,
  usePlan,
  usePlanVersion,
  usePlanVersions,
  useRejectPlan,
  useSubmitPlanReview,
} from "@/hooks/usePlans";
import { useProjects } from "@/hooks/useProjects";
import { useEnvironments } from "@/hooks/useEnvironments";
import { useMissionDAG, useCreateRun } from "@/hooks/useMissions";
import { DAGView } from "@/components/missions/DAGView";
import { formatUtcDateTime } from "@/lib/date";
import type { PlanStatus, PlanVersionSummary } from "@/types/plan";
import styles from "./PlanDetail.module.css";

import { Badge } from "@/components/ui/Badge";

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

export default function PlanDetail() {
  const { projectId: urlProjectId, planId } = useParams<{
    projectId?: string;
    planId: string;
  }>();
  const navigate = useNavigate();

  const { data: projectList } = useProjects();
  const currentProjectId = urlProjectId || projectList?.items[0]?.id;

  // Plan and Version data
  const { data: latestPlan, isLoading: isLoadingLatest, isError: isLatestError, error: latestError } = usePlan(planId);
  const { data: versionsData } = usePlanVersions(planId);

  const [selectedVersion, setSelectedVersion] = useState<number | undefined>(undefined);

  useEffect(() => {
    if (latestPlan && selectedVersion === undefined) {
      setSelectedVersion(latestPlan.version);
    }
  }, [latestPlan, selectedVersion]);

  // Load specific version only if user selected a version different from latest
  const isViewingOlderVersion =
    selectedVersion !== undefined &&
    latestPlan !== undefined &&
    selectedVersion !== latestPlan.version;

  const {
    data: planVersion,
    isLoading: isLoadingVersion,
  } = usePlanVersion(planId, isViewingOlderVersion ? selectedVersion : undefined);

  const activePlan = isViewingOlderVersion ? planVersion : latestPlan;
  const isMissionDAG = activePlan?.adapter_type === "multi_agent_dag";
  const { data: missionDAG } = useMissionDAG(isMissionDAG ? planId : undefined);

  // Modals state
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const [showArchiveModal, setShowArchiveModal] = useState(false);
  const [showRunModal, setShowRunModal] = useState(false);
  const [selectedEnvId, setSelectedEnvId] = useState<string>("");
  const [runDispatchError, setRunDispatchError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // Mutations
  const approveMutation = useApprovePlan(planId);
  const submitReviewMutation = useSubmitPlanReview(planId);
  const rejectMutation = useRejectPlan(planId);
  const archiveMutation = useArchivePlan(planId);
  const createRunMutation = useCreateRun(planId ?? "");

  const { data: envsData } = useEnvironments(currentProjectId ?? "");
  const environments = envsData?.items?.filter((e) => e.status === "active") ?? [];

  const handleLaunchRun = async () => {
    const envId = selectedEnvId || environments[0]?.id;
    if (!envId) {
      setRunDispatchError("Please select a target environment for execution.");
      return;
    }
    setRunDispatchError(null);
    try {
      const newRun = await createRunMutation.mutateAsync({
        environment_id: envId,
        suite_type: "all",
      });
      setShowRunModal(false);
      navigate(`/runs/${newRun.id}`);
    } catch (err) {
      setRunDispatchError(err instanceof Error ? err.message : "Failed to dispatch test run.");
    }
  };

  const handleApprove = async () => {
    if (!activePlan) return;
    setActionError(null);
    try {
      await approveMutation.mutateAsync(activePlan.version);
      setShowApproveModal(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to approve plan.");
    }
  };

  const handleSubmitReview = async () => {
    if (!activePlan) return;
    setActionError(null);
    try {
      await submitReviewMutation.mutateAsync(activePlan.version);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to submit plan for review.");
    }
  };

  const handleReject = async () => {
    if (!activePlan) return;
    setActionError(null);
    try {
      await rejectMutation.mutateAsync({
        version: activePlan.version,
        reason: rejectionReason.trim() || undefined,
      });
      setShowRejectModal(false);
      setRejectionReason("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to reject plan.");
    }
  };

  const handleArchive = async () => {
    if (!activePlan) return;
    setActionError(null);
    try {
      await archiveMutation.mutateAsync(activePlan.version);
      setShowArchiveModal(false);
      const plansListPath = currentProjectId
        ? `/projects/${currentProjectId}/plans`
        : "/plans";
      navigate(plansListPath);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Failed to archive plan.");
    }
  };

  const getEditPath = () =>
    currentProjectId
      ? `/projects/${currentProjectId}/plans/${planId}/edit`
      : `/plans/${planId}/edit`;

  const renderShell = (children: React.ReactNode) => (
    <AppShell
      header={
        <Header
          currentProjectId={currentProjectId}
          onSelectProject={(newId) => navigate(`/projects/${newId}/plans`)}
        />
      }
      sidebar={<Sidebar currentProjectId={currentProjectId} />}
    >
      {children}
    </AppShell>
  );

  if (isLoadingLatest || (isViewingOlderVersion && isLoadingVersion)) {
    return renderShell(<Spinner label="Loading test plan details..." />);
  }

  if (isLatestError || !activePlan) {
    return renderShell(
      <ErrorMessage
        title="Failed to load test plan"
        message={
          latestError instanceof Error
            ? latestError.message
            : "Failed to load test plan."
        }
      />
    );
  }

  const isApproved = activePlan.status === "approved";
  const isPendingReview = activePlan.status === "pending_review";
  const isDraft = activePlan.status === "draft";
  const isRejected = activePlan.status === "rejected";

  return renderShell(
    <div className={styles.container}>
        {/* Header with Title and Version Switcher */}
        <div className={styles.header}>
          <div className={styles.headerLeft}>
            <div className={styles.titleRow}>
              <h1>{activePlan.name}</h1>
              <div>{getStatusBadge(activePlan.status)}</div>
            </div>
            {activePlan.description && (
              <p className={styles.subtitle}>{activePlan.description}</p>
            )}

            {/* Version dropdown */}
            <div className={styles.versionSelector}>
              <label htmlFor="version-selector" style={{ fontSize: "var(--font-size-xs)", fontWeight: 600 }}>
                Version:
              </label>
              <select
                id="version-selector"
                className={styles.versionSelect}
                value={selectedVersion ?? activePlan.version}
                onChange={(e) => setSelectedVersion(Number(e.target.value))}
              >
                {versionsData?.versions.map((v: PlanVersionSummary) => (
                  <option key={v.version} value={v.version}>
                    v{v.version} ({v.status.toUpperCase()})
                  </option>
                )) ?? (
                  <option value={activePlan.version}>v{activePlan.version}</option>
                )}
              </select>
            </div>
          </div>

          {/* Workflow Action Buttons */}
          <div className={styles.headerActions}>
            {(isDraft || isRejected) && (
              <>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  onClick={() => void handleSubmitReview()}
                  disabled={submitReviewMutation.isPending}
                  id="submit-review-btn"
                >
                  <span>🚀</span>
                  <span>{submitReviewMutation.isPending ? "Submitting..." : "Submit for Review"}</span>
                </button>
                <Link to={getEditPath()} className={`${styles.btn} ${styles.btnSecondary}`} id="edit-draft-btn">
                  <span>✏️</span>
                  <span>Edit Draft</span>
                </Link>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSuccess}`}
                  onClick={() => setShowApproveModal(true)}
                  id="direct-approve-btn"
                >
                  <span>✅</span>
                  <span>Approve (v{activePlan.version})</span>
                </button>
              </>
            )}

            {isPendingReview && (
              <>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSuccess}`}
                  onClick={() => setShowApproveModal(true)}
                  id="approve-plan-btn"
                >
                  <span>✅</span>
                  <span>Approve Plan (v{activePlan.version})</span>
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnDanger}`}
                  onClick={() => setShowRejectModal(true)}
                  id="reject-plan-btn"
                >
                  <span>❌</span>
                  <span>Reject</span>
                </button>
                <Link to={getEditPath()} className={`${styles.btn} ${styles.btnSecondary}`} id="edit-plan-btn">
                  <span>✏️</span>
                  <span>Edit</span>
                </Link>
              </>
            )}

            {isApproved && (
              <>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  onClick={() => setShowRunModal(true)}
                  id="execute-run-btn"
                >
                  <span>▶️</span>
                  <span>Execute Test Run</span>
                </button>
                <Link to={getEditPath()} className={`${styles.btn} ${styles.btnSecondary}`} id="new-version-btn">
                  <span>➕</span>
                  <span>Create New Version</span>
                </Link>
              </>
            )}

            <button
              type="button"
              className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => setShowArchiveModal(true)}
              id="archive-plan-btn"
            >
              <span>🗄️</span>
              <span>Archive</span>
            </button>
          </div>
        </div>

        {actionError && (
          <ErrorMessage title="Action Failed" message={actionError}>
            <Button variant="secondary" size="sm" onClick={() => setActionError(null)}>
              Dismiss
            </Button>
          </ErrorMessage>
        )}

        {/* Audit and Approval Info Bar */}
        <div className={styles.auditBar}>
          <div className={styles.auditItem}>
            <span>Adapter:</span>
            <strong>{activePlan.adapter_type === "web" ? "🌐 Web (Playwright)" : "⚡ HTTP API"}</strong>
          </div>
          <div className={styles.auditItem}>
            <span>Created By:</span>
            <strong>{activePlan.created_by}</strong>
          </div>
          <div className={styles.auditItem}>
            <span>Created At:</span>
            <strong>{formatUtcDateTime(activePlan.created_at)}</strong>
          </div>
          {isApproved && activePlan.approved_by && activePlan.approved_at && (
            <div className={styles.auditItem} style={{ color: "#16a34a" }}>
              <span>Approved by:</span>
              <strong>{activePlan.approved_by}</strong>
              <span>at {formatUtcDateTime(activePlan.approved_at)}</span>
            </div>
          )}
        </div>

        {/* Rejection notice if status is rejected */}
        {isRejected && (
          <div className={styles.rejectionBanner} role="alert">
            <span>❌</span>
            <div>
              <strong>Plan Version Rejected:</strong> This version was rejected during review. Review feedback,
              edit the steps, and re-submit for approval.
            </div>
          </div>
        )}

        {/* Plan Steps Sequence List or Mission Task DAG */}
        {isMissionDAG && missionDAG ? (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <h2 style={{ fontSize: "var(--font-size-lg)", fontWeight: "bold" }}>
              Mission Task DAG ({missionDAG.tasks.length} Tasks)
            </h2>
            <DAGView plan={missionDAG} />
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
            <h2 style={{ fontSize: "var(--font-size-lg)", fontWeight: "bold" }}>
              Test Steps ({activePlan.steps.length})
            </h2>

          {activePlan.steps.map((step, sIdx) => (
            <div key={sIdx} className={styles.stepCard} id={`step-detail-${sIdx}`}>
              <div className={styles.stepHeader}>
                <div className={styles.stepTitleArea}>
                  <span className={styles.stepNum}>{sIdx + 1}</span>
                  <span className={styles.stepId}>{step.step_id}</span>
                  <span className={styles.actionPill}>{step.action.toUpperCase()}</span>
                </div>
                <div style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-muted)" }}>
                  Timeout: {step.timeout_seconds}s
                </div>
              </div>

              {step.description && (
                <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                  {step.description}
                </p>
              )}

              {/* Step Parameters Grid */}
              <div className={styles.stepMetaGrid}>
                <div className={styles.stepMetaItem}>
                  <span className={styles.metaLabel}>Idempotency</span>
                  <span className={styles.metaValue}>{step.idempotency_class}</span>
                </div>
                <div className={styles.stepMetaItem}>
                  <span className={styles.metaLabel}>Risk Level</span>
                  <span className={styles.metaValue}>{step.risk_level.toUpperCase()}</span>
                </div>
                <div className={styles.stepMetaItem}>
                  <span className={styles.metaLabel}>Max Retries</span>
                  <span className={styles.metaValue}>{step.retry_policy?.max_attempts ?? 1}</span>
                </div>
                {step.inputs && (
                  <div className={styles.stepMetaItem} style={{ gridColumn: "span 2" }}>
                    <span className={styles.metaLabel}>Inputs</span>
                    <span className={styles.metaValue} style={{ fontFamily: "var(--font-mono)" }}>
                      {step.inputs.path
                        ? `Path: ${String(step.inputs.path)}`
                        : step.inputs.url
                        ? `URL: ${String(step.inputs.url)}`
                        : step.inputs.locator_value
                        ? `Target: [${String(step.inputs.locator_type ?? "css")}] ${String(step.inputs.locator_value)}`
                        : JSON.stringify(step.inputs)}
                    </span>
                  </div>
                )}
              </div>

              {/* Deterministic Assertions */}
              {step.assertions && step.assertions.length > 0 && (
                <div className={styles.assertionList}>
                  <span className={styles.metaLabel}>Assertions ({step.assertions.length})</span>
                  {step.assertions.map((assertion, aIdx) => (
                    <div key={aIdx} className={styles.assertionItem}>
                      <span className={styles.assertionType}>{assertion.type}</span>
                      <span className={styles.assertionExpected}>
                        {assertion.value !== undefined ? `== ${String(assertion.value)}` : ""}
                        {assertion.path ? ` at ${assertion.path}` : ""}
                      </span>
                      {assertion.message && (
                        <span style={{ color: "var(--color-foreground-muted)", fontStyle: "italic" }}>
                          ({assertion.message})
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

        {/* Modal: Approve Plan */}
        {showApproveModal && (
          <div className={styles.modalOverlay} role="dialog" aria-modal="true">
            <div className={styles.modal}>
              <h3 className={styles.modalTitle}>Approve Plan Version {activePlan.version}?</h3>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                You are approving plan <strong>"{activePlan.name}"</strong> (version {activePlan.version})
                containing {activePlan.steps.length} test step(s).
                Once approved, this version becomes immutable.
              </p>
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => setShowApproveModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSuccess}`}
                  onClick={() => void handleApprove()}
                  disabled={approveMutation.isPending}
                  id="confirm-approve-btn"
                >
                  {approveMutation.isPending ? "Approving..." : "Confirm Approval"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Modal: Reject Plan */}
        {showRejectModal && (
          <div className={styles.modalOverlay} role="dialog" aria-modal="true">
            <div className={styles.modal}>
              <h3 className={styles.modalTitle}>Reject Plan Version {activePlan.version}</h3>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                Provide feedback to the plan author explaining why this plan version was not approved:
              </p>
              <textarea
                style={{
                  padding: "var(--space-2)",
                  borderRadius: "var(--radius-md)",
                  border: "1px solid var(--color-border)",
                  minHeight: "80px",
                }}
                placeholder="Reason for rejection (e.g. missing assertions, unsafe credentials, invalid target)..."
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                id="reject-reason-input"
              />
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => setShowRejectModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnDanger}`}
                  onClick={() => void handleReject()}
                  disabled={rejectMutation.isPending}
                  id="confirm-reject-btn"
                >
                  {rejectMutation.isPending ? "Rejecting..." : "Confirm Rejection"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Modal: Archive Plan */}
        {showArchiveModal && (
          <div className={styles.modalOverlay} role="dialog" aria-modal="true">
            <div className={styles.modal}>
              <h3 className={styles.modalTitle}>Archive Plan Version {activePlan.version}?</h3>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                Archiving this plan version will soft-delete it and remove it from active runs.
              </p>
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => setShowArchiveModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnDanger}`}
                  onClick={() => void handleArchive()}
                  disabled={archiveMutation.isPending}
                  id="confirm-archive-btn"
                >
                  {archiveMutation.isPending ? "Archiving..." : "Archive Plan"}
                </button>
              </div>
            </div>
          </div>
        )}
        {/* Modal: Execute Run */}
        {showRunModal && (
          <div className={styles.modalOverlay} role="dialog" aria-modal="true">
            <div className={styles.modal}>
              <h3 className={styles.modalTitle}>Launch Test Run</h3>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                Select an environment to execute this approved test plan.
              </p>
              {runDispatchError && (
                <div style={{ color: "#ef4444", marginBottom: "var(--space-2)", fontSize: "var(--font-size-sm)" }}>
                  {runDispatchError}
                </div>
              )}
              <div style={{ margin: "var(--space-4) 0", display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
                <label style={{ fontSize: "var(--font-size-xs)", fontWeight: 600 }}>Target Environment</label>
                <select
                  style={{
                    padding: "var(--space-2)",
                    borderRadius: "var(--radius-md)",
                    border: "1px solid var(--color-border)",
                    backgroundColor: "var(--color-surface, #1e293b)",
                    color: "inherit"
                  }}
                  value={selectedEnvId || environments[0]?.id || ""}
                  onChange={(e) => setSelectedEnvId(e.target.value)}
                >
                  {environments.length === 0 ? (
                    <option value="">No active environments available</option>
                  ) : (
                    environments.map((env) => (
                      <option key={env.id} value={env.id}>
                        {env.name} ({env.base_url})
                      </option>
                    ))
                  )}
                </select>
              </div>
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => setShowRunModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  onClick={() => void handleLaunchRun()}
                  disabled={createRunMutation.isPending || environments.length === 0}
                  id="confirm-launch-btn"
                >
                  {createRunMutation.isPending ? "Launching..." : "Launch Run →"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
}
