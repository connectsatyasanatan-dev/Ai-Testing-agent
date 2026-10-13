import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import {
  useCreatePlan,
  usePlan,
  useUpdatePlan,
  useValidatePlan,
} from "@/hooks/usePlans";
import { useProjects } from "@/hooks/useProjects";
import type {
  Assertion,
  AssertionType,
  IdempotencyClass,
  PlanValidationResponse,
  RiskLevel,
  TestStep,
} from "@/types/plan";
import styles from "./PlanEditor.module.css";

const HTTP_ACTIONS = [
  "get",
  "post",
  "put",
  "delete",
  "patch",
  "head",
  "options",
  "http_request",
];

const WEB_ACTIONS = [
  "navigate",
  "click",
  "fill",
  "select",
  "wait_for",
  "screenshot",
];

const HTTP_ASSERTION_TYPES: AssertionType[] = [
  "status_code",
  "json_path_equals",
  "json_path_exists",
  "json_path_matches",
  "header_equals",
  "header_exists",
  "response_time_under",
];

const WEB_ASSERTION_TYPES: AssertionType[] = [
  "url_contains",
  "url_equals",
  "title_contains",
  "title_equals",
  "text_visible",
  "element_visible",
  "element_hidden",
];

function createDefaultStep(adapterType: string, index: number): TestStep {
  const isWeb = adapterType === "web";
  return {
    step_id: `step_${index + 1}`,
    step_order: index + 1,
    adapter_type: adapterType,
    action: isWeb ? "navigate" : "get",
    inputs: isWeb
      ? { url: "https://example.com" }
      : { path: "/api/v1/health", method: "GET" },
    assertions: [
      isWeb
        ? { type: "url_contains", value: "example.com" }
        : { type: "status_code", value: 200 },
    ],
    timeout_seconds: 30,
    retry_policy: { max_attempts: 1, backoff_seconds: 1 },
    idempotency_class: "safe",
    risk_level: "low",
    description: "",
  };
}

export default function PlanEditor() {
  const { projectId: urlProjectId, planId } = useParams<{
    projectId?: string;
    planId?: string;
  }>();
  const navigate = useNavigate();

  const { data: projectList } = useProjects();
  const currentProjectId = urlProjectId || projectList?.items[0]?.id;

  const isEditing = Boolean(planId);
  const { data: existingPlan, isLoading: isLoadingPlan } = usePlan(planId);

  // Form State
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [adapterType, setAdapterType] = useState<string>("http_api");
  const [steps, setSteps] = useState<TestStep[]>([createDefaultStep("http_api", 0)]);
  const [deleteStepIndex, setDeleteStepIndex] = useState<number | null>(null);

  // Validation State
  const [validationResult, setValidationResult] =
    useState<PlanValidationResponse | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  // Mutations
  const createPlanMutation = useCreatePlan(currentProjectId);
  const updatePlanMutation = useUpdatePlan(planId);
  const validatePlanMutation = useValidatePlan();

  useEffect(() => {
    if (existingPlan) {
      setName(existingPlan.name);
      setDescription(existingPlan.description || "");
      setAdapterType(existingPlan.adapter_type);
      if (existingPlan.steps && existingPlan.steps.length > 0) {
        setSteps(
          existingPlan.steps.map((s, idx) => ({
            ...s,
            inputs: s.inputs || {},
            assertions: s.assertions || [],
            retry_policy: s.retry_policy || { max_attempts: 1 },
            step_order: idx + 1,
          }))
        );
      }
    }
  }, [existingPlan]);

  const handleAdapterChange = (newAdapter: string) => {
    setAdapterType(newAdapter);
    setSteps([createDefaultStep(newAdapter, 0)]);
    setValidationResult(null);
  };

  const handleAddStep = () => {
    setSteps((prev) => [...prev, createDefaultStep(adapterType, prev.length)]);
    setValidationResult(null);
  };

  const handleDuplicateStep = (index: number) => {
    const target = steps[index];
    const newStep: TestStep = {
      ...(JSON.parse(JSON.stringify(target)) as TestStep),
      step_id: `${target.step_id}_copy_${Date.now().toString().slice(-4)}`,
      step_order: steps.length + 1,
    };
    setSteps((prev) => [...prev, newStep]);
    setValidationResult(null);
  };

  const handleMoveStep = (index: number, direction: "up" | "down") => {
    const targetIndex = direction === "up" ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= steps.length) return;

    setSteps((prev) => {
      const next = [...prev];
      const temp = next[index];
      next[index] = next[targetIndex];
      next[targetIndex] = temp;
      return next.map((s, i) => ({ ...s, step_order: i + 1 }));
    });
    setValidationResult(null);
  };

  const confirmDeleteStep = () => {
    if (deleteStepIndex === null) return;
    setSteps((prev) =>
      prev.filter((_, i) => i !== deleteStepIndex).map((s, i) => ({ ...s, step_order: i + 1 }))
    );
    setDeleteStepIndex(null);
    setValidationResult(null);
  };

  const updateStepField = (index: number, field: keyof TestStep, value: unknown) => {
    setSteps((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
    setValidationResult(null);
  };

  const updateStepInput = (index: number, key: string, value: unknown) => {
    setSteps((prev) => {
      const next = [...prev];
      next[index] = {
        ...next[index],
        inputs: { ...next[index].inputs, [key]: value },
      };
      return next;
    });
    setValidationResult(null);
  };

  const addAssertion = (stepIndex: number) => {
    const isWeb = adapterType === "web";
    const defaultType = isWeb ? "url_contains" : "status_code";
    const defaultValue = isWeb ? "example" : 200;

    setSteps((prev) => {
      const next = [...prev];
      const currentAssertions = next[stepIndex].assertions || [];
      next[stepIndex] = {
        ...next[stepIndex],
        assertions: [...currentAssertions, { type: defaultType, value: defaultValue }],
      };
      return next;
    });
  };

  const updateAssertion = (
    stepIndex: number,
    assertionIndex: number,
    field: keyof Assertion,
    value: unknown
  ) => {
    setSteps((prev) => {
      const next = [...prev];
      const currentAssertions = [...next[stepIndex].assertions];
      currentAssertions[assertionIndex] = {
        ...currentAssertions[assertionIndex],
        [field]: value,
      };
      next[stepIndex] = { ...next[stepIndex], assertions: currentAssertions };
      return next;
    });
  };

  const removeAssertion = (stepIndex: number, assertionIndex: number) => {
    setSteps((prev) => {
      const next = [...prev];
      next[stepIndex] = {
        ...next[stepIndex],
        assertions: next[stepIndex].assertions.filter((_, i) => i !== assertionIndex),
      };
      return next;
    });
  };

  const handleValidate = async () => {
    setSaveError(null);
    try {
      const result = await validatePlanMutation.mutateAsync({
        planId,
        projectId: currentProjectId,
        data: { adapter_type: adapterType, steps },
      });
      setValidationResult(result);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Validation request failed.");
    }
  };

  const handleSave = async () => {
    setSaveError(null);
    if (!name.trim()) {
      setSaveError("Plan name is required.");
      return;
    }
    if (steps.length === 0) {
      setSaveError("At least one test step is required.");
      return;
    }

    try {
      if (isEditing && planId) {
        const saved = await updatePlanMutation.mutateAsync({
          name: name.trim(),
          description: description.trim() || undefined,
          steps,
        });
        const targetPath = currentProjectId
          ? `/projects/${currentProjectId}/plans/${saved.plan_id}`
          : `/plans/${saved.plan_id}`;
        navigate(targetPath);
      } else {
        const created = await createPlanMutation.mutateAsync({
          name: name.trim(),
          adapter_type: adapterType,
          description: description.trim() || undefined,
          steps,
        });
        const targetPath = currentProjectId
          ? `/projects/${currentProjectId}/plans/${created.plan_id}`
          : `/plans/${created.plan_id}`;
        navigate(targetPath);
      }
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save plan.");
    }
  };

  const isApproved = existingPlan?.status === "approved";
  const isSaving = createPlanMutation.isPending || updatePlanMutation.isPending;

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
          <div className={styles.titleArea}>
            <h1>{isEditing ? `Edit Plan: ${existingPlan?.name || name}` : "Create New Test Plan"}</h1>
            <p className={styles.subtitle}>
              Configure typed adapter actions, inputs, deterministic assertions, and execution limits.
            </p>
          </div>
          <div className={styles.headerActions}>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnSecondary}`}
              onClick={() => void handleValidate()}
              disabled={validatePlanMutation.isPending}
              id="validate-plan-btn"
            >
              <span>🔍</span>
              <span>{validatePlanMutation.isPending ? "Validating..." : "Validate Plan"}</span>
            </button>
            <button
              type="button"
              className={`${styles.btn} ${styles.btnPrimary}`}
              onClick={() => void handleSave()}
              disabled={isSaving}
              id="save-plan-btn"
            >
              <span>💾</span>
              <span>{isSaving ? "Saving..." : isApproved ? "Save as New Version" : "Save Plan"}</span>
            </button>
          </div>
        </div>

        {isApproved && (
          <div className={`${styles.banner} ${styles.bannerWarning}`} role="alert">
            <span>⚠️</span>
            <div>
              <strong>Approved Plan Immutability:</strong> Version {existingPlan.version} is approved and
              immutable. Saving your changes will automatically create a new draft version without
              modifying prior runs or the approved version.
            </div>
          </div>
        )}

        {saveError && (
          <ErrorMessage title="Plan Validation / Save Error" message={saveError}>
            <Button variant="secondary" size="sm" onClick={() => setSaveError(null)}>
              Dismiss
            </Button>
          </ErrorMessage>
        )}

        {validationResult && (
          <div className={styles.validationCard}>
            <div className={styles.validationTitle}>
              {validationResult.valid ? (
                <>
                  <span style={{ color: "#16a34a" }}>✅</span>
                  <span>Plan schema validation passed with 0 errors!</span>
                </>
              ) : (
                <>
                  <span style={{ color: "#dc2626" }}>❌</span>
                  <span>
                    Validation found {validationResult.errors.length} error(s) and{" "}
                    {validationResult.warnings.length} warning(s):
                  </span>
                </>
              )}
            </div>
            {validationResult.issues.length > 0 && (
              <ul className={styles.issueList}>
                {validationResult.issues.map((iss, idx) => (
                  <li
                    key={idx}
                    className={`${styles.issueItem} ${
                      iss.severity === "error" ? styles.issueError : styles.issueWarning
                    }`}
                  >
                    <span>{iss.severity === "error" ? "⛔" : "⚠️"}</span>
                    <span>{iss.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {isLoadingPlan ? (
          <Spinner label="Loading plan details..." />
        ) : (
          <>
            {/* Plan Metadata Form */}
            <div className={styles.formCard}>
              <div className={styles.formGrid}>
                <div className={styles.formGroup}>
                  <label htmlFor="plan-name-input" className={styles.label}>
                    Plan Name *
                  </label>
                  <input
                    id="plan-name-input"
                    type="text"
                    className={styles.input}
                    placeholder="e.g. User Authentication & Profile Smoke Test"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>

                <div className={styles.formGroup}>
                  <label htmlFor="plan-adapter-select" className={styles.label}>
                    Adapter Target
                  </label>
                  <select
                    id="plan-adapter-select"
                    className={styles.select}
                    value={adapterType}
                    disabled={isEditing}
                    onChange={(e) => handleAdapterChange(e.target.value)}
                  >
                    <option value="http_api">⚡ HTTP API Adapter</option>
                    <option value="web">🌐 Web Browser (Playwright)</option>
                  </select>
                </div>
              </div>

              <div className={styles.formGroup}>
                <label htmlFor="plan-desc-input" className={styles.label}>
                  Description (Optional)
                </label>
                <textarea
                  id="plan-desc-input"
                  className={styles.textarea}
                  placeholder="Describe the test plan scope and objectives..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>
            </div>

            {/* Test Steps Builder */}
            <div className={styles.stepsSection}>
              <div className={styles.stepsHeader}>
                <h2>Test Steps ({steps.length})</h2>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={handleAddStep}
                  id="add-step-btn"
                >
                  <span>➕ Add Step</span>
                </button>
              </div>

              {steps.map((step, sIdx) => {
                const isWeb = adapterType === "web";
                const supportedActions = isWeb ? WEB_ACTIONS : HTTP_ACTIONS;
                const supportedAssertions = isWeb
                  ? WEB_ASSERTION_TYPES
                  : HTTP_ASSERTION_TYPES;

                return (
                  <div key={sIdx} className={styles.stepCard} id={`step-editor-${sIdx}`}>
                    <div className={styles.stepCardHeader}>
                      <div className={styles.stepHeaderLeft}>
                        <span className={styles.stepNumberBadge}>{sIdx + 1}</span>
                        <input
                          id={`step-${sIdx}-id-input`}
                          type="text"
                          className={styles.input}
                          style={{ fontWeight: "bold", width: 180 }}
                          value={step.step_id}
                          onChange={(e) => updateStepField(sIdx, "step_id", e.target.value)}
                          placeholder="step_identifier"
                        />
                        <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-muted)" }}>
                          ({step.adapter_type})
                        </span>
                      </div>

                      <div className={styles.stepActions}>
                        <button
                          type="button"
                          className={styles.iconBtn}
                          disabled={sIdx === 0}
                          onClick={() => handleMoveStep(sIdx, "up")}
                          title="Move up"
                          aria-label={`Move step ${sIdx + 1} up`}
                        >
                          ⬆️
                        </button>
                        <button
                          type="button"
                          className={styles.iconBtn}
                          disabled={sIdx === steps.length - 1}
                          onClick={() => handleMoveStep(sIdx, "down")}
                          title="Move down"
                          aria-label={`Move step ${sIdx + 1} down`}
                        >
                          ⬇️
                        </button>
                        <button
                          type="button"
                          className={styles.iconBtn}
                          onClick={() => handleDuplicateStep(sIdx)}
                          title="Duplicate step"
                          aria-label={`Duplicate step ${sIdx + 1}`}
                        >
                          📋
                        </button>
                        <button
                          type="button"
                          className={`${styles.iconBtn} ${styles.btnDanger}`}
                          disabled={steps.length <= 1}
                          onClick={() => setDeleteStepIndex(sIdx)}
                          title="Delete step"
                          aria-label={`Delete step ${sIdx + 1}`}
                        >
                          🗑️
                        </button>
                      </div>
                    </div>

                    {/* Action & Inputs */}
                    <div className={styles.formGrid}>
                      <div className={styles.formGroup}>
                        <label htmlFor={`step-${sIdx}-action`} className={styles.label}>
                          Action
                        </label>
                        <select
                          id={`step-${sIdx}-action`}
                          className={styles.select}
                          value={step.action}
                          onChange={(e) => {
                            const newAct = e.target.value;
                            updateStepField(sIdx, "action", newAct);
                            if (adapterType === "http_api") {
                              updateStepInput(sIdx, "method", newAct.toUpperCase());
                            }
                          }}
                        >
                          {supportedActions.map((act) => (
                            <option key={act} value={act}>
                              {act.toUpperCase()}
                            </option>
                          ))}
                        </select>
                      </div>

                      {/* Adapter Specific Input Fields */}
                      {adapterType === "http_api" ? (
                        <div className={styles.formGroup}>
                          <label htmlFor={`step-${sIdx}-path`} className={styles.label}>
                            Path / Endpoint *
                          </label>
                          <input
                            id={`step-${sIdx}-path`}
                            type="text"
                            className={styles.input}
                            placeholder="/api/v1/..."
                            value={String(step.inputs?.path || "")}
                            onChange={(e) => updateStepInput(sIdx, "path", e.target.value)}
                          />
                        </div>
                      ) : (
                        <>
                          {step.action === "navigate" ? (
                            <div className={styles.formGroup}>
                              <label htmlFor={`step-${sIdx}-url`} className={styles.label}>
                                Navigation URL *
                              </label>
                              <input
                                id={`step-${sIdx}-url`}
                                type="text"
                                className={styles.input}
                                placeholder="https://example.com"
                                value={String(step.inputs?.url || "")}
                                onChange={(e) => updateStepInput(sIdx, "url", e.target.value)}
                              />
                            </div>
                          ) : (
                            <>
                              <div className={styles.formGroup}>
                                <label htmlFor={`step-${sIdx}-locator-type`} className={styles.label}>
                                  Locator Strategy
                                </label>
                                <select
                                  id={`step-${sIdx}-locator-type`}
                                  className={styles.select}
                                  value={String(step.inputs?.locator_type || "css")}
                                  onChange={(e) =>
                                    updateStepInput(sIdx, "locator_type", e.target.value)
                                  }
                                >
                                  <option value="css">CSS Selector</option>
                                  <option value="text">Text Content</option>
                                  <option value="test_id">Test ID (data-testid)</option>
                                  <option value="role">ARIA Role</option>
                                  <option value="label">Label</option>
                                </select>
                              </div>
                              <div className={styles.formGroup}>
                                <label htmlFor={`step-${sIdx}-locator-value`} className={styles.label}>
                                  Locator Target *
                                </label>
                                <input
                                  id={`step-${sIdx}-locator-value`}
                                  type="text"
                                  className={styles.input}
                                  placeholder="#submit-button, 'Log In', etc."
                                  value={String(step.inputs?.locator_value || "")}
                                  onChange={(e) =>
                                    updateStepInput(sIdx, "locator_value", e.target.value)
                                  }
                                />
                              </div>
                              {step.action === "fill" && (
                                <div className={styles.formGroup}>
                                  <label htmlFor={`step-${sIdx}-fill-text`} className={styles.label}>
                                    Text to Fill *
                                  </label>
                                  <input
                                    id={`step-${sIdx}-fill-text`}
                                    type="text"
                                    className={styles.input}
                                    placeholder="Enter input text..."
                                    value={String(step.inputs?.text || step.inputs?.value || "")}
                                    onChange={(e) => updateStepInput(sIdx, "text", e.target.value)}
                                  />
                                </div>
                              )}
                            </>
                          )}
                        </>
                      )}
                    </div>

                    {/* Step Safety & Limits */}
                    <div className={styles.formGrid}>
                      <div className={styles.formGroup}>
                        <label htmlFor={`step-${sIdx}-timeout`} className={styles.label}>
                          Timeout (seconds)
                        </label>
                        <input
                          id={`step-${sIdx}-timeout`}
                          type="number"
                          min="1"
                          max="300"
                          className={styles.input}
                          value={step.timeout_seconds}
                          onChange={(e) =>
                            updateStepField(sIdx, "timeout_seconds", parseFloat(e.target.value) || 30)
                          }
                        />
                      </div>

                      <div className={styles.formGroup}>
                        <label htmlFor={`step-${sIdx}-idempotency`} className={styles.label}>
                          Idempotency Class
                        </label>
                        <select
                          id={`step-${sIdx}-idempotency`}
                          className={styles.select}
                          value={step.idempotency_class}
                          onChange={(e) =>
                            updateStepField(sIdx, "idempotency_class", e.target.value as IdempotencyClass)
                          }
                        >
                          <option value="safe">Safe (Read-only)</option>
                          <option value="idempotent">Idempotent (Repeatable mutation)</option>
                          <option value="non_idempotent">Non-Idempotent (Side-effecting)</option>
                          <option value="unknown">Unknown</option>
                        </select>
                      </div>

                      <div className={styles.formGroup}>
                        <label htmlFor={`step-${sIdx}-risk`} className={styles.label}>
                          Risk Level
                        </label>
                        <select
                          id={`step-${sIdx}-risk`}
                          className={styles.select}
                          value={step.risk_level}
                          onChange={(e) =>
                            updateStepField(sIdx, "risk_level", e.target.value as RiskLevel)
                          }
                        >
                          <option value="low">Low</option>
                          <option value="medium">Medium</option>
                          <option value="high">High</option>
                          <option value="critical">Critical</option>
                        </select>
                      </div>
                    </div>

                    {/* Assertions Builder */}
                    <div className={styles.assertionsArea}>
                      <div className={styles.assertionsHeader}>
                        <span className={styles.label}>Deterministic Assertions</span>
                        <button
                          type="button"
                          className={styles.actionBtn}
                          onClick={() => addAssertion(sIdx)}
                          id={`step-${sIdx}-add-assertion-btn`}
                        >
                          ➕ Add Assertion
                        </button>
                      </div>

                      {step.assertions.length === 0 ? (
                        <p style={{ fontSize: "var(--font-size-xs)", color: "var(--color-foreground-muted)" }}>
                          No assertions defined for this step.
                        </p>
                      ) : (
                        step.assertions.map((assertion, aIdx) => (
                          <div key={aIdx} className={styles.assertionRow}>
                            <select
                              className={styles.select}
                              value={assertion.type}
                              onChange={(e) =>
                                updateAssertion(sIdx, aIdx, "type", e.target.value as AssertionType)
                              }
                            >
                              {supportedAssertions.map((aType) => (
                                <option key={aType} value={aType}>
                                  {aType}
                                </option>
                              ))}
                            </select>

                            <input
                              type="text"
                              className={styles.input}
                              placeholder="Expected Value (e.g. 200, 'Success')"
                              value={String(assertion.value ?? "")}
                              onChange={(e) => {
                                const val = e.target.value;
                                const parsed = assertion.type === "status_code" ? parseInt(val, 10) || val : val;
                                updateAssertion(sIdx, aIdx, "value", parsed);
                              }}
                            />

                            {assertion.type.startsWith("json_path") ? (
                              <input
                                type="text"
                                className={styles.input}
                                placeholder="JSON Path (e.g. $.status)"
                                value={assertion.path || ""}
                                onChange={(e) => updateAssertion(sIdx, aIdx, "path", e.target.value)}
                              />
                            ) : (
                              <input
                                type="text"
                                className={styles.input}
                                placeholder="Optional assertion message"
                                value={assertion.message || ""}
                                onChange={(e) => updateAssertion(sIdx, aIdx, "message", e.target.value)}
                              />
                            )}

                            <button
                              type="button"
                              className={`${styles.iconBtn} ${styles.btnDanger}`}
                              onClick={() => removeAssertion(sIdx, aIdx)}
                              title="Remove assertion"
                            >
                              ✕
                            </button>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}

        {/* Confirmation Modal for Step Deletion */}
        {deleteStepIndex !== null && (
          <div className={styles.modalOverlay} role="dialog" aria-modal="true">
            <div className={styles.modal}>
              <h3 className={styles.modalTitle}>Confirm Step Deletion</h3>
              <p style={{ fontSize: "var(--font-size-sm)", color: "var(--color-foreground-muted)" }}>
                Are you sure you want to remove step #{deleteStepIndex + 1} (
                {steps[deleteStepIndex]?.step_id})? This action cannot be undone.
              </p>
              <div className={styles.modalActions}>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnSecondary}`}
                  onClick={() => setDeleteStepIndex(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={`${styles.btn} ${styles.btnDanger}`}
                  onClick={confirmDeleteStep}
                  id="confirm-delete-step-btn"
                >
                  Delete Step
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
