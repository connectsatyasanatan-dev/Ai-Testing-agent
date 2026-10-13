import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CreateProjectModal } from "@/components/modals/CreateProjectModal";
import { useAuth } from "@/hooks/useAuth";
import { useHealth } from "@/hooks/useHealth";
import { useProjects } from "@/hooks/useProjects";
import { useTheme } from "@/hooks/useTheme";
import styles from "./Header.module.css";

interface HeaderProps {
  currentProjectId?: string;
  onSelectProject?: (projectId: string) => void;
}

/** Minimal SVG icon components — no icon library dependency. */
function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4"/>
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
    </svg>
  );
}

function LogoMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white"
         strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
    </svg>
  );
}

export function Header({ currentProjectId, onSelectProject }: HeaderProps) {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  const { data: health } = useHealth();
  const { data: projectsData } = useProjects();
  const { user, logout } = useAuth();
  const projects = projectsData?.items ?? [];

  const [isCreateProjectOpen, setIsCreateProjectOpen] = useState(false);

  const handleProjectChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const nextId = e.target.value;
    if (onSelectProject) {
      onSelectProject(nextId);
    } else {
      navigate(`/projects/${nextId}`);
    }
  };

  const handleSignOut = async () => {
    await logout();
    navigate("/login");
  };

  const apiOk = health?.status === "ok";

  return (
    <>
      <div className={styles.headerContainer}>
        {/* Brand */}
        <Link to="/" className={styles.brand} aria-label="UITP Home">
          <span className={styles.brandMark}>
            <LogoMark />
          </span>
          <span className={styles.brandWordmark}>UITP</span>
        </Link>

        <div className={styles.rightControls}>
          {/* Project selector */}
          <div className={styles.projectSelector}>
            {projects.length > 0 && (
              <>
                <label htmlFor="project-switcher" className={styles.projectLabel}>
                  Project
                </label>
                <select
                  id="project-switcher"
                  className={styles.projectSelect}
                  value={currentProjectId || (projects[0]?.id ?? "")}
                  onChange={handleProjectChange}
                  aria-label="Select active project"
                >
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </>
            )}
            <button
              type="button"
              className={styles.newProjectBtn}
              onClick={() => setIsCreateProjectOpen(true)}
              title="Create new project workspace"
            >
              + New Project
            </button>
          </div>

          <span className={styles.divider} aria-hidden="true" />

          {/* User Auth */}
          <div className={styles.userSection}>
            {user ? (
              <div className={styles.userBadge} title={`Logged in as ${user.email}`}>
                <span className={styles.userDot} aria-hidden="true" />
                <Link
                  to={currentProjectId ? `/projects/${currentProjectId}/settings` : "/settings"}
                  style={{ color: "inherit", textDecoration: "none", fontWeight: 600 }}
                  title="Project & Account Settings"
                >
                  {user.full_name || user.email.split("@")[0]}
                </Link>
                <button
                  type="button"
                  className={styles.signOutBtn}
                  onClick={() => void handleSignOut()}
                  title="Sign out"
                >
                  Sign out
                </button>
              </div>
            ) : (
              <button
                type="button"
                className={styles.signInBtn}
                onClick={() => navigate("/login")}
              >
                Sign In
              </button>
            )}
          </div>

          <span className={styles.divider} aria-hidden="true" />

          {/* System status */}
          <div
            className={styles.systemStatus}
            title={`API ${health?.version ?? "unknown"} · ${apiOk ? "Healthy" : "Degraded"}`}
          >
            <span
              className={`${styles.statusDot} ${!apiOk ? styles.statusDotDegraded : ""}`}
              aria-hidden="true"
            />
            {apiOk ? "API Ready" : "Connecting"}
          </div>

          {/* Theme toggle */}
          <button
            className={styles.iconBtn}
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </div>

      <CreateProjectModal
        isOpen={isCreateProjectOpen}
        onClose={() => setIsCreateProjectOpen(false)}
      />
    </>
  );
}
