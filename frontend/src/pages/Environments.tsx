/**
 * Environments.tsx — Project-scoped Test Environment Management.
 * Supports CRUD, allowlist management, secret reference configuration,
 * and status toggling without exposing raw secrets.
 */
import React, { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import {
  useCreateEnvironment,
  useDeleteEnvironment,
  useEnvironments,
  useUpdateEnvironment,
} from "@/hooks/useEnvironments";
import { useProjects } from "@/hooks/useProjects";
import { CreateProjectModal } from "@/components/modals/CreateProjectModal";
import type {
  CreateEnvironmentInput,
  Environment,
} from "@/types/environment";
import styles from "./Environments.module.css";

export default function Environments() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const { data: projectsData } = useProjects();
  const projects = projectsData?.items ?? [];

  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const projectId =
    urlProjectId ||
    selectedProjectId ||
    (projects.length > 0 ? projects[0].id : "");
  const [includeArchived, setIncludeArchived] = useState<boolean>(false);

  // Modals state
  const [isCreateOpen, setIsCreateOpen] = useState<boolean>(false);
  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState<boolean>(false);
  const [editingEnv, setEditingEnv] = useState<Environment | null>(null);
  const [deletingEnv, setDeletingEnv] = useState<Environment | null>(null);

  // Queries & Mutations
  const {
    data,
    isLoading,
    error,
    refetch,
  } = useEnvironments(projectId, includeArchived);
  const createMutation = useCreateEnvironment(projectId);
  const updateMutation = useUpdateEnvironment(projectId);
  const deleteMutation = useDeleteEnvironment(projectId);

  // Copy URL helper
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const handleCopyUrl = (id: string, url: string) => {
    void navigator.clipboard.writeText(url).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    });
  };

  // Toggle active/archived status
  const handleToggleStatus = (env: Environment) => {
    const nextStatus = env.status === "active" ? "archived" : "active";
    void updateMutation.mutateAsync({
      environmentId: env.id,
      data: { status: nextStatus },
    });
  };

  return (
    <div className={styles.page}>
      {/* Header */}
      <header className={styles.header}>
        <div className={styles.titleArea}>
          <h1 className={styles.title}>Test Environments</h1>
          <p className={styles.subtitle}>
            Manage target endpoints, network allowlists, and secret references
            for test runs.
          </p>
        </div>
        <div className={styles.headerActions}>
          <Link to="/">
            <Button variant="outline" size="sm">
              ← Dashboard
            </Button>
          </Link>
          <Button
            variant="primary"
            onClick={() => {
              if (!projectId) {
                setIsCreateProjectOpen(true);
              } else {
                setIsCreateOpen(true);
              }
            }}
            id="create-environment-btn"
          >
            + New Environment
          </Button>
        </div>
      </header>

      {/* Controls Bar */}
      <div className={styles.controlsBar}>
        <div className={styles.projectIdInputWrapper}>
          <label htmlFor="project-id-select" className={styles.label}>
            Active Project:
          </label>
          {projects.length > 0 ? (
            <select
              id="project-id-select"
              className={styles.projectInput}
              value={projectId}
              onChange={(e) => setSelectedProjectId(e.target.value)}
              aria-label="Select Project"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          ) : (
            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              <input
                id="project-id-input"
                type="text"
                className={styles.projectInput}
                value={projectId}
                onChange={(e) => setSelectedProjectId(e.target.value.trim())}
                placeholder="No project active"
                disabled
              />
              <Button size="sm" variant="outline" onClick={() => setIsCreateProjectOpen(true)}>
                + New Project
              </Button>
            </div>
          )}
        </div>

        <div className={styles.filterGroup}>
          <label className={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            Show archived environments
          </label>
        </div>
      </div>

      {/* Zero projects state */}
      {!isLoading && projects.length === 0 && (
        <Card>
          <CardBody>
            <EmptyState
              icon="📁"
              title="No Project Found"
              description="Test environments must belong to a Project. Please create your first project before configuring environments."
              action={
                <Button variant="primary" onClick={() => setIsCreateProjectOpen(true)}>
                  + Create First Project
                </Button>
              }
            />
          </CardBody>
        </Card>
      )}

      {/* Main Content Area */}
      {isLoading && (
        <Spinner fullPage label="Loading project environments…" />
      )}

      {error && (
        <ErrorMessage
          title="Failed to load environments"
          message={error.message}
        >
          <Button variant="outline" size="sm" onClick={() => { void refetch(); }}>
            Retry
          </Button>
        </ErrorMessage>
      )}

      {!isLoading && !error && data && (
        <>
          {data.items.length === 0 ? (
            <Card>
              <CardBody>
                <EmptyState
                  icon="🌐"
                  title="No environments found"
                  description={
                    includeArchived
                      ? "No active or archived environments exist for this project."
                      : "Create a test environment pointing to your API or application target."
                  }
                  action={
                    <Button
                      variant="primary"
                      onClick={() => setIsCreateOpen(true)}
                    >
                      Create First Environment
                    </Button>
                  }
                />
              </CardBody>
            </Card>
          ) : (
            <div className={styles.grid}>
              {data.items.map((env) => (
                <div key={env.id} className={styles.envCard} data-testid={`env-card-${env.id}`}>
                  <div className={styles.envCardHeader}>
                    <div className={styles.envNameWrapper}>
                      <h2 className={styles.envName}>{env.name}</h2>
                    </div>
                    <Badge
                      variant={env.status === "active" ? "passed" : "cancelled"}
                    >
                      {env.status.toUpperCase()}
                    </Badge>
                  </div>

                  {env.description && (
                    <p className={styles.envDescription}>{env.description}</p>
                  )}

                  {/* Target Base URL */}
                  <div className={styles.urlBlock}>
                    <span className={styles.urlText} title={env.base_url}>
                      {env.base_url}
                    </span>
                    <button
                      type="button"
                      className={styles.copyBtn}
                      onClick={() => handleCopyUrl(env.id, env.base_url)}
                      aria-label="Copy Base URL"
                    >
                      {copiedId === env.id ? "Copied!" : "Copy"}
                    </button>
                  </div>

                  {/* Metadata and Allowlists */}
                  <div className={styles.metadataSection}>
                    <div className={styles.metaItem}>
                      <span className={styles.metaLabel}>SSRF Allowlist:</span>
                      {env.allowlist && env.allowlist.length > 0 ? (
                        <div className={styles.tagsWrapper}>
                          {env.allowlist.map((host, idx) => (
                            <span key={idx} className={styles.tag}>
                              {host}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className={styles.helpText}>Default target only</span>
                      )}
                    </div>

                    <div className={styles.metaItem}>
                      <span className={styles.metaLabel}>Configured Secrets:</span>
                      {env.secret_refs && Object.keys(env.secret_refs).length > 0 ? (
                        <div className={styles.secretsIndicator}>
                          <span>🔒</span>
                          <span>
                            {Object.keys(env.secret_refs).length} reference
                            {Object.keys(env.secret_refs).length === 1 ? "" : "s"} (
                            {Object.keys(env.secret_refs).join(", ")})
                          </span>
                        </div>
                      ) : (
                        <span className={styles.helpText}>No secret references</span>
                      )}
                    </div>
                  </div>

                  {/* Card Footer Actions */}
                  <div className={styles.cardFooter}>
                    <span className={styles.cardDate}>
                      Updated: {new Date(env.updated_at).toLocaleDateString()}
                    </span>
                    <div className={styles.cardActions}>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setEditingEnv(env)}
                      >
                        Edit
                      </Button>
                      <Button
                        variant={env.status === "active" ? "ghost" : "secondary"}
                        size="sm"
                        onClick={() => { handleToggleStatus(env); }}
                      >
                        {env.status === "active" ? "Disable" : "Enable"}
                      </Button>
                      <Button
                        variant="danger"
                        size="sm"
                        onClick={() => setDeletingEnv(env)}
                        aria-label={`Delete ${env.name}`}
                      >
                        Archive
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Create Modal */}
      {isCreateOpen && (
        <EnvironmentFormModal
          title="Create New Environment"
          isOpen={isCreateOpen}
          hasProject={Boolean(projectId)}
          onClose={() => setIsCreateOpen(false)}
          onSubmit={(input) => {
            void createMutation.mutateAsync(input).then(() => {
              setIsCreateOpen(false);
            });
          }}
          isLoading={createMutation.isPending}
          apiError={createMutation.error ? createMutation.error.message : null}
        />
      )}

      {/* Edit Modal */}
      {editingEnv && (
        <EnvironmentFormModal
          title={`Edit: ${editingEnv.name}`}
          isOpen={Boolean(editingEnv)}
          hasProject={Boolean(projectId)}
          initialData={editingEnv}
          onClose={() => setEditingEnv(null)}
          onSubmit={(input) => {
            void updateMutation.mutateAsync({
              environmentId: editingEnv.id,
              data: input,
            }).then(() => {
              setEditingEnv(null);
            });
          }}
          isLoading={updateMutation.isPending}
          apiError={updateMutation.error ? updateMutation.error.message : null}
        />
      )}

      {/* Archive / Delete Confirmation Modal */}
      {deletingEnv && (
        <div className={styles.modalBackdrop} role="dialog" aria-modal="true">
          <div className={styles.modalDialog}>
            <div className={styles.modalHeader}>
              <h2 className={styles.modalTitle}>Archive Environment</h2>
              <button
                type="button"
                className={styles.closeBtn}
                onClick={() => setDeletingEnv(null)}
                aria-label="Close"
              >
                ✕
              </button>
            </div>
            <div className={styles.modalBody}>
              <p>
                Are you sure you want to archive <strong>{deletingEnv.name}</strong>?
              </p>
              <div className={styles.modalAlert}>
                This environment will be soft-deleted. Existing test runs and history
                will remain intact, but new runs cannot target archived environments.
              </div>
            </div>
            <div className={styles.modalFooter}>
              <Button
                variant="outline"
                onClick={() => setDeletingEnv(null)}
                disabled={deleteMutation.isPending}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                loading={deleteMutation.isPending}
                onClick={() => {
                  void deleteMutation.mutateAsync(deletingEnv.id).then(() => {
                    setDeletingEnv(null);
                  });
                }}
              >
                Confirm Archive
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Create Project Modal */}
      <CreateProjectModal
        isOpen={isCreateProjectOpen}
        onClose={() => setIsCreateProjectOpen(false)}
      />
    </div>
  );
}

// ── Environment Form Modal ───────────────────────────────────────────────────

interface EnvironmentFormModalProps {
  title: string;
  isOpen: boolean;
  hasProject: boolean;
  initialData?: Environment | null;
  onClose: () => void;
  onSubmit: (input: CreateEnvironmentInput) => void;
  isLoading: boolean;
  apiError: string | null;
}

function EnvironmentFormModal({
  title,
  isOpen,
  hasProject,
  initialData,
  onClose,
  onSubmit,
  isLoading,
  apiError,
}: EnvironmentFormModalProps) {
  const [name, setName] = useState(initialData?.name || "");
  const [baseUrl, setBaseUrl] = useState(initialData?.base_url || "");
  const [description, setDescription] = useState(initialData?.description || "");
  const [allowlistText, setAllowlistText] = useState(
    initialData?.allowlist ? initialData.allowlist.join("\n") : ""
  );

  // Secret refs state
  const [secretRefs, setSecretRefs] = useState<Array<{ key: string; ref: string }>>(
    initialData?.secret_refs
      ? Object.entries(initialData.secret_refs).map(([key, ref]) => ({ key, ref }))
      : []
  );

  // Form field errors
  const [errors, setErrors] = useState<Record<string, string>>({});

  const validate = (): boolean => {
    const errs: Record<string, string> = {};
    if (!name.trim()) {
      errs.name = "Environment name is required.";
    }

    const trimmedUrl = baseUrl.trim();
    if (!trimmedUrl) {
      errs.baseUrl = "Base URL is required.";
    } else if (!/^https?:\/\//i.test(trimmedUrl)) {
      errs.baseUrl = "Base URL must begin with http:// or https://.";
    } else if (
      trimmedUrl.includes("169.254.169.254") ||
      trimmedUrl.includes("metadata.google.internal")
    ) {
      errs.baseUrl = "Cloud metadata endpoints are prohibited by SSRF security policy.";
    }

    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleAddSecretRef = () => {
    setSecretRefs([...secretRefs, { key: "", ref: "" }]);
  };

  const handleRemoveSecretRef = (index: number) => {
    setSecretRefs(secretRefs.filter((_, i) => i !== index));
  };

  const handleSecretRefChange = (
    index: number,
    field: "key" | "ref",
    value: string
  ) => {
    const updated = [...secretRefs];
    updated[index][field] = value;
    setSecretRefs(updated);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    // Parse allowlist lines
    const allowlist = allowlistText
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

    // Parse secret refs
    const validSecretRefs: Record<string, string> = {};
    secretRefs.forEach(({ key, ref }) => {
      const k = key.trim();
      const r = ref.trim();
      if (k && r) {
        validSecretRefs[k] = r;
      }
    });

    const payload: CreateEnvironmentInput = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      description: description.trim() || null,
      allowlist: allowlist.length > 0 ? allowlist : null,
      secret_refs: Object.keys(validSecretRefs).length > 0 ? validSecretRefs : null,
    };

    onSubmit(payload);
  };

  if (!isOpen) return null;

  return (
    <div className={styles.modalBackdrop} role="dialog" aria-modal="true">
      <div className={styles.modalDialog}>
        <div className={styles.modalHeader}>
          <h2 className={styles.modalTitle}>{title}</h2>
          <button
            type="button"
            className={styles.closeBtn}
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} className={styles.modalForm}>
          <div className={styles.modalBody}>
            {!hasProject && (
              <div className={styles.modalAlert}>
                ⚠️ No project selected. Please select or create a project before saving an environment.
              </div>
            )}
            {apiError && <div className={styles.modalAlert}>{apiError}</div>}

            <div className={styles.formGroup}>
              <label htmlFor="env-name" className={styles.label}>
                Environment Name <span className={styles.required}>*</span>
              </label>
              <input
                id="env-name"
                type="text"
                className={`${styles.input} ${errors.name ? styles.inputError : ""}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Staging API, Production Gateway"
              />
              {errors.name && <span className={styles.fieldError}>{errors.name}</span>}
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="env-url" className={styles.label}>
                Base URL <span className={styles.required}>*</span>
              </label>
              <input
                id="env-url"
                type="text"
                className={`${styles.input} ${errors.baseUrl ? styles.inputError : ""}`}
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="http://localhost:8000"
              />
              {errors.baseUrl && (
                <span className={styles.fieldError}>{errors.baseUrl}</span>
              )}
              <span className={styles.helpText}>
                Must include scheme (http/https). Cloud metadata endpoints are blocked.
              </span>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="env-description" className={styles.label}>
                Description
              </label>
              <textarea
                id="env-description"
                className={styles.textarea}
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Brief description of this environment's target purpose"
              />
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="env-allowlist" className={styles.label}>
                SSRF Allowlist (one host or host:port per line)
              </label>
              <textarea
                id="env-allowlist"
                className={styles.textarea}
                rows={3}
                value={allowlistText}
                onChange={(e) => setAllowlistText(e.target.value)}
                placeholder={"localhost:8000\nlocalhost:5173\n127.0.0.1:8000"}
              />
              <span className={styles.helpText}>
                Target hosts allowed for test execution (e.g. localhost:8000, localhost:5173).
              </span>
            </div>

            <div className={styles.formGroup}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span className={styles.label}>Secret References</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={handleAddSecretRef}
                >
                  + Add Reference
                </Button>
              </div>
              <span className={styles.helpText}>
                Store ONLY secret manager identifiers (e.g. vault://path/key or env:VAR).
                NEVER enter plaintext API keys, passwords, or secrets here.
              </span>

              {secretRefs.map((item, idx) => (
                <div key={idx} className={styles.secretRefRow}>
                  <input
                    type="text"
                    className={`${styles.input} ${styles.secretKeyInput}`}
                    placeholder="Reference name (e.g. api_key)"
                    value={item.key}
                    onChange={(e) => handleSecretRefChange(idx, "key", e.target.value)}
                  />
                  <input
                    type="text"
                    className={`${styles.input} ${styles.secretRefInput}`}
                    placeholder="Manager ID (e.g. vault://keys/auth)"
                    value={item.ref}
                    onChange={(e) => handleSecretRefChange(idx, "ref", e.target.value)}
                  />
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    onClick={() => handleRemoveSecretRef(idx)}
                    aria-label="Remove secret reference"
                  >
                    ✕
                  </Button>
                </div>
              ))}
            </div>
          </div>

          <div className={styles.modalFooter}>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={isLoading}
              disabled={!hasProject || isLoading}
            >
              Save Environment
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
