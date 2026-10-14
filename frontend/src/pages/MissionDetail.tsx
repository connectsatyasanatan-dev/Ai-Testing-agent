import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { DAGView } from "@/components/missions/DAGView";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/hooks/useAuth";
import { useEnvironments } from "@/hooks/useEnvironments";
import { useApproveMission, useCreateRun, useMissionDAG } from "@/hooks/useMissions";
import { usePlan } from "@/hooks/usePlans";
import { useProjects } from "@/hooks/useProjects";
import type { SuiteType } from "@/types/mission";
import styles from "./MissionDetail.module.css";

export default function MissionDetail() {
  const { projectId: urlProjectId, missionId } = useParams<{
    projectId?: string;
    missionId: string;
  }>();
  const navigate = useNavigate();

  const { data: projectList } = useProjects();
  const currentProjectId = urlProjectId || projectList?.items[0]?.id || "";

  const { user } = useAuth();
  const { data: envList } = useEnvironments(currentProjectId);

  const { data: missionPlan, isLoading: isLoadingDAG, isError: isDAGError, error: dagError, refetch: refetchDAG } =
    useMissionDAG(missionId);
  const { data: planRecord, isLoading: isLoadingPlan } = usePlan(missionId);

  const approveMutation = useApproveMission(missionId);
  const createRunMutation = useCreateRun(missionId ?? "");

  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [showRunModal, setShowRunModal] = useState(false);
  const [selectedEnvId, setSelectedEnvId] = useState("");
  const [selectedSuite, setSelectedSuite] = useState<SuiteType>("all");
  const [runDispatchError, setRunDispatchError] = useState<string | null>(null);

  const isApproved = planRecord?.status === "approved";
  const isDraft = planRecord?.status === "draft" || !isApproved;

  const handleApprove = async () => {
    setApprovalError(null);
    try {
      await approveMutation.mutateAsync("Approved via Web Console");
      await refetchDAG();
    } catch (err) {
      setApprovalError(
        err instanceof Error ? err.message : "Failed to approve mission plan. Verify authorization permissions."
      );
    }
  };

  const handleDispatchRun = async () => {
    setRunDispatchError(null);
    const envId = selectedEnvId || envList?.items[0]?.id;
    if (!envId) {
      setRunDispatchError("Please select a target environment for execution.");
      return;
    }

    try {
      const newRun = await createRunMutation.mutateAsync({
        environment_id: envId,
        suite_type: selectedSuite,
      });
      setShowRunModal(false);
      navigate(`/runs/${newRun.id}`);
    } catch (err) {
      setRunDispatchError(err instanceof Error ? err.message : "Failed to dispatch mission test run.");
    }
  };

  if (isLoadingDAG || isLoadingPlan) {
    return (
      <AppShell
        sidebar={<Sidebar currentProjectId={currentProjectId} />}
        header={<Header currentProjectId={currentProjectId} />}
      >
        <div style={{ padding: "var(--space-12)", display: "flex", justifyContent: "center" }}>
          <Spinner label="Loading mission DAG structure..." />
        </div>
      </AppShell>
    );
  }

  if (isDAGError || !missionPlan) {
    return (
      <AppShell
        sidebar={<Sidebar currentProjectId={currentProjectId} />}
        header={<Header currentProjectId={currentProjectId} />}
      >
        <div style={{ maxWidth: "800px", margin: "var(--space-6) auto" }}>
          <ErrorMessage
            title="Mission Plan Not Found"
            message={dagError?.message || "Could not retrieve mission DAG definition."}
          >
            <Button variant="secondary" onClick={() => { void refetchDAG(); }}>
              Retry
            </Button>
          </ErrorMessage>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell
      sidebar={<Sidebar currentProjectId={currentProjectId} />}
      header={<Header currentProjectId={currentProjectId} />}
    >
      <div className={styles.container}>
        {/* Header and Control Bar */}
        <div className={styles.headerCard}>
          <div className={styles.topRow}>
            <div className={styles.titleArea}>
              <div style={{ marginBottom: "var(--space-1)" }}>
                <Link
                  to={currentProjectId ? `/projects/${currentProjectId}/plans` : "/plans"}
                  style={{ fontSize: "var(--font-size-xs)", color: "var(--color-primary)", textDecoration: "none" }}
                >
                  ← Back to Plans
                </Link>
              </div>

              <div className={styles.titleWithBadge}>
                <h1 className={styles.title}>{missionPlan.name}</h1>
                <Badge variant={isApproved ? "approved" : "draft"}>
                  {isApproved ? "✓ Approved" : "Draft (Pending Approval)"}
                </Badge>
              </div>

              <p className={styles.description}>
                {missionPlan.description || "Autonomous multi-agent testing mission DAG."}
                {user && (
                  <span style={{ display: "block", marginTop: "4px", fontSize: "12px", opacity: 0.8 }}>
                    Current operator: <strong>{user.email}</strong>
                  </span>
                )}
              </p>
            </div>

            <div className={styles.actions}>
              {isDraft && (
                <button
                  type="button"
                  className={styles.btnApprove}
                  onClick={() => { void handleApprove(); }}
                  disabled={approveMutation.isPending}
                  id="approve-mission-btn"
                  title="Approve this mission DAG version for execution"
                >
                  <span>✅</span>
                  <span>{approveMutation.isPending ? "Approving..." : "Approve Mission DAG"}</span>
                </button>
              )}

              {isApproved && (
                <button
                  type="button"
                  className={styles.btnExecute}
                  onClick={() => setShowRunModal(true)}
                  id="execute-mission-btn"
                >
                  <span>▶️</span>
                  <span>Execute Test Run...</span>
                </button>
              )}
            </div>
          </div>

          {approvalError && (
            <div role="alert" style={{ color: "#b91c1c", fontSize: "13px", marginTop: "var(--space-2)" }}>
              ⚠️ {approvalError}
            </div>
          )}

          {/* Banner explaining gate status */}
          {isDraft ? (
            <div className={styles.unapprovedBanner} role="status">
              <span>🔒</span>
              <div>
                <strong>Draft Mission Plan:</strong> This DAG plan is unapproved. Execution cannot be dispatched until
                an authorized project maintainer or owner reviews the task DAG and approves it.
              </div>
            </div>
          ) : (
            <div className={styles.approvedBanner} role="status">
              <span>✅</span>
              <div>
                <strong>Mission Approved:</strong> This DAG plan version has passed human review and is authorized for
                execution.
              </div>
            </div>
          )}

          {/* Metadata bar */}
          <div className={styles.metaBar}>
            <div className={styles.metaItem}>
              <span>Mission ID:</span>
              <strong style={{ fontFamily: "var(--font-mono)" }}>{missionPlan.mission_id}</strong>
            </div>

            <div className={styles.metaItem}>
              <span>Adapter:</span>
              <strong>Multi-Agent DAG</strong>
            </div>

            <div className={styles.metaItem}>
              <span>Tasks:</span>
              <strong>{missionPlan.tasks.length}</strong>
            </div>

            <div className={styles.metaItem}>
              <span>Parallelism:</span>
              <strong>{missionPlan.max_parallel_tasks} workers</strong>
            </div>

            {planRecord?.approved_at && (
              <div className={styles.metaItem} style={{ color: "#16a34a" }}>
                <span>Approved:</span>
                <strong>{new Date(planRecord.approved_at).toLocaleString()}</strong>
              </div>
            )}
          </div>
        </div>

        {/* DAG Tasks & Dependency Graph View */}
        <DAGView plan={missionPlan} />

        {/* Execute Run Modal */}
        {showRunModal && (
          <div
            className={styles.modalBackdrop}
            role="dialog"
            aria-modal="true"
            aria-labelledby="run-dialog-title"
          >
            <div className={styles.modalCard}>
              <h2 id="run-dialog-title" className={styles.modalTitle}>
                Execute Mission Test Run
              </h2>

              {runDispatchError && (
                <div role="alert" style={{ color: "#b91c1c", fontSize: "13px" }}>
                  ⚠️ {runDispatchError}
                </div>
              )}

              <div>
                <label
                  htmlFor="run-env-select"
                  style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "4px" }}
                >
                  Target Environment:
                </label>
                <select
                  id="run-env-select"
                  value={selectedEnvId || envList?.items[0]?.id || ""}
                  onChange={(e) => setSelectedEnvId(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    borderRadius: "6px",
                    border: "1px solid #d1d5db",
                    fontSize: "14px",
                  }}
                >
                  {envList?.items?.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name} ({env.base_url})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label style={{ display: "block", fontSize: "13px", fontWeight: 600, marginBottom: "8px" }}>
                  Test Suite Selection:
                </label>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <div
                    className={`${styles.suiteOption} ${selectedSuite === "all" ? styles.suiteOptionActive : ""}`}
                    onClick={() => setSelectedSuite("all")}
                    tabIndex={0}
                    role="radio"
                    aria-checked={selectedSuite === "all"}
                  >
                    <span>🌐</span>
                    <div>
                      <strong style={{ fontSize: "13px" }}>Full Mission Suite (All Tasks)</strong>
                      <div style={{ fontSize: "12px", color: "#6b7280" }}>
                        Executes all tasks in the DAG in topological order.
                      </div>
                    </div>
                  </div>

                  <div
                    className={`${styles.suiteOption} ${selectedSuite === "smoke" ? styles.suiteOptionActive : ""}`}
                    onClick={() => setSelectedSuite("smoke")}
                    tabIndex={0}
                    role="radio"
                    aria-checked={selectedSuite === "smoke"}
                  >
                    <span>🔥</span>
                    <div>
                      <strong style={{ fontSize: "13px" }}>Smoke Only (Critical P0 Journeys)</strong>
                      <div style={{ fontSize: "12px", color: "#6b7280" }}>
                        Executes only critical P0 tasks and their prerequisites, skipping non-critical tests.
                      </div>
                    </div>
                  </div>

                  <div
                    className={`${styles.suiteOption} ${selectedSuite === "regression" ? styles.suiteOptionActive : ""}`}
                    onClick={() => setSelectedSuite("regression")}
                    tabIndex={0}
                    role="radio"
                    aria-checked={selectedSuite === "regression"}
                  >
                    <span>🔄</span>
                    <div>
                      <strong style={{ fontSize: "13px" }}>Regression Suite</strong>
                      <div style={{ fontSize: "12px", color: "#6b7280" }}>
                        Executes regression-eligible tasks across user journeys.
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "8px" }}>
                <Button variant="secondary" onClick={() => setShowRunModal(false)}>
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  onClick={() => { void handleDispatchRun(); }}
                  disabled={createRunMutation.isPending}
                  id="confirm-dispatch-run-btn"
                >
                  {createRunMutation.isPending ? "Dispatching..." : "Launch Run →"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
