/**
 * Badge — semantic status label for runs, plans, and steps.
 * Use statusVariant() to map raw strings to badge variants.
 */
import styles from "./Badge.module.css";

type BadgeVariant =
  | "passed" | "failed" | "blocked" | "inconclusive"
  | "running" | "cancelled" | "skipped" | "queued"
  | "draft" | "pending" | "approved" | "rejected" | "archived"
  | "default";

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
  /** Show animated dot prefix for live states */
  animated?: boolean;
}

export function Badge({ variant = "default", children, className = "", animated }: BadgeProps) {
  const cls = [
    styles.badge,
    styles[variant],
    animated ? styles.runningDot : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return <span className={cls}>{children}</span>;
}

/** Map a run/step status string to the correct Badge variant. */
// eslint-disable-next-line react-refresh/only-export-components
export function statusVariant(status: string): BadgeVariant {
  const map: Record<string, BadgeVariant> = {
    passed: "passed",
    failed: "failed",
    blocked: "blocked",
    inconclusive: "inconclusive",
    running: "running",
    cancelled: "cancelled",
    skipped: "skipped",
    queued: "queued",
    draft: "draft",
    pending_review: "pending",
    approved: "approved",
    rejected: "rejected",
    archived: "archived",
  };
  return map[status.toLowerCase()] ?? "default";
}

/** Human-readable label for run/plan statuses. */
// eslint-disable-next-line react-refresh/only-export-components
export function statusLabel(status: string): string {
  const map: Record<string, string> = {
    passed: "Passed",
    failed: "Failed",
    blocked: "Blocked",
    inconclusive: "Inconclusive",
    running: "Running",
    cancelled: "Cancelled",
    skipped: "Skipped",
    queued: "Queued",
    draft: "Draft",
    pending_review: "Pending Review",
    approved: "Approved",
    rejected: "Rejected",
    archived: "Archived",
  };
  return map[status.toLowerCase()] ?? status;
}
