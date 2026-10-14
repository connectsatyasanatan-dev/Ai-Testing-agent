import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/Button";
import { useEnvironments } from "@/hooks/useEnvironments";
import { usePlanMission } from "@/hooks/useMissions";
import { useProjects } from "@/hooks/useProjects";
import styles from "./MissionPlanner.module.css";

const MAX_REQUIREMENTS_BYTES = 65536; // 64KB
const MAX_OPENAPI_BYTES = 262144;     // 256KB
const MAX_SCENARIOS_BYTES = 65536;    // 64KB

export default function MissionPlanner() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const navigate = useNavigate();

  const { data: projectList } = useProjects();
  const currentProjectId = urlProjectId || projectList?.items[0]?.id || "";

  const { data: envList, isLoading: isLoadingEnvs } = useEnvironments(currentProjectId);
  const planMissionMutation = usePlanMission(currentProjectId);

  const [environmentId, setEnvironmentId] = useState("");
  const [requirementsContent, setRequirementsContent] = useState("");
  const [openapiSpec, setOpenapiSpec] = useState("");
  const [scenariosContent, setScenariosContent] = useState("");
  const [targetUrls, setTargetUrls] = useState<string[]>([""]);
  const [maxParallelTasks, setMaxParallelTasks] = useState(4);

  const [validationError, setValidationError] = useState<string | null>(null);

  // File upload handlers with client-side bounds checking
  const handleFileUpload = (
    file: File,
    maxBytes: number,
    allowedExtensions: string[],
    setter: (content: string) => void
  ) => {
    setValidationError(null);
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!ext || !allowedExtensions.includes(ext)) {
      setValidationError(`Invalid file type for ${file.name}. Allowed: .${allowedExtensions.join(", .")}`);
      return;
    }
    if (file.size > maxBytes) {
      setValidationError(`File ${file.name} exceeds maximum size limit of ${Math.round(maxBytes / 1024)}KB.`);
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result;
      if (typeof text === "string") {
        setter(text);
      }
    };
    reader.onerror = () => {
      setValidationError(`Failed to read file ${file.name}.`);
    };
    reader.readAsText(file);
  };

  const handleAddUrl = () => {
    setTargetUrls([...targetUrls, ""]);
  };

  const handleRemoveUrl = (index: number) => {
    setTargetUrls(targetUrls.filter((_, i) => i !== index));
  };

  const handleUrlChange = (index: number, val: string) => {
    const updated = [...targetUrls];
    updated[index] = val;
    setTargetUrls(updated);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setValidationError(null);

    // 1. Client-side field validations
    if (!currentProjectId) {
      setValidationError("Please select or create a project first.");
      return;
    }

    const selectedEnv = environmentId || envList?.items[0]?.id;
    if (!selectedEnv) {
      setValidationError("A target environment is required. Please create an environment if none exist.");
      return;
    }

    const trimmedReq = requirementsContent.trim();
    if (!trimmedReq) {
      setValidationError("Requirements specification content cannot be empty.");
      return;
    }

    if (new TextEncoder().encode(trimmedReq).length > MAX_REQUIREMENTS_BYTES) {
      setValidationError(`Requirements content exceeds limit of ${MAX_REQUIREMENTS_BYTES / 1024}KB.`);
      return;
    }

    const validTargetUrls = targetUrls.map((u) => u.trim()).filter(Boolean);
    for (const url of validTargetUrls) {
      try {
        new URL(url);
      } catch {
        setValidationError(`Invalid target URL format: "${url}". Must include http:// or https://.`);
        return;
      }
    }

    // 2. Submit mission planning request
    try {
      const result = await planMissionMutation.mutateAsync({
        environment_id: selectedEnv,
        requirements_content: trimmedReq,
        openapi_spec: openapiSpec.trim() || undefined,
        scenarios_content: scenariosContent.trim() || undefined,
        target_urls: validTargetUrls,
        max_parallel_tasks: maxParallelTasks,
      });

      // Navigate to Mission DAG review
      const targetPath = currentProjectId
        ? `/projects/${currentProjectId}/missions/${result.mission_id}`
        : `/missions/${result.mission_id}`;
      navigate(targetPath);
    } catch (err) {
      setValidationError(
        err instanceof Error ? err.message : "Failed to decompose mission into DAG plan."
      );
    }
  };

  return (
    <AppShell
      sidebar={<Sidebar currentProjectId={currentProjectId} />}
      header={<Header currentProjectId={currentProjectId} />}
    >
      <div className={styles.container}>
        <div className={styles.header}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)" }}>
            <Link
              to={currentProjectId ? `/projects/${currentProjectId}/plans` : "/plans"}
              style={{ fontSize: "var(--font-size-sm)", color: "var(--color-primary)" }}
            >
              ← Back to Plans
            </Link>
          </div>
          <h1 className={styles.title}>Plan Autonomous Multi-Agent Mission</h1>
          <p className={styles.subtitle}>
            Upload specifications, OpenAPI contracts, and test scenarios. The Supervisor agent will decompose your
            inputs into an adapter-driven task DAG with prerequisite dependencies.
          </p>
        </div>

        {validationError && (
          <div className={styles.errorBanner} role="alert" aria-live="polite">
            <span>⚠️</span>
            <div>
              <strong>Validation Error:</strong> {validationError}
            </div>
          </div>
        )}

        <form className={styles.formCard} onSubmit={(e) => { void handleSubmit(e); }} noValidate>
          {/* Section 1: Target Environment */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>
              <span>🎯</span>
              <span>1. Target Environment</span>
            </h2>
            <p className={styles.sectionDesc}>
              Select the registered project environment where API endpoints and web locators will be evaluated.
            </p>

            <div className={styles.formGroup}>
              <label htmlFor="environment-select" className={styles.label}>
                Environment<span className={styles.labelRequired}>*</span>
              </label>
              <select
                id="environment-select"
                className={styles.select}
                value={environmentId || envList?.items[0]?.id || ""}
                onChange={(e) => setEnvironmentId(e.target.value)}
                disabled={isLoadingEnvs || !envList?.items?.length}
                required
              >
                {isLoadingEnvs ? (
                  <option value="">Loading environments...</option>
                ) : !envList?.items?.length ? (
                  <option value="">No environments found — create one first</option>
                ) : (
                  envList.items.map((env) => (
                    <option key={env.id} value={env.id}>
                      {env.name} ({env.base_url})
                    </option>
                  ))
                )}
              </select>
            </div>
          </div>

          {/* Section 2: Requirements Specification (Markdown) */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>
              <span>📋</span>
              <span>2. Requirements Specification (requirements.md)</span>
            </h2>
            <p className={styles.sectionDesc}>
              Markdown specification describing critical user journeys, business rules, and acceptance criteria.
            </p>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span>📄</span>
                <span>Upload requirements file (.md, .txt, max 64KB)</span>
              </div>
              <label className={styles.fileUploadBtn}>
                Browse File
                <input
                  type="file"
                  accept=".md,.txt"
                  style={{ display: "none" }}
                  aria-label="Upload requirements.md file"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFileUpload(f, MAX_REQUIREMENTS_BYTES, ["md", "txt"], setRequirementsContent);
                  }}
                />
              </label>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="requirements-content" className={styles.label}>
                Requirements Content<span className={styles.labelRequired}>*</span>
              </label>
              <textarea
                id="requirements-content"
                className={styles.textarea}
                rows={8}
                placeholder="# Feature Specification&#10;REQ-AUTH-01: User must authenticate via POST /api/v1/auth/login&#10;REQ-PROFILE-02: Authenticated user can view profile at /profile"
                value={requirementsContent}
                onChange={(e) => setRequirementsContent(e.target.value)}
                required
              />
              <span className={styles.characterCount}>
                {requirementsContent.length} chars / {MAX_REQUIREMENTS_BYTES} bytes limit
              </span>
            </div>
          </div>

          {/* Section 3: OpenAPI Specification (Optional) */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>
              <span>⚡</span>
              <span>3. API Specification (openapi.json / yaml — Optional)</span>
            </h2>
            <p className={styles.sectionDesc}>
              Enables deterministic endpoint analysis, request schema generation, and HTTP assertion mapping.
            </p>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span>🔌</span>
                <span>Upload OpenAPI spec (.json, .yaml, .yml, max 256KB)</span>
              </div>
              <label className={styles.fileUploadBtn}>
                Browse Spec
                <input
                  type="file"
                  accept=".json,.yaml,.yml"
                  style={{ display: "none" }}
                  aria-label="Upload openapi.json file"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFileUpload(f, MAX_OPENAPI_BYTES, ["json", "yaml", "yml"], setOpenapiSpec);
                  }}
                />
              </label>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="openapi-content" className={styles.label}>
                OpenAPI JSON / YAML Content
              </label>
              <textarea
                id="openapi-content"
                className={styles.textarea}
                rows={5}
                placeholder='{"openapi": "3.0.3", "info": {"title": "App API", "version": "1.0"}, "paths": {...}}'
                value={openapiSpec}
                onChange={(e) => setOpenapiSpec(e.target.value)}
              />
              <span className={styles.characterCount}>
                {openapiSpec.length} chars / {MAX_OPENAPI_BYTES} bytes limit
              </span>
            </div>
          </div>

          {/* Section 4: Scenarios (scenarios.json — Optional) */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>
              <span>🎭</span>
              <span>4. Test Scenarios (scenarios.json — Optional)</span>
            </h2>
            <p className={styles.sectionDesc}>
              Structured user scenarios or regression flow criteria to guide task generation.
            </p>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span>📝</span>
                <span>Upload scenarios JSON file (.json, max 64KB)</span>
              </div>
              <label className={styles.fileUploadBtn}>
                Browse Scenarios
                <input
                  type="file"
                  accept=".json"
                  style={{ display: "none" }}
                  aria-label="Upload scenarios.json file"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) handleFileUpload(f, MAX_SCENARIOS_BYTES, ["json"], setScenariosContent);
                  }}
                />
              </label>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="scenarios-content" className={styles.label}>
                Scenarios JSON Content
              </label>
              <textarea
                id="scenarios-content"
                className={styles.textarea}
                rows={4}
                placeholder='[{"id": "SCEN-01", "name": "Admin Onboarding", "steps": ["login", "invite"]}]'
                value={scenariosContent}
                onChange={(e) => setScenariosContent(e.target.value)}
              />
            </div>
          </div>

          {/* Section 5: Target Web URLs & Orchestration Options */}
          <div className={styles.section}>
            <h2 className={styles.sectionTitle}>
              <span>🌐</span>
              <span>5. Target Web URLs & Parallelism</span>
            </h2>

            <div className={styles.formGroup}>
              <label className={styles.label}>Target Web Application URLs</label>
              {targetUrls.map((url, idx) => (
                <div key={idx} className={styles.targetUrlRow}>
                  <input
                    type="url"
                    className={styles.input}
                    placeholder="https://app.example.local/login"
                    value={url}
                    aria-label={`Target URL ${idx + 1}`}
                    onChange={(e) => handleUrlChange(idx, e.target.value)}
                  />
                  {targetUrls.length > 1 && (
                    <button
                      type="button"
                      className={styles.removeBtn}
                      onClick={() => handleRemoveUrl(idx)}
                      aria-label={`Remove URL ${idx + 1}`}
                    >
                      ✕ Remove
                    </button>
                  )}
                </div>
              ))}
              <button
                type="button"
                className={styles.addUrlBtn}
                onClick={handleAddUrl}
                id="add-target-url-btn"
              >
                + Add Another Target URL
              </button>
            </div>

            <div className={styles.formGroup} style={{ maxWidth: "200px", marginTop: "var(--space-2)" }}>
              <label htmlFor="parallel-tasks-input" className={styles.label}>
                Max Parallel Tasks (1–10)
              </label>
              <input
                id="parallel-tasks-input"
                type="number"
                min={1}
                max={10}
                className={styles.input}
                value={maxParallelTasks}
                onChange={(e) => setMaxParallelTasks(Number(e.target.value))}
              />
            </div>
          </div>

          {/* Submission Bar */}
          <div className={styles.submitBar}>
            <Link to={currentProjectId ? `/projects/${currentProjectId}/plans` : "/plans"}>
              <Button variant="secondary" type="button">
                Cancel
              </Button>
            </Link>

            <Button
              variant="primary"
              type="submit"
              disabled={planMissionMutation.isPending}
              id="plan-mission-submit-btn"
            >
              {planMissionMutation.isPending ? "Decomposing Mission DAG..." : "Decompose Mission DAG →"}
            </Button>
          </div>
        </form>
      </div>
    </AppShell>
  );
}
