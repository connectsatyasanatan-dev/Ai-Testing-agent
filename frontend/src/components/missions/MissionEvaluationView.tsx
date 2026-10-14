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
            <span className={isPassing ? styles.statusPassed : styles.statusFailed}>
              {verdict}
            </span>
          </div>

          {evaluation.is_idempotent_replay && (
            <span style={{ fontSize: "12px", color: "#94a3b8" }}>
              ⚡ Cached Replay (Idempotent)
            </span>
          )}
        </div>

        {/* Counter Tiles */}
        <div className={styles.metricsGrid}>
          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#f8fafc" }}>
              {evaluation.total_tasks}
            </span>
            <span className={styles.metricLabel}>Total Tasks</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#4ade80" }}>
              {evaluation.passed_tasks}
            </span>
            <span className={styles.metricLabel}>Passed</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#f87171" }}>
              {evaluation.failed_tasks}
            </span>
            <span className={styles.metricLabel}>Failed</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#fbbf24" }}>
              {evaluation.blocked_tasks}
            </span>
            <span className={styles.metricLabel}>Blocked</span>
          </div>

          <div className={styles.metricTile}>
            <span className={styles.metricVal} style={{ color: "#94a3b8" }}>
              {evaluation.skipped_tasks ?? 0}
            </span>
            <span className={styles.metricLabel}>Skipped</span>
          </div>
        </div>

        {/* Executive Summary Callout */}
        {evaluation.executive_summary && (
          <div
            className={`${styles.execSummary} ${isPassing ? styles.execSummaryPassed : styles.execSummaryFailed}`}
            role="region"
            aria-label="Executive Summary"
          >
            <strong>Evaluator Assessment: </strong>
            <span>{evaluation.executive_summary}</span>
          </div>
        )}

        {/* Deliverables & Exports Toolbar */}
        <div className={styles.deliverablesBar}>
          <span className={styles.deliverableLabel}>
            Reported Deliverables:
          </span>

          <button
            type="button"
            className={styles.deliverableBtn}
            onClick={() => setShowReportModal(true)}
            id="view-report-md-btn"
          >
            📄 View report.md
          </button>

          <button
            type="button"
            className={styles.deliverableBtn}
            onClick={() => setShowLogModal(true)}
            id="view-execution-log-btn"
          >
            📋 View execution_log.json
          </button>

          <a
            href={`/api/v1/runs/${runId}/export/report`}
            download={`report_${runId.slice(0, 8)}.md`}
            className={styles.downloadLink}
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
                      <td style={{ fontWeight: 600, fontFamily: "var(--font-mono)", color: "#f8fafc" }}>
                        {req.requirement_id}
                      </td>
                      <td>
                        <span className={statusClass}>{req.status}</span>
                      </td>
                      <td>
                        {req.tested_by_tasks && req.tested_by_tasks.length > 0 ? (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                            {req.tested_by_tasks.map((t) => (
                              <span key={t} className={styles.taskTag}>
                                {t}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span style={{ color: "#64748b" }}>None</span>
                        )}
                      </td>
                      <td style={{ color: "#94a3b8" }}>
                        {req.notes || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ color: "#94a3b8", fontSize: "13px" }}>
            No declared requirements were tracked in this evaluation.
          </div>
        )}
      </div>

      {/* 3. Evidence-Grounded Root Cause Analysis */}
      {evaluation.root_cause_analysis && evaluation.root_cause_analysis.length > 0 && (
        <div className={styles.evalCard}>
          <h3 className={styles.title} style={{ fontSize: "16px", color: "#f87171" }}>
            Root Cause Analysis & Diagnostics ({evaluation.root_cause_analysis.length})
          </h3>

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {evaluation.root_cause_analysis.map((rc, idx) => (
              <div key={idx} className={styles.rcaCard}>
                <div className={styles.rcaHeader}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                    <strong style={{ fontSize: "14px", color: "#f1f5f9" }}>Task: {rc.task_id}</strong>
                    <span className={rc.status === "failed" ? styles.statusFailed : styles.statusBlocked}>
                      {rc.status.toUpperCase()}
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        backgroundColor: rc.cause_type === "confirmed" ? "rgba(239, 68, 68, 0.15)" : "rgba(245, 158, 11, 0.15)",
                        color: rc.cause_type === "confirmed" ? "#fca5a5" : "#fcd34d",
                        border: `1px solid ${rc.cause_type === "confirmed" ? "rgba(239, 68, 68, 0.3)" : "rgba(245, 158, 11, 0.3)"}`,
                        padding: "2px 8px",
                        borderRadius: "4px",
                      }}
                    >
                      {rc.classification || rc.cause_type}
                    </span>
                    {rc.is_ai_generated && (
                      <span
                        style={{
                          fontSize: "11px",
                          backgroundColor: "rgba(59, 130, 246, 0.15)",
                          color: "#93c5fd",
                          border: "1px solid rgba(59, 130, 246, 0.3)",
                          padding: "2px 8px",
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
                  <div className={styles.remediationArea}>
                    <strong>Suggested Remediation:</strong>
                    <ul>
                      {rc.suggested_debugging_steps.map((step, sIdx) => (
                        <li key={sIdx}>{step}</li>
                      ))}
                    </ul>
                  </div>
                )}

                {rc.evidence_references && rc.evidence_references.length > 0 && (
                  <div className={styles.evidenceArea}>
                    <strong>Captured Evidence Artifacts: </strong>
                    <div>
                      {rc.evidence_references.map((refId, rIdx) => (
                        <span key={rIdx} className={styles.evidenceChip}>
                          {refId}
                        </span>
                      ))}
                    </div>
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
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#f8fafc" }}>
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
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#f8fafc" }}>
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
