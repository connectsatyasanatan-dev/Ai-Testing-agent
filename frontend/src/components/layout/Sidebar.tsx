import { Link, useLocation } from "react-router-dom";
import styles from "./Sidebar.module.css";

interface SidebarProps {
  currentProjectId?: string;
}

/* ── Inline SVG icons (no library dependency) ────────────────────── */
function DashboardIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1"/>
      <rect x="14" y="3" width="7" height="7" rx="1"/>
      <rect x="3" y="14" width="7" height="7" rx="1"/>
      <rect x="14" y="14" width="7" height="7" rx="1"/>
    </svg>
  );
}

function PlansIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="9" y1="13" x2="15" y2="13"/>
      <line x1="9" y1="17" x2="12" y2="17"/>
    </svg>
  );
}

function RunsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <polyline points="12 6 12 12 16 14"/>
    </svg>
  );
}

function EnvironmentsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
      <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
      <line x1="12" y1="22.08" x2="12" y2="12"/>
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
    </svg>
  );
}

export function Sidebar({ currentProjectId }: SidebarProps) {
  const location = useLocation();

  const base = currentProjectId ? `/projects/${currentProjectId}` : "";

  const links = [
    {
      to: currentProjectId ? `/projects/${currentProjectId}` : "/",
      label: "Dashboard",
      icon: <DashboardIcon />,
      isActive: (path: string) =>
        path === "/" ||
        path === `/projects/${currentProjectId}` ||
        path === `/projects/${currentProjectId}/`,
    },
    {
      to: `${base}/plans`,
      label: "Test Plans",
      icon: <PlansIcon />,
      isActive: (path: string) =>
        path.startsWith("/plans") || path.includes("/plans"),
    },
    {
      to: `${base}/runs`,
      label: "Test Runs",
      icon: <RunsIcon />,
      isActive: (path: string) =>
        path.startsWith("/runs") || path.includes("/runs"),
    },
    {
      to: `${base}/environments`,
      label: "Environments",
      icon: <EnvironmentsIcon />,
      isActive: (path: string) => path.includes("/environments"),
    },
    {
      to: `${base}/settings`,
      label: "Settings & Access",
      icon: <SettingsIcon />,
      isActive: (path: string) => path.includes("/settings"),
    },
  ];

  return (
    <nav className={styles.nav} aria-label="Main navigation">
      <div className={styles.section}>
        <div className={styles.sectionLabel}>Testing</div>
        {links.map(({ to, label, icon, isActive }) => {
          const active = isActive(location.pathname);
          return (
            <Link
              key={to}
              to={to}
              className={`${styles.navLink} ${active ? styles.active : ""}`}
              aria-current={active ? "page" : undefined}
            >
              <span className={styles.navIcon} aria-hidden="true">
                {icon}
              </span>
              <span className={styles.navLabel}>{label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
