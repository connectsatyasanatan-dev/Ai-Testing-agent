import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { useEnvironments } from "@/hooks/useEnvironments";
import { ApiError, generatePlan } from "@/lib/api-client";
import type { GeneratePlanResponse } from "@/types/plan";
import styles from "./AIGeneratePlanModal.module.css";

interface AIGeneratePlanModalProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: (planId: string) => void;
}

export function AIGeneratePlanModal({
  projectId,
  isOpen,
  onClose,
  onSuccess,
}: AIGeneratePlanModalProps) {
  const navigate = useNavigate();
  const { data: envsData } = useEnvironments(projectId);
  const environments = envsData?.items ?? [];

  const [intent, setIntent] = useState("");
  const [adapterType, setAdapterType] = useState<"http_api" | "web">("http_api");
  const [environmentId, setEnvironmentId] = useState<string>("");
  const [specContent, setSpecContent] = useState("");

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isAiUnavailable, setIsAiUnavailable] = useState(false);
  const [result, setResult] = useState<GeneratePlanResponse | null>(null);

  if (!isOpen) return null;

  const handleReset = () => {
    setResult(null);
    setError(null);
    setIsAiUnavailable(false);
  };

  const handleClose = () => {
    handleReset();
    onClose();
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!intent.trim()) {
      setError("Please describe the testing objective or user story.");
      return;
    }

    setIsLoading(true);
    setError(null);
    setIsAiUnavailable(false);
    setResult(null);

    try {
      const resp = await generatePlan(projectId, {
        intent: intent.trim(),
        adapter_type: adapterType,
        environment_id: environmentId || undefined,
        spec_content: specContent.trim() || undefined,
      });
      setResult(resp);
      if (onSuccess) {
        onSuccess(resp.plan_id);
      }
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.isAiUnavailable || err.status === 503) {
          setIsAiUnavailable(true);
        } else {
          setError(err.message || "Failed to generate test plan.");
        }
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unexpected error occurred during test plan generation.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleNavigateToEditor = () => {
    if (!result) return;
    navigate(`/projects/${projectId}/plans/${result.plan_id}/edit`);
    handleClose();
  };

  const handleNavigateToDetail = () => {
    if (!result) return;
    navigate(`/projects/${projectId}/plans/${result.plan_id}`);
    handleClose();
  };

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby="ai-modal-title">
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.headerTitleGroup}>
            <h2 id="ai-modal-title">✨ AI-Assisted Test Plan Generator</h2>
            <p className={styles.headerSubtitle}>
              Generate structured, declarative test drafts from user intent or API specifications.
            </p>
          </div>
          <button
            type="button"
            className={styles.closeButton}
            onClick={handleClose}
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        <div className={styles.body}>
          {/* Safety Notice */}
          <div className={styles.safetyNotice}>
            <span>🛡️</span>
            <div>
              <strong>Human Approval Gate Enforced:</strong> AI-generated plans are saved strictly as
              drafts and can never execute directly. All actions are validated against strict allowlists
              and require formal review and approval by a maintainer before running.
            </div>
          </div>

          {/* AI Unavailable Banner */}
          {isAiUnavailable && (
            <div className={styles.unconfiguredBanner} role="alert">
              <h3>⚠️ AI Provider Not Configured</h3>
              <p>
                AI-assisted plan generation is not currently configured for this instance.
              </p>
              <p>
                To enable this feature, configure <code className={styles.codeSnippet}>UITP_AI_PROVIDER</code>{" "}
                (e.g., <code className={styles.codeSnippet}>openai</code> or{" "}
                <code className={styles.codeSnippet}>mock</code>) and{" "}
                <code className={styles.codeSnippet}>UITP_AI_API_KEY</code> in your server environment.
              </p>
              <p>
                Manual test plan authoring remains fully operational. You can use the visual Plan Editor
                to build and validate test plans without an AI provider.
              </p>
            </div>
          )}

          {/* Error Message */}
          {error && !isAiUnavailable && (
            <div className={styles.blockedAlert} role="alert">
              <div className={styles.blockedAlertTitle}>
                <span>❌</span> Generation Failed
              </div>
              <p style={{ margin: 0 }}>{error}</p>
            </div>
          )}

          {/* Loading State */}
          {isLoading && (
            <div className={styles.loadingContainer}>
              <Spinner size="lg" />
              <div className={styles.loadingTitle}>Generating Test Plan Draft...</div>
              <div className={styles.loadingSub}>
                Synthesizing declarative steps, verifying security allowlists, and validating assertions.
              </div>
            </div>
          )}

          {/* Generated Result Preview */}
          {!isLoading && result && (
            <div className={styles.resultContainer}>
              <div className={styles.resultHeaderCard}>
                <div className={styles.resultHeaderTop}>
                  <h3 className={styles.resultPlanName}>{result.name}</h3>
                  <span className={styles.badgeDraft}>✏️ Draft (Unapproved)</span>
                </div>
                <div style={{ fontSize: "0.85rem", color: "var(--color-text-muted)" }}>
                  Target Adapter: <strong>{result.adapter_type}</strong> • Steps: <strong>{result.steps.length}</strong>
                </div>
                <div className={styles.approvalNotice}>
                  🔒 <strong>Approval Required:</strong> This plan has been saved as a draft with immutable versioning.
                  It cannot run until explicitly approved.
                </div>
              </div>

              {/* Blocked Steps Warning */}
              {result.blocked_steps.length > 0 && (
                <div className={styles.blockedAlert}>
                  <div className={styles.blockedAlertTitle}>
                    <span>⚠️</span> Blocked Steps ({result.blocked_steps.length})
                  </div>
                  <p style={{ margin: "0 0 0.5rem", fontSize: "0.85rem" }}>
                    The following steps suggested by the model were rejected by deterministic policy validation and excluded:
                  </p>
                  <ul className={styles.blockedList}>
                    {result.blocked_steps.map((b, i) => (
                      <li key={i} className={styles.blockedItem}>
                        <strong>{b.step_id}</strong> (<code>{b.action}</code>): {b.reason}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Steps Preview */}
              <div className={styles.stepsPreviewSection}>
                <h4>Generated Steps ({result.steps.length})</h4>
                {result.steps.map((s, idx) => (
                  <div key={idx} className={styles.stepPreviewItem}>
                    <div className={styles.stepPreviewHeader}>
                      <span className={styles.stepPreviewId}>{s.step_id}</span>
                      <span className={styles.stepPreviewAction}>{s.action}</span>
                    </div>
                    {s.description && (
                      <p className={styles.stepPreviewDesc}>{s.description}</p>
                    )}
                    <div style={{ fontSize: "0.75rem", color: "var(--color-text-muted)", marginTop: "0.25rem" }}>
                      Idempotency: <strong>{s.idempotency_class}</strong> • Timeout: <strong>{s.timeout_seconds}s</strong> • Assertions: <strong>{s.assertions?.length || 0}</strong>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Form Input View */}
          {!isLoading && !result && (
            <form id="ai-generate-form" onSubmit={(e) => { void handleGenerate(e); }}>
              <div className={styles.rowInputs}>
                <div className={styles.formGroup}>
                  <label htmlFor="ai-adapter-select">
                    Target Adapter <span className={styles.requiredAsterisk}>*</span>
                  </label>
                  <select
                    id="ai-adapter-select"
                    className={styles.select}
                    value={adapterType}
                    onChange={(e) => setAdapterType(e.target.value as "http_api" | "web")}
                    disabled={isLoading}
                  >
                    <option value="http_api">HTTP API (API requests & schema validation)</option>
                    <option value="web">Web Browser (Playwright browser actions)</option>
                  </select>
                </div>

                <div className={styles.formGroup}>
                  <label htmlFor="ai-environment-select">Target Environment</label>
                  <select
                    id="ai-environment-select"
                    className={styles.select}
                    value={environmentId}
                    onChange={(e) => setEnvironmentId(e.target.value)}
                    disabled={isLoading}
                  >
                    <option value="">No specific environment</option>
                    {environments.map((env) => (
                      <option key={env.id} value={env.id}>
                        {env.name} ({env.base_url})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="ai-intent-textarea">
                  Testing Objective / Acceptance Criteria <span className={styles.requiredAsterisk}>*</span>
                </label>
                <textarea
                  id="ai-intent-textarea"
                  className={styles.textarea}
                  rows={4}
                  placeholder="e.g. Verify the user login endpoint. Send a POST request to /api/v1/auth/login with valid credentials, expect status code 200, and assert that the response JSON contains a session token."
                  value={intent}
                  onChange={(e) => setIntent(e.target.value)}
                  maxLength={4000}
                  disabled={isLoading}
                  required
                />
                <div className={styles.helpText}>
                  Describe what functionality to test, expected response codes, or user workflows (up to 4,000 characters).
                </div>
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="ai-spec-textarea">
                  API Specification / Swagger / Schema (Optional)
                </label>
                <textarea
                  id="ai-spec-textarea"
                  className={styles.textarea}
                  rows={3}
                  placeholder="Paste OpenAPI paths, JSON schemas, curl commands, or response shapes here..."
                  value={specContent}
                  onChange={(e) => setSpecContent(e.target.value)}
                  maxLength={16384}
                  disabled={isLoading}
                />
                <div className={styles.helpText}>
                  Optional reference material to guide step parameterization (up to 16,384 characters).
                </div>
              </div>
            </form>
          )}
        </div>

        <div className={styles.footer}>
          {!result ? (
            <>
              <Button variant="ghost" onClick={handleClose} disabled={isLoading}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                form="ai-generate-form"
                disabled={isLoading || !intent.trim()}
                id="submit-ai-generate-button"
              >
                {isLoading ? "Generating..." : "✨ Generate Draft Plan"}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={handleReset}>
                Generate Another
              </Button>
              <Button variant="secondary" onClick={handleNavigateToEditor} id="open-editor-button">
                ✏️ Inspect & Edit in Plan Editor
              </Button>
              <Button variant="primary" onClick={handleNavigateToDetail} id="view-plan-detail-button">
                📋 View Plan & Review
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
