import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { useRunExecutionLog, useRunReport } from "@/hooks/useMissions";
import type { MissionEvaluationReport } from "@/types/mission";
import styles from "./MissionEvaluationView.module.css";

interface MissionEvaluationViewProps {
  runId: string;
  evaluation: MissionEvaluationReport;
}

export function MissionEvaluationView({ runId, evaluation }: MissionEvaluationViewProps) {
  const [showReportModal, setShowReportModal] = useState(false);
  const [showLogModal, setShowLogModal] = useState(false);

  const { data: reportText, isLoading: isLoadingReport } = useRunReport(runId, showReportModal);
  const { data: logJson, isLoading: isLoadingLog } = useRunExecutionLog(runId, showLogModal);

  const verdict = evaluation.verdict.toUpperCase();
  const isPassing = verdict === "PASSED";

  return (
    <div className={styles.container} aria-label="Mission Evaluation Results">
      {/* 1. Executive Summary & Verdict Card */}
      <div className={styles.evalCard}>
        <div className={styles.headerRow}>
          <div className={styles.titleArea}>
            <span style={{ fontSize: "20px" }}>{isPassing ? "🎯" : "⚠️"}</span>
            <h2 className={styles.title}>Evaluator Assessment & Verdict</h2>
            <span
              className={isPassing ? styles.statusPassed : styles.statusFailed}
              style={{ fontSize: "13px", padding: "4px 10px" }}
            >
              {verdict}
            </span>
          </div>

          {evaluation.is_idempotent_replay && (
            <span style={{ fontSize: "12px", color: "var(--color-text-muted)" }}>
              ⚡ Cached Replay (Idempotent)
            </span>
          )}
        </div>

        {/* Counter Tiles */}
        <div className={styles.metricsGrid}>
          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "var(--color-text-primary)" }}>
              {evaluation.total_tasks}
            </span>
            <span className={styles.metricLabel}>Total Tasks</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#16a34a" }}>
              {evaluation.passed_tasks}
            </span>
            <span className={styles.metricLabel}>Passed</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#dc2626" }}>
              {evaluation.failed_tasks}
            </span>
            <span className={styles.metricLabel}>Failed</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#ea580c" }}>
              {evaluation.blocked_tasks}
            </span>
            <span className={styles.metricLabel}>Blocked</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#6b7280" }}>
              {evaluation.skipped_tasks ?? 0}
            </span>
            <span className={styles.metricLabel}>Skipped</span>
          </div>
        </div>

        {/* Executive Summary Callout */}
        {evaluation.executive_summary && (
          <div className={styles.execSummary} role="region" aria-label="Executive Summary">
            <strong>Evaluator Assessment: </strong>
            <span>{evaluation.executive_summary}</span>
          </div>
        )}

        {/* Deliverables & Exports Toolbar */}
        <div className={styles.deliverablesBar}>
          <span style={{ fontSize: "13px", fontWeight: 600, color: "#334155" }}>
            Exported Deliverables:
          </span>

          <button
            type="button"
            className="btn btn-secondary"
            style={{
              padding: "5px 12px",
              fontSize: "12px",
              fontWeight: 600,
              backgroundColor: "#fff",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              cursor: "pointer",
            }}
            onClick={() => setShowReportModal(true)}
            id="view-report-md-btn"
          >
            📄 View report.md
          </button>

          <button
            type="button"
            className="btn btn-secondary"
            style={{
              padding: "5px 12px",
              fontSize: "12px",
              fontWeight: 600,
              backgroundColor: "#fff",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              cursor: "pointer",
            }}
            onClick={() => setShowLogModal(true)}
            id="view-execution-log-btn"
          >
            📋 View execution_log.json
          </button>

          <a
            href={`/api/v1/runs/${runId}/export/report`}
            download={`report_${runId.slice(0, 8)}.md`}
            style={{ fontSize: "12px", color: "var(--color-primary)", textDecoration: "none", marginLeft: "auto" }}
          >
            Download Markdown Report ↗
          </a>
        </div>
      </div>

      {/* 2. Requirement Traceability Matrix */}
      <div className={styles.evalCard}>
        <h3 className={styles.title} style={{ fontSize: "16px" }}>
          Requirement Traceability Matrix ({evaluation.requirement_coverage?.length ?? 0})
        </h3>

        {evaluation.requirement_coverage && evaluation.requirement_coverage.length > 0 ? (
          <div className={styles.tableWrapper}>
            <table className={styles.table} aria-label="Requirement Coverage Table">
              <thead>
                <tr>
                  <th scope="col">Requirement ID</th>
                  <th scope="col">Status</th>
                  <th scope="col">Verifying Tasks</th>
                  <th scope="col">Evaluator Notes & Diagnostics</th>
                </tr>
              </thead>
              <tbody>
                {evaluation.requirement_coverage.map((req) => {
                  const statusClass =
                    req.status === "PASSED"
                      ? styles.statusPassed
                      : req.status === "FAILED"
                      ? styles.statusFailed
                      : req.status === "BLOCKED"
                      ? styles.statusBlocked
                      : styles.statusUntested;

                  return (
                    <tr key={req.requirement_id}>
                      <td style={{ fontWeight: 600, fontFamily: "var(--font-mono)" }}>
                        {req.requirement_id}
                      </td>
                      <td>
                        <span className={statusClass}>{req.status}</span>
                      </td>
                      <td>
                        {req.tested_by_tasks && req.tested_by_tasks.length > 0 ? (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                            {req.tested_by_tasks.map((t) => (
                              <span
                                key={t}
                                style={{
                                  fontSize: "11px",
                                  fontFamily: "var(--font-mono)",
                                  backgroundColor: "#f1f5f9",
                                  padding: "1px 5px",
                                  borderRadius: "4px",
                                }}
                              >
                                {t}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span style={{ color: "var(--color-text-muted)" }}>None</span>
                        )}
                      </td>
                      <td style={{ color: "var(--color-text-secondary)" }}>
                        {req.notes || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ color: "var(--color-text-muted)", fontSize: "13px" }}>
            No declared requirements were tracked in this evaluation.
          </div>
        )}
      </div>

      {/* 3. Evidence-Grounded Root Cause Analysis (if any failures / blocked tasks) */}
      {evaluation.root_cause_analysis && evaluation.root_cause_analysis.length > 0 && (
        <div className={styles.evalCard}>
          <h3 className={styles.title} style={{ fontSize: "16px", color: "#b91c1c" }}>
            Root Cause Analysis & Diagnostics ({evaluation.root_cause_analysis.length})
          </h3>

          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            {evaluation.root_cause_analysis.map((rc, idx) => (
              <div key={idx} className={styles.rcaCard}>
                <div className={styles.rcaHeader}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <strong style={{ fontSize: "14px" }}>Task: {rc.task_id}</strong>
                    <span className={rc.status === "failed" ? styles.statusFailed : styles.statusBlocked}>
                      {rc.status.toUpperCase()}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        backgroundColor: rc.cause_type === "confirmed" ? "#fee2e2" : "#fef3c7",
                        color: rc.cause_type === "confirmed" ? "#991b1b" : "#92400e",
                        padding: "1px 6px",
                        borderRadius: "4px",
                      }}
                    >
                      {rc.classification || rc.cause_type}
                    </span>
                    {rc.is_ai_generated && (
                      <span
                        style={{
                          fontSize: "11px",
                          backgroundColor: "#eff6ff",
                          color: "#1d4ed8",
                          padding: "1px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        🤖 AI Hypothesis
                      </span>
                    )}
                  </div>
                </div>

                <div className={styles.rcaDiagnostic}>{rc.diagnostic}</div>

                {rc.suggested_debugging_steps && rc.suggested_debugging_steps.length > 0 && (
                  <div style={{ fontSize: "12px", color: "#374151" }}>
                    <strong>Suggested Remediation:</strong>
                    <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                      {rc.suggested_debugging_steps.map((step, sIdx) => (
                        <li key={sIdx}>{step}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {rc.evidence_references && rc.evidence_references.length > 0 && (
                  <div style={{ fontSize: "12px", color: "#4b5563" }}>
                    <strong>Captured Evidence Artifacts: </strong>
                    {rc.evidence_references.map((refId, rIdx) => (
                      <span
                        key={rIdx}
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: "11px",
                          backgroundColor: "#f3f4f6",
                          padding: "1px 5px",
                          borderRadius: "4px",
                          marginRight: "6px",
                        }}
                      >
                        {refId}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Report.md Preview Modal */}
      {showReportModal && (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true">
          <div className={styles.previewCard}>
            <div className={styles.previewHeader}>
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700 }}>
                Deliverable Preview: /output/report.md
              </h3>
              <Button variant="secondary" size="sm" onClick={() => setShowReportModal(false)}>
                ✕ Close
              </Button>
            </div>
            <div className={styles.previewBody}>
              {isLoadingReport ? "Loading report.md artifact..." : reportText || "Report empty."}
            </div>
          </div>
        </div>
      )}

      {/* Execution Log Preview Modal */}
      {showLogModal && (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true">
          <div className={styles.previewCard}>
            <div className={styles.previewHeader}>
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700 }}>
                Deliverable Preview: /output/execution_log.json
              </h3>
              <Button variant="secondary" size="sm" onClick={() => setShowLogModal(false)}>
                ✕ Close
              </Button>
            </div>
            <div className={styles.previewBody}>
              {isLoadingLog
                ? "Loading execution_log.json artifact..."
                : JSON.stringify(logJson, null, 2) || "Log empty."}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
