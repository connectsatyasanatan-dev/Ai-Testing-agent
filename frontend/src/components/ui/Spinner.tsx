import styles from "./Spinner.module.css";

type SpinnerSize = "sm" | "md" | "lg";

interface SpinnerProps {
  size?: SpinnerSize;
  label?: string;
  /** If true, renders centred with optional label — for full-page loading states. */
  fullPage?: boolean;
}

export function Spinner({ size = "md", label, fullPage = false }: SpinnerProps) {
  const inner = (
    <span
      className={[styles.spinner, styles[size]].join(" ")}
      role="status"
      aria-label={label ?? "Loading…"}
    />
  );

  if (fullPage) {
    return (
      <div className={styles.wrapper}>
        {inner}
        {label && <span>{label}</span>}
      </div>
    );
  }
  return inner;
}
