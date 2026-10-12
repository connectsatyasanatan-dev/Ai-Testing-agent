/**
 * Badge — status label for run/step outcomes.
 */
import styles from "./Badge.module.css";

type BadgeVariant =
  | "passed" | "failed" | "blocked" | "inconclusive"
  | "running" | "cancelled" | "skipped" | "queued" | "default";

interface BadgeProps {
  variant?: BadgeVariant;
  children: React.ReactNode;
  className?: string;
}

export function Badge({ variant = "default", children, className = "" }: BadgeProps) {
  const cls = [styles.badge, styles[variant], className].filter(Boolean).join(" ");
  return <span className={cls}>{children}</span>;
}

/** Map a run/step status string to the correct Badge variant. */
export function statusVariant(status: string): BadgeVariant {
  const map: Record<string, BadgeVariant> = {
    passed: "passed", failed: "failed", blocked: "blocked",
    inconclusive: "inconclusive", running: "running",
    cancelled: "cancelled", skipped: "skipped", queued: "queued",
  };
  return map[status.toLowerCase()] ?? "default";
}
