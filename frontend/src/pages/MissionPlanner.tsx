import { useEffect, useState } from "react";
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

interface DecompositionStage {
  step: number;
  title: string;
  desc: string;
  icon: string;
}

const DECOMPOSITION_STAGES: DecompositionStage[] = [
  {
    step: 1,
    title: "RAG Semantic Context Indexer",
    desc: "Chunking OpenAPI schemas & Markdown requirements with BM25 semantic retriever...",
    icon: "📚",
  },
  {
    step: 2,
    title: "LangGraph Multi-Agent State Machine",
    desc: "Supervisor agent decomposing target journeys into high-value specialized tasks...",
    icon: "🤖",
  },
  {
    step: 3,
    title: "Specialist Agents Contract Binding",
    desc: "API Specialist binding JWT headers & JSONPaths; UI Specialist generating Playwright steps...",
    icon: "🛡️",
  },
  {
    step: 4,
    title: "Evaluator Kahn DAG & SSRF Guardrail",
    desc: "Enforcing topological acyclicity, variable export consistency & target allowlists...",
    icon: "⚡",
  },
  {
    step: 5,
    title: "Compiling Autonomous Test Plan",
    desc: "Generating immutable draft DAG with live telemetry ready for mission approval...",
    icon: "🎯",
  },
];

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
  const maxParallelTasks = 4;

  // AI & Multi-Agent Orchestration Settings
  const [aiModel, setAiModel] = useState("nvidia/nemotron-4-340b-instruct");
  const [orchestrationEngine, setOrchestrationEngine] = useState<"langgraph" | "topological_dag">("langgraph");
  const [useRag, setUseRag] = useState(true);
  const [testStrategy, setTestStrategy] = useState<"full_regression" | "critical_smoke" | "security_boundary">("full_regression");
  const [temperature, setTemperature] = useState(0.1);

  const [validationError, setValidationError] = useState<string | null>(null);

  // Loading state & step progression animation
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [activeStageIndex, setActiveStageIndex] = useState(0);

  useEffect(() => {
    if (!planMissionMutation.isPending) {
      setElapsedSeconds(0);
      setActiveStageIndex(0);
      return;
    }

    const timerInterval = setInterval(() => {
      setElapsedSeconds((prev) => prev + 1);
    }, 1000);

    const stageInterval = setInterval(() => {
      setActiveStageIndex((prev) => (prev < DECOMPOSITION_STAGES.length - 1 ? prev + 1 : prev));
    }, 2800);

    return () => {
      clearInterval(timerInterval);
      clearInterval(stageInterval);
    };
  }, [planMissionMutation.isPending]);

  // Auto-prefill first target URL from environment base_url
  useEffect(() => {
    const selectedEnvId = environmentId || envList?.items[0]?.id;
    const currentEnv = envList?.items.find((e) => e.id === selectedEnvId);
    if (currentEnv?.base_url) {
      setTargetUrls((prev) => {
        if (prev.length === 1 && !prev[0].trim()) {
          return [currentEnv.base_url];
        }
        return prev;
      });
    }
  }, [environmentId, envList]);

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
        ai_provider_name: aiModel.includes("nemotron") ? "nemotron" : aiModel.includes("gpt") ? "openai" : "anthropic",
        ai_model: aiModel,
        orchestration_engine: orchestrationEngine,
        use_rag: useRag,
        test_strategy: testStrategy,
        temperature: temperature,
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
          <div className={styles.breadcrumbWrapper}>
            <Link
              to={currentProjectId ? `/projects/${currentProjectId}/plans` : "/plans"}
              className={styles.backLink}
            >
              ← Back to Plans
            </Link>
            <span className={styles.badgePill}>
              ⚡ LangGraph Multi-Agent • RAG-Augmented • {aiModel.includes("nemotron") ? "Nemotron-340B" : "GPT-4o"}
            </span>
          </div>
          <h1 className={styles.title}>Plan Autonomous Multi-Agent Mission</h1>
          <p className={styles.subtitle}>
            Upload specifications, OpenAPI contracts, and test scenarios. The LangGraph Supervisor and Specialist
            agents will decompose your inputs into an adapter-driven task DAG with prerequisite dependencies.
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
          {/* Section 1: AI Multi-Agent & RAG Engine Configuration */}
          <div className={`${styles.section} ${styles.aiSection}`}>
            <div className={styles.aiSectionHeader}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "0.5rem" }}>
                <div className={styles.aiActivePill}>
                  <span className={styles.aiActivePillDot} />
                  <span>LangGraph Core v1.2 Active</span>
                </div>
                <span style={{ fontSize: "0.74rem", color: "#94a3b8", fontFamily: "monospace" }}>
                  Autonomous Multi-Agent Testing Mesh
                </span>
              </div>
              <h2 className={styles.sectionTitle}>
                <span className={`${styles.stepNumber} ${styles.aiStepNumber}`}>01</span>
                <span>AI Multi-Agent & RAG Engine Configuration</span>
              </h2>
              <p className={styles.sectionDesc}>
                Configure LLM reasoning models, LangGraph multi-agent orchestration, and RAG semantic chunking for zero-hallucination test synthesis.
              </p>
            </div>

            <div className={styles.aiGrid}>
              {/* Card 1: AI Reasoning Model */}
              <div className={styles.aiFeatureCard}>
                <div className={styles.aiCardHeader}>
                  <span className={styles.aiCardTitle}>🤖 AI Reasoning Model</span>
                  <span className={`${styles.aiCardBadge} ${styles.aiCardBadgeActive}`}>NVIDIA NIM</span>
                </div>
                <select
                  id="ai-model-select"
                  className={styles.select}
                  value={aiModel}
                  onChange={(e) => setAiModel(e.target.value)}
                >
                  <option value="nvidia/nemotron-4-340b-instruct">
                    Nemotron 340B (NVIDIA NIM)
                  </option>
                  <option value="gpt-4o">
                    GPT-4o (OpenAI Multimodal)
                  </option>
                  <option value="claude-3-5-sonnet-20241022">
                    Claude 3.5 Sonnet (Anthropic)
                  </option>
                  <option value="llama-3.3-70b-instruct">
                    Llama 3.3 70B (Local vLLM)
                  </option>
                </select>
                <span className={styles.aiCardHelper}>
                  ⚡ 128K Context Window • Deterministic JSON Schema Enforcement
                </span>
              </div>

              {/* Card 2: Multi-Agent Orchestration Framework */}
              <div className={styles.aiFeatureCard}>
                <div className={styles.aiCardHeader}>
                  <span className={styles.aiCardTitle}>🕸️ Orchestration Framework</span>
                  <span className={`${styles.aiCardBadge} ${styles.aiCardBadgeActive}`}>Multi-Agent</span>
                </div>
                <select
                  id="orchestration-engine-select"
                  className={styles.select}
                  value={orchestrationEngine}
                  onChange={(e) => setOrchestrationEngine(e.target.value as "langgraph" | "topological_dag")}
                >
                  <option value="langgraph">
                    LangGraph State Machine (Recommended)
                  </option>
                  <option value="topological_dag">
                    Topological Kahn DAG Engine
                  </option>
                </select>
                <span className={styles.aiCardHelper}>
                  🔄 Supervisor ➔ RAG ➔ API Specialist ➔ UI Specialist ➔ Guardrail
                </span>
              </div>

              {/* Card 3: Test Planning Strategy */}
              <div className={styles.aiFeatureCard}>
                <div className={styles.aiCardHeader}>
                  <span className={styles.aiCardTitle}>🎯 Planning Strategy</span>
                  <span className={styles.aiCardBadge}>Autonomous</span>
                </div>
                <select
                  id="test-strategy-select"
                  className={styles.select}
                  value={testStrategy}
                  onChange={(e) => setTestStrategy(e.target.value as "full_regression" | "critical_smoke" | "security_boundary")}
                >
                  <option value="full_regression">
                    Full E2E Regression (API + UI)
                  </option>
                  <option value="critical_smoke">
                    Critical P0 Smoke Path
                  </option>
                  <option value="security_boundary">
                    Security Boundary & Negative
                  </option>
                </select>
                <span className={styles.aiCardHelper}>
                  🛡️ Multi-adapter coverage: REST HTTP + Playwright Chromium
                </span>
              </div>

              {/* Card 4: Reasoning Strictness & Determinism */}
              <div className={`${styles.aiFeatureCard} ${styles.sliderCard}`}>
                <div className={styles.aiCardHeader}>
                  <span className={styles.aiCardTitle}>⚡ Reasoning Strictness</span>
                  <span className={`${styles.aiCardBadge} ${styles.aiCardBadgeActive}`}>
                    {temperature.toFixed(2)} • {temperature <= 0.15 ? "Strict" : "Exploratory"}
                  </span>
                </div>
                <div className={styles.sliderTrackWrapper}>
                  <input
                    id="temp-slider"
                    type="range"
                    min="0.0"
                    max="0.7"
                    step="0.05"
                    value={temperature}
                    onChange={(e) => setTemperature(parseFloat(e.target.value))}
                    className={styles.rangeInputCustom}
                    aria-label="Model Temperature"
                  />
                  <div className={styles.sliderPresetRow}>
                    <button
                      type="button"
                      className={`${styles.sliderPresetBtn} ${temperature === 0.0 ? styles.sliderPresetBtnActive : ""}`}
                      onClick={() => setTemperature(0.0)}
                    >
                      0.00 Strict
                    </button>
                    <button
                      type="button"
                      className={`${styles.sliderPresetBtn} ${temperature === 0.1 ? styles.sliderPresetBtnActive : ""}`}
                      onClick={() => setTemperature(0.1)}
                    >
                      0.10 Balanced
                    </button>
                    <button
                      type="button"
                      className={`${styles.sliderPresetBtn} ${temperature === 0.35 ? styles.sliderPresetBtnActive : ""}`}
                      onClick={() => setTemperature(0.35)}
                    >
                      0.35 Exploratory
                    </button>
                  </div>
                </div>
              </div>

              {/* Row 3: Full-Width RAG Context Banner */}
              <div
                className={styles.ragBannerCard}
                onClick={() => setUseRag(!useRag)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setUseRag(!useRag); }}
                aria-pressed={useRag}
              >
                <div className={styles.ragLeft}>
                  <div className={styles.ragIconBox}>📚</div>
                  <div className={styles.ragContent}>
                    <div className={styles.ragTitleRow}>
                      <span className={styles.ragTitle}>RAG-Augmented Endpoint & Requirement Retrieval</span>
                      <span className={styles.ragActivePill}>{useRag ? "ACTIVE • ZERO-HALLUCINATION" : "DISABLED"}</span>
                    </div>
                    <p className={styles.ragDesc}>
                      Automatically chunks OpenAPI 3.0+ endpoints and Markdown requirements into BM25 vector units. Delivers exact request bodies, parameter schemas, and JWT tokens directly into agent prompts without context overflow.
                    </p>
                  </div>
                </div>
                <div className={`${styles.switchTrack} ${useRag ? styles.switchTrackChecked : ""}`}>
                  <div className={`${styles.switchThumb} ${useRag ? styles.switchThumbChecked : ""}`} />
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Target Environment & Application URLs */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                <span className={styles.stepNumber}>02</span>
                <span>Target Environment & Application URLs</span>
              </h2>
              <p className={styles.sectionDesc}>
                Select the project environment and specify the target application URLs to test (e.g. FastAPI backend and React frontend).
              </p>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="environment-select" className={styles.label}>
                Environment<span className={styles.labelRequired}>*</span>
              </label>
              <select
                id="environment-select"
                className={styles.select}
                value={environmentId || envList?.items[0]?.id || ""}
                onChange={(e) => {
                  const newEnvId = e.target.value;
                  setEnvironmentId(newEnvId);
                  const selected = envList?.items.find((item) => item.id === newEnvId);
                  if (selected?.base_url) {
                    setTargetUrls((prev) => {
                      if (prev.length <= 1) return [selected.base_url];
                      return [selected.base_url, ...prev.slice(1)];
                    });
                  }
                }}
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

            {/* Target Application URLs (URL 1: FastAPI, URL 2: React) */}
            <div className={styles.formGroup} style={{ marginTop: "0.75rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem" }}>
                <label className={styles.label}>Target Application URLs</label>
                <span style={{ fontSize: "0.8rem", color: "var(--color-primary, #60a5fa)" }}>
                  💡 URL 1: Backend (FastAPI :8000) • URL 2: Frontend (React :5173)
                </span>
              </div>
              {targetUrls.map((url, idx) => (
                <div key={idx} className={styles.targetUrlRow}>
                  <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flex: 1 }}>
                    <span style={{
                      fontSize: "0.78rem",
                      fontWeight: 700,
                      color: "#60a5fa",
                      background: "rgba(59, 130, 246, 0.12)",
                      border: "1px solid rgba(59, 130, 246, 0.25)",
                      padding: "0.45rem 0.75rem",
                      borderRadius: "6px",
                      whiteSpace: "nowrap"
                    }}>
                      URL {idx + 1}
                    </span>
                    <input
                      type="url"
                      className={styles.input}
                      placeholder={idx === 0 ? "http://localhost:8000" : "http://localhost:5173"}
                      value={url}
                      aria-label={`Target URL ${idx + 1}`}
                      onChange={(e) => handleUrlChange(idx, e.target.value)}
                    />
                  </div>
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
                + Add Another Target URL (e.g. React Frontend)
              </button>
            </div>
          </div>

          {/* Section 3: Requirements Specification (Markdown) */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                <span className={styles.stepNumber}>03</span>
                <span>Requirements Specification (requirements.md)</span>
              </h2>
              <p className={styles.sectionDesc}>
                Markdown specification describing critical user journeys, business rules, and acceptance criteria.
              </p>
            </div>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span className={styles.fileIcon}>📄</span>
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

          {/* Section 4: OpenAPI Specification (Optional) */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                <span className={styles.stepNumber}>04</span>
                <span>API Specification (openapi.json / yaml — Optional)</span>
              </h2>
              <p className={styles.sectionDesc}>
                Enables deterministic endpoint analysis, request schema generation, and HTTP assertion mapping.
              </p>
            </div>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span className={styles.fileIcon}>⚡</span>
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

          {/* Section 5: Scenarios (scenarios.json — Optional) */}
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <h2 className={styles.sectionTitle}>
                <span className={styles.stepNumber}>05</span>
                <span>Test Scenarios (scenarios.json — Optional)</span>
              </h2>
              <p className={styles.sectionDesc}>
                Structured user scenarios or regression flow criteria to guide task generation.
              </p>
            </div>

            <div className={styles.fileDropZone}>
              <div className={styles.fileInfo}>
                <span className={styles.fileIcon}>🎭</span>
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

          {/* Submission Bar */}
          <div className={styles.submitBar}>
            <Link to={currentProjectId ? `/projects/${currentProjectId}/plans` : "/plans"}>
              <Button variant="secondary" type="button" disabled={planMissionMutation.isPending}>
                Cancel
              </Button>
            </Link>

            <Button
              variant="primary"
              type="submit"
              loading={planMissionMutation.isPending}
              disabled={planMissionMutation.isPending}
              id="plan-mission-submit-btn"
            >
              {planMissionMutation.isPending ? "Decomposing Mission DAG..." : "Decompose Mission DAG →"}
            </Button>
          </div>
        </form>

        {/* ── High-End AI Multi-Agent Decomposition Overlay & Loader ──────── */}
        {planMissionMutation.isPending && (
          <div
            className={styles.loaderOverlay}
            role="dialog"
            aria-modal="true"
            aria-labelledby="loader-modal-title"
          >
            <div className={styles.loaderCard}>
              {/* Animated AI Agent Core Orb */}
              <div className={styles.aiOrbWrapper}>
                <div className={styles.aiOrbRingOuter} />
                <div className={styles.aiOrbRingInner} />
                <div className={styles.aiOrbCore}>
                  <span className={styles.aiOrbIcon}>⚡</span>
                </div>
              </div>

              {/* Title & Status Header */}
              <div className={styles.loaderHeader}>
                <div className={styles.aiBadgePill}>
                  <span className={styles.pulseDot} />
                  <span>NVIDIA Nemotron 120B • Autonomous Multi-Agent Core</span>
                </div>
                <h3 id="loader-modal-title" className={styles.loaderTitle}>
                  Synthesizing Mission DAG
                </h3>
                <p className={styles.loaderSubtitle}>
                  Supervisor and Specialist agents are analyzing your specifications and target environments to decompose an executable test graph.
                </p>
              </div>

              {/* Live Target Scope Tags */}
              {targetUrls.filter((u) => u.trim().length > 0).length > 0 && (
                <div className={styles.loaderTargetList}>
                  <span className={styles.targetListLabel}>Target Scope:</span>
                  {targetUrls
                    .filter((u) => u.trim().length > 0)
                    .map((url, i) => (
                      <span key={i} className={styles.targetBadge}>
                        {url.includes("8000") ? "⚡ FastAPI: " : url.includes("5173") ? "⚛️ React: " : "🌐 "}
                        {url}
                      </span>
                    ))}
                </div>
              )}

              {/* Step Progression Ticker */}
              <div className={styles.stepsContainer}>
                {DECOMPOSITION_STAGES.map((stage, idx) => {
                  const isDone = idx < activeStageIndex;
                  const isCurrent = idx === activeStageIndex;

                  return (
                    <div
                      key={stage.step}
                      className={`${styles.stepRow} ${
                        isCurrent
                          ? styles.stepRowCurrent
                          : isDone
                          ? styles.stepRowDone
                          : styles.stepRowUpcoming
                      }`}
                    >
                      <div className={styles.stepIndicator}>
                        {isDone ? (
                          <span className={styles.stepCheck}>✓</span>
                        ) : isCurrent ? (
                          <span className={styles.stepSpinnerMini} />
                        ) : (
                          <span className={styles.stepDot} />
                        )}
                      </div>
                      <div className={styles.stepTextWrapper}>
                        <div className={styles.stepTitle}>
                          <span>{stage.title}</span>
                          {isCurrent && <span className={styles.stepActiveBadge}>In Progress</span>}
                        </div>
                        <div className={styles.stepDesc}>{stage.desc}</div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Shimmer Progress Bar */}
              <div className={styles.progressBarWrapper}>
                <div
                  className={styles.progressBarFill}
                  style={{
                    width: `${Math.min(95, ((activeStageIndex + 1) / DECOMPOSITION_STAGES.length) * 100)}%`,
                  }}
                />
              </div>

              {/* Footer Live Telemetry */}
              <div className={styles.loaderFooter}>
                <span className={styles.elapsedTime}>
                  ⏱️ Elapsed: {String(Math.floor(elapsedSeconds / 60)).padStart(2, "0")}:
                  {String(elapsedSeconds % 60).padStart(2, "0")}s
                </span>
                <span className={styles.loaderTip}>
                  Please keep this tab open • DAG generation in progress
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
