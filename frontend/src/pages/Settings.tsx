/**
 * Settings.tsx — Project Settings, Team Access, API Tokens, and Enterprise Audit Trail.
 */
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Header } from "@/components/layout/Header";
import { Sidebar } from "@/components/layout/Sidebar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/hooks/useAuth";
import { useProjects } from "@/hooks/useProjects";
import {
  addProjectMember,
  createApiToken,
  listApiTokens,
  listProjectAuditEvents,
  listProjectMembers,
  removeProjectMember,
  revokeApiToken,
  type ApiTokenItem,
  type AuditEventItem,
  type CreatedTokenResult,
  type ProjectMember,
} from "@/lib/api-client";
import styles from "./Settings.module.css";

export default function Settings() {
  const { projectId: urlProjectId } = useParams<{ projectId?: string }>();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const { data: projectsData } = useProjects();
  const projects = projectsData?.items ?? [];

  const projectId =
    urlProjectId ||
    (projects.length > 0 ? projects[0].id : "");

  const activeProject = projects.find((p) => p.id === projectId);

  const [activeTab, setActiveTab] = useState<"team" | "tokens" | "audit">("team");

  // ── Team State ───────────────────────────────────────────────────────────────
  const [isAddMemberOpen, setIsAddMemberOpen] = useState(false);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberRole, setMemberRole] = useState<"owner" | "maintainer" | "viewer">("viewer");
  const [teamError, setTeamError] = useState<string | null>(null);

  // ── Token State ──────────────────────────────────────────────────────────────
  const [isTokenModalOpen, setIsTokenModalOpen] = useState(false);
  const [tokenName, setTokenName] = useState("");
  const [tokenDays, setTokenDays] = useState<string>("30");
  const [newlyCreatedToken, setNewlyCreatedToken] = useState<CreatedTokenResult | null>(null);
  const [copiedToken, setCopiedToken] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);

  // ── Audit State ──────────────────────────────────────────────────────────────
  const [selectedAuditType, setSelectedAuditType] = useState<string>("");
  const [inspectingEvent, setInspectingEvent] = useState<AuditEventItem | null>(null);

  // ── Queries ──────────────────────────────────────────────────────────────────
  const {
    data: membersData,
    isLoading: isMembersLoading,
    error: membersError,
    refetch: refetchMembers,
  } = useQuery({
    queryKey: ["project-members", projectId],
    queryFn: () => listProjectMembers(projectId),
    enabled: Boolean(projectId) && activeTab === "team",
  });

  const {
    data: tokensData,
    isLoading: isTokensLoading,
    error: tokensQueryError,
    refetch: refetchTokens,
  } = useQuery({
    queryKey: ["api-tokens"],
    queryFn: () => listApiTokens(),
    enabled: activeTab === "tokens",
  });

  const {
    data: auditData,
    isLoading: isAuditLoading,
    error: auditQueryError,
    refetch: refetchAudit,
  } = useQuery({
    queryKey: ["project-audit-events", projectId, selectedAuditType],
    queryFn: () =>
      listProjectAuditEvents(projectId, {
        event_type: selectedAuditType || undefined,
        limit: 50,
      }),
    enabled: Boolean(projectId) && activeTab === "audit",
  });

  // ── Team Mutations ───────────────────────────────────────────────────────────
  const addMemberMutation = useMutation({
    mutationFn: (data: { email: string; role: "owner" | "maintainer" | "viewer" }) =>
      addProjectMember(projectId, data),
    onSuccess: () => {
      setIsAddMemberOpen(false);
      setMemberEmail("");
      setTeamError(null);
      void queryClient.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
    onError: (err: Error) => {
      setTeamError(err.message || "Failed to add member.");
    },
  });

  const removeMemberMutation = useMutation({
    mutationFn: (userId: string) => removeProjectMember(projectId, userId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project-members", projectId] });
    },
    onError: (err: Error) => {
      setTeamError(err.message || "Failed to remove member.");
    },
  });

  // ── Token Mutations ──────────────────────────────────────────────────────────
  const createTokenMutation = useMutation({
    mutationFn: (data: { name: string; expires_in_days?: number }) =>
      createApiToken(data),
    onSuccess: (res) => {
      setNewlyCreatedToken(res);
      setIsTokenModalOpen(false);
      setTokenName("");
      setTokenError(null);
      void queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
    onError: (err: Error) => {
      setTokenError(err.message || "Failed to generate token.");
    },
  });

  const revokeTokenMutation = useMutation({
    mutationFn: (tokenId: string) => revokeApiToken(tokenId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["api-tokens"] });
    },
  });

  const handleCopyToken = () => {
    if (newlyCreatedToken) {
      void navigator.clipboard.writeText(newlyCreatedToken.token);
      setCopiedToken(true);
      setTimeout(() => setCopiedToken(false), 3000);
    }
  };

  const handleAddMemberSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!memberEmail.trim()) return;
    addMemberMutation.mutate({
      email: memberEmail.trim(),
      role: memberRole,
    });
  };

  const handleCreateTokenSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenName.trim()) return;
    const days = tokenDays === "never" ? undefined : parseInt(tokenDays, 10);
    createTokenMutation.mutate({
      name: tokenName.trim(),
      expires_in_days: days,
    });
  };

  return (
    <AppShell
      header={<Header currentProjectId={projectId} />}
      sidebar={<Sidebar currentProjectId={projectId} />}
    >
      <div className={styles.container}>
        <div className={styles.header}>
          <div className={styles.titleArea}>
            <h1 className={styles.title}>Project Settings & Access</h1>
            <p className={styles.subtitle}>
              Manage {activeProject?.name ?? "project"} team roles, personal API tokens for CI/CD, and compliance audit logs.
            </p>
          </div>
        </div>

        {/* Navigation Tabs */}
        <div className={styles.tabsList} role="tablist" aria-label="Settings navigation">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "team"}
            className={`${styles.tabButton} ${activeTab === "team" ? styles.tabButtonActive : ""}`}
            onClick={() => setActiveTab("team")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
              <path d="M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
            Team & Access
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "tokens"}
            className={`${styles.tabButton} ${activeTab === "tokens" ? styles.tabButtonActive : ""}`}
            onClick={() => setActiveTab("tokens")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
            API Tokens (CI/CD)
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "audit"}
            className={`${styles.tabButton} ${activeTab === "audit" ? styles.tabButtonActive : ""}`}
            onClick={() => setActiveTab("audit")}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            Audit Trail
          </button>
        </div>

        {/* ── TAB 1: TEAM & ACCESS ────────────────────────────────────────────── */}
        {activeTab === "team" && (
          <div className={styles.panelCard}>
            <div className={styles.panelHeader}>
              <div>
                <h2 className={styles.panelTitle}>Project Members</h2>
                <p className={styles.panelDescription}>
                  Control role-based access control (RBAC). Only owners can add members or alter permission levels.
                </p>
              </div>
              <Button
                variant="primary"
                onClick={() => {
                  setTeamError(null);
                  setIsAddMemberOpen(true);
                }}
              >
                + Add Member
              </Button>
            </div>

            {teamError && (
              <div style={{ marginBottom: "var(--space-4)" }}>
                <ErrorMessage message={teamError} />
              </div>
            )}

            {!projectId ? (
              <EmptyState
                icon="👥"
                title="No Active Project Selected"
                description="Create or select a project from the top navigation to manage team access and permissions."
              />
            ) : isMembersLoading ? (
              <div style={{ display: "flex", justifyContent: "center", padding: "var(--space-8)" }}>
                <Spinner label="Loading project team..." />
              </div>
            ) : membersError ? (
              <ErrorMessage message={(membersError).message || "Failed to load project members."}>
                <div style={{ marginTop: "var(--space-2)" }}>
                  <Button variant="secondary" size="sm" onClick={() => void refetchMembers()}>
                    Retry
                  </Button>
                </div>
              </ErrorMessage>
            ) : (membersData?.items ?? []).length === 0 ? (
              <EmptyState title="No members found" description="Invite collaborators to this project." />
            ) : (
              <div className={styles.tableWrapper}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Assigned Role</th>
                      <th>Joined Date</th>
                      <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(membersData?.items ?? []).map((m: ProjectMember) => (
                      <tr key={m.id}>
                        <td>
                          <div className={styles.userCell}>
                            <div className={styles.avatar}>
                              {m.email.substring(0, 2).toUpperCase()}
                            </div>
                            <div>
                              <div className={styles.userName}>{m.full_name || m.email.split("@")[0]}</div>
                              <div className={styles.userEmail}>{m.email}</div>
                            </div>
                          </div>
                        </td>
                        <td>
                          <Badge
                            variant={
                              m.role === "owner"
                                ? "approved"
                                : m.role === "maintainer"
                                ? "running"
                                : "default"
                            }
                          >
                            {m.role.toUpperCase()}
                          </Badge>
                        </td>
                        <td>
                          {new Date(m.created_at).toLocaleDateString([], {
                            year: "numeric",
                            month: "short",
                            day: "numeric",
                          })}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          {m.user_id !== user?.id && (
                            <Button
                              variant="danger"
                              size="sm"
                              onClick={() => {
                                if (window.confirm(`Are you sure you want to remove ${m.email} from this project?`)) {
                                  removeMemberMutation.mutate(m.user_id);
                                }
                              }}
                              disabled={removeMemberMutation.isPending}
                            >
                              Remove
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── TAB 2: API TOKENS (CI/CD) ───────────────────────────────────────── */}
        {activeTab === "tokens" && (
          <div className={styles.panelCard}>
            <div className={styles.panelHeader}>
              <div>
                <h2 className={styles.panelTitle}>Personal & CI/CD API Tokens</h2>
                <p className={styles.panelDescription}>
                  Authenticate command-line tools and GitHub Actions / GitLab CI pipelines using Bearer tokens.
                </p>
              </div>
              <Button
                variant="primary"
                onClick={() => {
                  setTokenError(null);
                  setIsTokenModalOpen(true);
                }}
              >
                + Generate New Token
              </Button>
            </div>

            {/* Flash Banner for Newly Created Token */}
            {newlyCreatedToken && (
              <div className={styles.tokenAlert}>
                <div className={styles.tokenAlertTitle}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  Token Generated Successfully ({newlyCreatedToken.name})
                </div>
                <p style={{ margin: 0, fontSize: "var(--font-size-sm)", color: "var(--color-text-secondary)" }}>
                  Make sure to copy your personal API token now as you will <strong>not be able to see it again</strong>!
                </p>
                <div className={styles.tokenBox}>
                  <code>{newlyCreatedToken.token}</code>
                  <Button variant="secondary" size="sm" onClick={handleCopyToken}>
                    {copiedToken ? "Copied! ✓" : "Copy Token"}
                  </Button>
                </div>
              </div>
            )}

            {isTokensLoading ? (
              <div style={{ display: "flex", justifyContent: "center", padding: "var(--space-8)" }}>
                <Spinner label="Loading tokens..." />
              </div>
            ) : tokensQueryError ? (
              <ErrorMessage message={(tokensQueryError).message || "Failed to load tokens."}>
                <div style={{ marginTop: "var(--space-2)" }}>
                  <Button variant="secondary" size="sm" onClick={() => void refetchTokens()}>
                    Retry
                  </Button>
                </div>
              </ErrorMessage>
            ) : (tokensData?.items ?? []).length === 0 ? (
              <EmptyState
                title="No active tokens"
                description="Generate a bearer token for automated testing pipelines or CLI."
              />
            ) : (
              <div className={styles.tableWrapper}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Token Name</th>
                      <th>Token Prefix</th>
                      <th>Created At</th>
                      <th>Expires</th>
                      <th>Last Used</th>
                      <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(tokensData?.items ?? []).map((t: ApiTokenItem) => (
                      <tr key={t.id}>
                        <td style={{ fontWeight: 600 }}>{t.name}</td>
                        <td>
                          <code style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
                            {t.token_prefix}...
                          </code>
                        </td>
                        <td>{new Date(t.created_at).toLocaleDateString()}</td>
                        <td>
                          {t.expires_at ? (
                            new Date(t.expires_at).toLocaleDateString()
                          ) : (
                            <span style={{ color: "var(--color-text-tertiary)" }}>Never</span>
                          )}
                        </td>
                        <td>
                          {t.last_used_at ? (
                            new Date(t.last_used_at).toLocaleDateString()
                          ) : (
                            <span style={{ color: "var(--color-text-tertiary)" }}>Never used</span>
                          )}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => {
                              if (window.confirm(`Revoke token '${t.name}'? Existing CI jobs will be denied.`)) {
                                revokeTokenMutation.mutate(t.id);
                              }
                            }}
                            disabled={revokeTokenMutation.isPending}
                          >
                            Revoke
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Quick Curl Guide */}
            <div className={styles.snippetBlock}>
              <div className={styles.snippetTitle}>How to use in CI/CD</div>
              <pre className={styles.snippetCode}>
{`# Authenticate automated pipelines using Authorization: Bearer header
curl -H "Authorization: Bearer <YOUR_API_TOKEN>" \\
     -X POST http://localhost:8090/api/v1/plans/<PLAN_ID>/runs \\
     -H "Content-Type: application/json" \\
     -d '{"environment_id": "<ENV_ID>", "plan_version": 1}'`}
              </pre>
            </div>
          </div>
        )}

        {/* ── TAB 3: AUDIT TRAIL ──────────────────────────────────────────────── */}
        {activeTab === "audit" && (
          <div className={styles.panelCard}>
            <div className={styles.panelHeader}>
              <div>
                <h2 className={styles.panelTitle}>Compliance & Audit Trail</h2>
                <p className={styles.panelDescription}>
                  Immutable, tamper-evident record of all security-sensitive operations across projects and runs.
                </p>
              </div>
              <div style={{ display: "flex", gap: "var(--space-3)", alignItems: "center" }}>
                <label htmlFor="audit-filter" className={styles.label} style={{ margin: 0 }}>
                  Filter:
                </label>
                <select
                  id="audit-filter"
                  className={styles.select}
                  value={selectedAuditType}
                  onChange={(e) => setSelectedAuditType(e.target.value)}
                >
                  <option value="">All Event Types</option>
                  <option value="user_login">user_login</option>
                  <option value="role_change">role_change</option>
                  <option value="project_created">project_created</option>
                  <option value="environment_created">environment_created</option>
                  <option value="plan_approved">plan_approved</option>
                  <option value="run_created">run_created</option>
                  <option value="run_cancelled">run_cancelled</option>
                  <option value="policy_blocked">policy_blocked</option>
                  <option value="artifact_accessed">artifact_accessed</option>
                </select>
              </div>
            </div>

            {!projectId ? (
              <EmptyState
                icon="📜"
                title="No Active Project Selected"
                description="Create or select a project from the top navigation to view compliance audit logs."
              />
            ) : isAuditLoading ? (
              <div style={{ display: "flex", justifyContent: "center", padding: "var(--space-8)" }}>
                <Spinner label="Loading audit logs..." />
              </div>
            ) : auditQueryError ? (
              <ErrorMessage message={(auditQueryError).message || "Failed to load audit records."}>
                <div style={{ marginTop: "var(--space-2)" }}>
                  <Button variant="secondary" size="sm" onClick={() => void refetchAudit()}>
                    Retry
                  </Button>
                </div>
              </ErrorMessage>
            ) : (auditData?.items ?? []).length === 0 ? (
              <EmptyState title="No audit events found" description="No events recorded for this criteria." />
            ) : (
              <div className={styles.tableWrapper}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Timestamp</th>
                      <th>Event Type</th>
                      <th>Actor ID</th>
                      <th>Resource Type</th>
                      <th>Redaction</th>
                      <th style={{ textAlign: "right" }}>Inspect</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(auditData?.items ?? []).map((e: AuditEventItem) => (
                      <tr key={e.id}>
                        <td>
                          {new Date(e.timestamp).toLocaleString([], {
                            month: "short",
                            day: "numeric",
                            hour: "2-digit",
                            minute: "2-digit",
                            second: "2-digit",
                          })}
                        </td>
                        <td>
                          <Badge variant={e.event_type.includes("failed") || e.event_type.includes("blocked") ? "failed" : "default"}>
                            {e.event_type}
                          </Badge>
                        </td>
                        <td>
                          <code style={{ fontSize: "var(--font-size-xs)" }}>
                            {e.actor_id ? e.actor_id.substring(0, 8) : "system"}
                          </code>
                        </td>
                        <td>{e.resource_type || "—"}</td>
                        <td>
                          {e.metadata_redacted ? (
                            <Badge variant="blocked">Redacted</Badge>
                          ) : (
                            <span style={{ color: "var(--color-text-tertiary)" }}>Clean</span>
                          )}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Button variant="secondary" size="sm" onClick={() => setInspectingEvent(e)}>
                            Inspect
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ── MODAL: ADD MEMBER ───────────────────────────────────────────────── */}
        {isAddMemberOpen && (
          <div className={styles.modalBackdrop}>
            <div className={styles.modal} role="dialog" aria-modal="true">
              <h2 className={styles.modalTitle}>Add Project Member</h2>
              {teamError && <ErrorMessage message={teamError} />}
              <form onSubmit={handleAddMemberSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                <div className={styles.formGroup}>
                  <label htmlFor="member-email" className={styles.label}>
                    User Email Address
                  </label>
                  <input
                    id="member-email"
                    type="email"
                    className={styles.input}
                    placeholder="colleague@example.com"
                    value={memberEmail}
                    onChange={(e) => setMemberEmail(e.target.value)}
                    required
                  />
                </div>
                <div className={styles.formGroup}>
                  <label htmlFor="member-role" className={styles.label}>
                    Permission Role
                  </label>
                  <select
                    id="member-role"
                    className={styles.select}
                    value={memberRole}
                    onChange={(e) => setMemberRole(e.target.value as "owner" | "maintainer" | "viewer")}
                  >
                    <option value="viewer">Viewer (Read-only)</option>
                    <option value="maintainer">Maintainer (Execute runs & edit plans)</option>
                    <option value="owner">Owner (Full administrative control)</option>
                  </select>
                </div>
                <div className={styles.modalActions}>
                  <Button variant="secondary" onClick={() => setIsAddMemberOpen(false)} type="button">
                    Cancel
                  </Button>
                  <Button variant="primary" type="submit" disabled={addMemberMutation.isPending}>
                    {addMemberMutation.isPending ? "Adding..." : "Add Member"}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* ── MODAL: GENERATE TOKEN ───────────────────────────────────────────── */}
        {isTokenModalOpen && (
          <div className={styles.modalBackdrop}>
            <div className={styles.modal} role="dialog" aria-modal="true">
              <h2 className={styles.modalTitle}>Generate Personal API Token</h2>
              {tokenError && <ErrorMessage message={tokenError} />}
              <form onSubmit={handleCreateTokenSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
                <div className={styles.formGroup}>
                  <label htmlFor="token-name" className={styles.label}>
                    Token Name / Description
                  </label>
                  <input
                    id="token-name"
                    type="text"
                    className={styles.input}
                    placeholder="e.g. GitHub Actions CI Runner"
                    value={tokenName}
                    onChange={(e) => setTokenName(e.target.value)}
                    required
                  />
                </div>
                <div className={styles.formGroup}>
                  <label htmlFor="token-expiry" className={styles.label}>
                    Expiration
                  </label>
                  <select
                    id="token-expiry"
                    className={styles.select}
                    value={tokenDays}
                    onChange={(e) => setTokenDays(e.target.value)}
                  >
                    <option value="30">30 days</option>
                    <option value="90">90 days</option>
                    <option value="365">1 year</option>
                    <option value="never">No expiration</option>
                  </select>
                </div>
                <div className={styles.modalActions}>
                  <Button variant="secondary" onClick={() => setIsTokenModalOpen(false)} type="button">
                    Cancel
                  </Button>
                  <Button variant="primary" type="submit" disabled={createTokenMutation.isPending}>
                    {createTokenMutation.isPending ? "Generating..." : "Generate Token"}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* ── MODAL: INSPECT AUDIT EVENT ──────────────────────────────────────── */}
        {inspectingEvent && (
          <div className={styles.modalBackdrop}>
            <div className={styles.modal} role="dialog" aria-modal="true" style={{ maxWidth: "680px" }}>
              <h2 className={styles.modalTitle}>Audit Event Inspection</h2>
              <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                <Badge variant="default">{inspectingEvent.event_type}</Badge>
                <span style={{ fontSize: "var(--font-size-xs)", color: "var(--color-text-secondary)" }}>
                  {inspectingEvent.id}
                </span>
              </div>
              <div className={styles.jsonViewer}>
                {JSON.stringify(inspectingEvent, null, 2)}
              </div>
              <div className={styles.modalActions}>
                <Button variant="secondary" onClick={() => setInspectingEvent(null)}>
                  Close
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
