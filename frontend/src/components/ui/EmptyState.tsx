import React from "react";
import styles from "./EmptyState.module.css";

interface EmptyStateProps {
  /** SVG icon element */
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
  compact?: boolean;
}

function DefaultIcon() {
  return (
    <svg viewBox="0 0 48 48" fill="none" stroke="currentColor"
         strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true">
      <rect x="8" y="8" width="32" height="32" rx="4" strokeDasharray="4 2"/>
      <path d="M18 24h12M24 18v12" opacity="0.5"/>
    </svg>
  );
}

export function EmptyState({ icon, title, description, action, compact }: EmptyStateProps) {
  return (
    <div className={`${styles.empty} ${compact ? styles.compact : ""}`} role="status">
      <div className={styles.iconWrapper} aria-hidden="true">
        {icon ?? <DefaultIcon />}
      </div>
      <h3 className={styles.title}>{title}</h3>
      {description && <p className={styles.description}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
