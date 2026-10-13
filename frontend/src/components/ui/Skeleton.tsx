/**
 * Skeleton — content placeholder for loading states.
 * Use instead of spinners where layout is known.
 */
import styles from "./Skeleton.module.css";

interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  borderRadius?: string;
  className?: string;
}

export function Skeleton({
  width = "100%",
  height = "1em",
  borderRadius,
  className = "",
}: SkeletonProps) {
  return (
    <span
      className={`${styles.skeleton} ${className}`}
      style={{
        width: typeof width === "number" ? `${width}px` : width,
        height: typeof height === "number" ? `${height}px` : height,
        borderRadius,
      }}
      aria-hidden="true"
    />
  );
}

/** Pre-composed skeleton row for table/list loading states */
export function SkeletonRow({ cols = 4 }: { cols?: number }) {
  return (
    <tr className={styles.skeletonRow} aria-hidden="true">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i}>
          <Skeleton height="14px" width={i === 0 ? "60%" : i === cols - 1 ? "40%" : "80%"} />
        </td>
      ))}
    </tr>
  );
}

/** Pre-composed skeleton card */
export function SkeletonCard() {
  return (
    <div className={styles.skeletonCard} aria-hidden="true">
      <Skeleton height="12px" width="40%" />
      <Skeleton height="28px" width="60%" />
      <Skeleton height="12px" width="30%" />
    </div>
  );
}

