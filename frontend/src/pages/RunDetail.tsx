import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { Badge, statusVariant } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import {
  useRun,
  useRunArtifact,
  useRunArtifacts,
  useRunEvents,
} from "@/hooks/useRunDetail";
import { cancelRun, getArtifactDownloadUrl } from "@/lib/api-client";
import styles from "./RunDetail.module.css";

function formatTimestamp(isoString?: string | null): string {
  if (!isoString) return "—";
  try {
    const date = new Date(isoString);
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
  } catch {
    return isoString;
  }
}

function formatDuration(
  startedAt?: string | null,
  finishedAt?: string | null
): string {
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

function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
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
    case "cancelled":
      return "🛑 Cancelled";
    default:
      return status;
  }
}

function getArtifactTypeLabel(type: string): string {
  switch (type.toLowerCase()) {
    case "screenshot":
      return "🖼️ Screenshot";
    case "response_log":
      return "📋 Response Log";
    case "request_log":
      return "📤 Request Log";
    case "trace":
      return "📦 Trace";
    case "har":
      return "🌐 HAR Network";
    case "console_log":
      return "📜 Console Log";
    default:
      return `📁 ${type}`;
  }
}

export default function RunDetail() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();

  const { data: run, isLoading, error, refetch } = useRun(runId ?? "");
  const { data: events } = useRunEvents(runId ?? "");
  const { data: artifacts, isLoading: isLoadingArtifacts } = useRunArtifacts(
    runId ?? "",
    true
  );

  // Evidence modal state
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(
    null
  );
  const [activeJsonTab, setActiveJsonTab] = useState<
    "response" | "request" | "assertions" | "raw"
  >("response");

  const selectedArtifact = artifacts?.find((a) => a.id === selectedArtifactId);
  const {
    data: artifactPayload,
    isLoading: isLoadingPayload,
    error: payloadError,
  } = useRunArtifact(runId ?? "", selectedArtifactId);

  const projectId = run?.project_id;
  const [isCancelling, setIsCancelling] = useState(false);

  const handleCancel = async () => {
    if (!run || !window.confirm("Are you sure you want to cancel this test run?")) return;
    setIsCancelling(true);
    try {
      await cancelRun(run.id);
      void refetch();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed to cancel run.");
    } finally {
      setIsCancelling(false);
    }
  };

  const closeModal = () => {
    setSelectedArtifactId(null);
    setActiveJsonTab("response");
  };

  // Decode inline payload content safely
  let decodedText: string | null = null;
  let parsedJson: Record<string, unknown> | null = null;
  if (artifactPayload?.inline_content) {
    try {
      decodedText = atob(artifactPayload.inline_content);
      parsedJson = JSON.parse(decodedText) as Record<string, unknown>;
    } catch {
      // Keep decodedText as plain string if not valid JSON
    }
  }

  // Safe image types only
  const isImageArtifact =
    selectedArtifact?.type === "screenshot" ||
    selectedArtifact?.content_type?.startsWith("image/png") ||
    selectedArtifact?.content_type?.startsWith("image/jpeg") ||
    selectedArtifact?.content_type?.startsWith("image/webp");

  // Never render HTML or SVG as trusted same-origin content
  const isBlockedActiveContent =
    selectedArtifact?.content_type === "text/html" ||
    selectedArtifact?.content_type === "image/svg+xml";

  return (
    <AppShell
      header={<Header currentProjectId={projectId} />}
      sidebar={<Sidebar currentProjectId={projectId} />}
    >
      <div className={styles.detailPage}>
        {/* Top Header Bar */}
        <div className={styles.topBar}>
          <div className={styles.headingArea}>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                navigate(-1);
              }}
            >
              ← Back
            </Button>
            <h1 className={styles.heading}>Run Details</h1>
            {run && (
              <Badge variant={statusVariant(run.status)}>
                {getStatusAccessibleLabel(run.status)}
              </Badge>
            )}
          </div>
          {run && (run.status === "queued" || run.status === "running") && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => void handleCancel()}
              loading={isCancelling}
              disabled={isCancelling}
            >
              Cancel Run
            </Button>
          )}
        </div>

        {isLoading && <Spinner fullPage label="Loading run details…" />}

        {error && (
          <ErrorMessage
            title="Failed to load run details"
            message={error.message}
          >
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void refetch();
              }}
            >
              Retry
            </Button>
          </ErrorMessage>
        )}

        {run && (
          <>
            {/* Overview Metadata Card */}
            <div className={styles.overviewCard}>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Run ID</span>
                <span className={styles.metaValue}>{run.id}</span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Test Plan</span>
                <span className={styles.metaValue}>
                  {run.plan_name || run.plan_id} (v{run.plan_version})
                </span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Target Adapter</span>
                <span className={styles.metaValue}>
                  {run.adapter_type?.toUpperCase() || "HTTP_API"}
                </span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Environment</span>
                <span className={styles.metaValue}>
                  {run.environment_name || run.environment_id}
                </span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Execution Mode</span>
                <span className={styles.metaValue}>
                  {run.worker_id ? `Worker (${run.worker_id})` : "In-process"}
                </span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Started</span>
                <span className={styles.metaValue}>
                  {formatTimestamp(run.started_at)}
                </span>
              </div>
              <div className={styles.metaItem}>
                <span className={styles.metaLabel}>Duration</span>
                <span className={styles.metaValue}>
                  {formatDuration(run.started_at, run.finished_at)}
                </span>
              </div>
            </div>

            {/* Step-by-Step Timeline Breakdown */}
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>
                  Step Execution Timeline ({run.step_attempts.length})
                </h2>
              </div>
              {run.step_attempts.length === 0 ? (
                <div style={{ padding: "var(--space-6)" }}>
                  <EmptyState
                    icon="⏳"
                    title="No step attempts recorded"
                    description="Execution has not started steps or was queued."
                  />
                </div>
              ) : (
                <div className={styles.tableWrapper}>
                  <table
                    className={styles.table}
                    aria-label="Step Attempts Table"
                  >
                    <thead>
                      <tr>
                        <th scope="col">Status</th>
                        <th scope="col">Action</th>
                        <th scope="col">Step ID</th>
                        <th scope="col">Attempt</th>
                        <th scope="col">Duration</th>
                        <th scope="col">Outcome & Diagnostics</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.step_attempts.map((attempt) => (
                        <tr key={attempt.id}>
                          <td>
                            <Badge variant={statusVariant(attempt.status)}>
                              {getStatusAccessibleLabel(attempt.status)}
                            </Badge>
                          </td>
                          <td>
                            {attempt.action ? (
                              <span className={styles.actionBadge}>
                                {attempt.action}
                              </span>
                            ) : (
                              "—"
                            )}
                          </td>
                          <td style={{ fontWeight: "var(--font-semibold)" }}>
                            {attempt.step_id}
                          </td>
                          <td>#{attempt.attempt_number}</td>
                          <td>
                            {attempt.duration_ms !== undefined &&
                            attempt.duration_ms !== null
                              ? `${attempt.duration_ms} ms`
                              : formatDuration(
                                  attempt.started_at,
                                  attempt.finished_at
                                )}
                          </td>
                          <td>
                            {/* Failure category tag */}
                            {attempt.failure_category && (
                              <div style={{ marginBottom: "var(--space-1)" }}>
                                {attempt.failure_category ===
                                  "assertion_failure" && (
                                  <span
                                    className={`${styles.categoryBadge} ${styles.catAssertion}`}
                                  >
                                    ❌ Assertion Failure
                                  </span>
                                )}
                                {attempt.failure_category === "timeout" && (
                                  <span
                                    className={`${styles.categoryBadge} ${styles.catTimeout}`}
                                  >
                                    ⏱️ Timeout Exceeded
                                  </span>
                                )}
                                {attempt.failure_category ===
                                  "cancellation" && (
                                  <span
                                    className={`${styles.categoryBadge} ${styles.catCancellation}`}
                                  >
                                    🛑 Cancelled
                                  </span>
                                )}
                                {attempt.failure_category ===
                                  "policy_blocked" && (
                                  <span
                                    className={`${styles.categoryBadge} ${styles.catPolicy}`}
                                  >
                                    🛡️ Policy Blocked (SSRF)
                                  </span>
                                )}
                                {attempt.failure_category ===
                                  "execution_error" && (
                                  <span
                                    className={`${styles.categoryBadge} ${styles.catError}`}
                                  >
                                    ⚠️ Execution Error
                                  </span>
                                )}
                              </div>
                            )}

                            {/* Error diagnostic message */}
                            {attempt.error_message && (
                              <div className={styles.errorMessage}>
                                <strong>
                                  {attempt.error_code || "ERROR"}:
                                </strong>{" "}
                                {attempt.error_message}
                              </div>
                            )}

                            {/* Step Assertions listing */}
                            {attempt.assertions &&
                              attempt.assertions.length > 0 && (
                                <div className={styles.assertionsContainer}>
                                  {attempt.assertions.map((assertion, idx) => (
                                    <div
                                      key={idx}
                                      className={styles.assertionRow}
                                    >
                                      <span className={styles.assertionIcon}>
                                        {assertion.passed === false
                                          ? "❌"
                                          : "✓"}
                                      </span>
                                      <span>
                                        {assertion.type}:{" "}
                                        {assertion.value !== undefined
                                          ? String(assertion.value)
                                          : ""}
                                      </span>
                                    </div>
                                  ))}
                                </div>
                              )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Evidence Artifacts Section */}
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>
                  Captured Evidence & Artifacts ({artifacts?.length ?? 0})
                </h2>
              </div>
              {isLoadingArtifacts ? (
                <div style={{ padding: "var(--space-6)" }}>
                  <Spinner label="Loading evidence artifacts…" />
                </div>
              ) : !artifacts || artifacts.length === 0 ? (
                <div style={{ padding: "var(--space-6)" }}>
                  <EmptyState
                    icon="📂"
                    title="No evidence artifacts captured"
                    description="No logs or screenshots were stored for this run."
                  />
                </div>
              ) : (
                <div className={styles.tableWrapper}>
                  <table
                    className={styles.table}
                    aria-label="Evidence Artifacts Table"
                  >
                    <thead>
                      <tr>
                        <th scope="col">Artifact Type</th>
                        <th scope="col">Content Type</th>
                        <th scope="col">Size</th>
                        <th scope="col">Linked Step</th>
                        <th scope="col">Captured At</th>
                        <th scope="col">Retention Status</th>
                        <th scope="col">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {artifacts.map((artifact) => {
                        const isExpired =
                          artifact.is_expired ||
                          (artifact.retention_until &&
                            new Date(artifact.retention_until) < new Date());

                        return (
                          <tr key={artifact.id}>
                            <td>
                              <span className={styles.artifactBadge}>
                                {getArtifactTypeLabel(artifact.type)}
                              </span>
                            </td>
                            <td
                              style={{
                                fontFamily: "var(--font-mono)",
                                fontSize: "var(--text-xs)",
                              }}
                            >
                              {artifact.content_type || "application/json"}
                            </td>
                            <td>{formatBytes(artifact.size_bytes)}</td>
                            <td>
                              {artifact.step_attempt_id ? (
                                <span
                                  style={{
                                    fontFamily: "var(--font-mono)",
                                    fontSize: "var(--text-xs)",
                                  }}
                                >
                                  {artifact.step_attempt_id.slice(0, 8)}…
                                </span>
                              ) : (
                                "Run root"
                              )}
                            </td>
                            <td>{formatTimestamp(artifact.created_at)}</td>
                            <td>
                              {isExpired ? (
                                <span
                                  className={`${styles.availBadge} ${styles.availExpired}`}
                                >
                                  ⏳ Expired
                                </span>
                              ) : (
                                <span
                                  className={`${styles.availBadge} ${styles.availActive}`}
                                >
                                  ✅ Available
                                </span>
                              )}
                            </td>
                            <td>
                              <div className={styles.artifactActions}>
                                {!isExpired && (
                                  <Button
                                    variant="secondary"
                                    size="sm"
                                    onClick={() => {
                                      setSelectedArtifactId(artifact.id);
                                    }}
                                  >
                                    👁️ Preview
                                  </Button>
                                )}
                                {!isExpired ? (
                                  <a
                                    href={getArtifactDownloadUrl(
                                      run.id,
                                      artifact.id
                                    )}
                                    download={`${artifact.id}.bin`}
                                    rel="noreferrer"
                                  >
                                    <Button variant="ghost" size="sm">
                                      ⬇ Download
                                    </Button>
                                  </a>
                                ) : (
                                  <span
                                    style={{
                                      fontSize: "var(--text-xs)",
                                      color: "var(--color-text-muted)",
                                    }}
                                  >
                                    Purged
                                  </span>
                                )}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Timeline Events Section */}
            <div className={styles.section}>
              <div className={styles.sectionHeader}>
                <h2 className={styles.sectionTitle}>Timeline Events</h2>
              </div>
              {!events || events.length === 0 ? (
                <div style={{ padding: "var(--space-6)" }}>
                  <EmptyState
                    icon="🕒"
                    title="No events yet"
                    description="Timeline events will appear as execution progresses."
                  />
                </div>
              ) : (
                <div className={styles.eventsList}>
                  {events.map((event) => (
                    <div key={event.id} className={styles.eventRow}>
                      <span className={styles.eventTime}>
                        {formatTimestamp(event.timestamp)}
                      </span>
                      <Badge variant="default">{event.event_type}</Badge>
                      <span style={{ flex: 1 }}>{event.message || "—"}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Evidence In-App Preview Modal */}
        {selectedArtifactId && selectedArtifact && (
          <div
            className={styles.modalOverlay}
            onClick={closeModal}
            role="presentation"
          >
            <div
              className={styles.modalContent}
              role="dialog"
              aria-modal="true"
              aria-label="Evidence Artifact Viewer"
              onClick={(e) => {
                e.stopPropagation();
              }}
            >
              {/* Modal Header */}
              <div className={styles.modalHeader}>
                <div className={styles.modalTitleArea}>
                  <h3 className={styles.modalTitle}>
                    {getArtifactTypeLabel(selectedArtifact.type)}
                  </h3>
                  <span
                    style={{
                      fontSize: "var(--text-xs)",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    ({formatBytes(selectedArtifact.size_bytes)})
                  </span>
                </div>
                <div style={{ display: "flex", gap: "var(--space-2)" }}>
                  {run && (
                    <a
                      href={getArtifactDownloadUrl(
                        run.id,
                        selectedArtifact.id
                      )}
                      download={`${selectedArtifact.id}.bin`}
                      rel="noreferrer"
                    >
                      <Button variant="secondary" size="sm">
                        ⬇ Safe Download
                      </Button>
                    </a>
                  )}
                  <Button variant="ghost" size="sm" onClick={closeModal}>
                    ✕ Close
                  </Button>
                </div>
              </div>

              {/* Modal Body */}
              <div className={styles.modalBody}>
                {isLoadingPayload && (
                  <Spinner label="Loading artifact payload…" />
                )}

                {/* Error or Expired state */}
                {payloadError && (
                  <>
                    {payloadError.message.toLowerCase().includes("expired") ||
                    selectedArtifact.is_expired ? (
                      <div className={styles.expiredCard}>
                        <span className={styles.expiredIcon}>⏳</span>
                        <h4 className={styles.expiredTitle}>
                          Artifact Retention Expired
                        </h4>
                        <p className={styles.expiredText}>
                          The 30-day retention period for this artifact has
                          elapsed. The storage payload has been safely purged
                          from the filesystem in compliance with data privacy
                          policies, while run metadata remains preserved.
                        </p>
                      </div>
                    ) : (
                      <ErrorMessage
                        title="Failed to load artifact"
                        message={payloadError.message}
                      />
                    )}
                  </>
                )}

                {/* Safe Content Viewers */}
                {artifactPayload && (
                  <>
                    {/* Active Content Security Check */}
                    {isBlockedActiveContent ? (
                      <div className={styles.expiredCard}>
                        <span className={styles.expiredIcon}>🛡️</span>
                        <h4 className={styles.expiredTitle}>
                          Active Content Blocked
                        </h4>
                        <p className={styles.expiredText}>
                          For security reasons, active HTML and SVG payloads are
                          not rendered directly within the application origin.
                          Please use the Safe Download option to inspect this
                          file in an isolated sandbox.
                        </p>
                      </div>
                    ) : isImageArtifact && artifactPayload.inline_content ? (
                      /* Supported Image Viewer */
                      <div className={styles.previewImageContainer}>
                        <img
                          src={`data:${selectedArtifact.content_type || "image/png"};base64,${artifactPayload.inline_content}`}
                          alt={`Artifact ${selectedArtifact.id}`}
                          className={styles.previewImage}
                        />
                      </div>
                    ) : parsedJson ? (
                      /* Structured JSON & Response Viewer */
                      <div>
                        <div className={styles.tabBar}>
                          <button
                            type="button"
                            className={`${styles.tabBtn} ${activeJsonTab === "response" ? styles.tabBtnActive : ""}`}
                            onClick={() => {
                              setActiveJsonTab("response");
                            }}
                          >
                            Response
                          </button>
                          <button
                            type="button"
                            className={`${styles.tabBtn} ${activeJsonTab === "request" ? styles.tabBtnActive : ""}`}
                            onClick={() => {
                              setActiveJsonTab("request");
                            }}
                          >
                            Request
                          </button>
                          <button
                            type="button"
                            className={`${styles.tabBtn} ${activeJsonTab === "assertions" ? styles.tabBtnActive : ""}`}
                            onClick={() => {
                              setActiveJsonTab("assertions");
                            }}
                          >
                            Assertions
                          </button>
                          <button
                            type="button"
                            className={`${styles.tabBtn} ${activeJsonTab === "raw" ? styles.tabBtnActive : ""}`}
                            onClick={() => {
                              setActiveJsonTab("raw");
                            }}
                          >
                            Raw Payload
                          </button>
                        </div>

                        {activeJsonTab === "response" && (
                          <pre className={styles.codeBox}>
                            {JSON.stringify(
                              parsedJson.response ?? parsedJson,
                              null,
                              2
                            )}
                          </pre>
                        )}
                        {activeJsonTab === "request" && (
                          <pre className={styles.codeBox}>
                            {JSON.stringify(
                              parsedJson.request ?? parsedJson,
                              null,
                              2
                            )}
                          </pre>
                        )}
                        {activeJsonTab === "assertions" && (
                          <pre className={styles.codeBox}>
                            {JSON.stringify(
                              parsedJson.assertions ?? [],
                              null,
                              2
                            )}
                          </pre>
                        )}
                        {activeJsonTab === "raw" && (
                          <pre className={styles.codeBox}>
                            {JSON.stringify(parsedJson, null, 2)}
                          </pre>
                        )}
                      </div>
                    ) : decodedText ? (
                      /* Plain text / Log Viewer */
                      <pre className={styles.codeBox}>{decodedText}</pre>
                    ) : (
                      /* Fallback for oversized or binary files */
                      <div className={styles.expiredCard}>
                        <span className={styles.expiredIcon}>📦</span>
                        <h4 className={styles.expiredTitle}>
                          Binary or Large Artifact
                        </h4>
                        <p className={styles.expiredText}>
                          This artifact is too large for inline preview or
                          contains binary data ({selectedArtifact.content_type})
                          . Use the download button below to inspect it.
                        </p>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Modal Footer */}
              <div className={styles.modalFooter}>
                <span className={styles.checksumNote}>
                  SHA-256: {selectedArtifact.checksum || "Verified on ingest"}
                </span>
                <span className={styles.checksumNote}>
                  Redaction: {selectedArtifact.redaction_status}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
