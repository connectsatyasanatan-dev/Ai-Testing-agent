import styles from "./ErrorMessage.module.css";

interface ErrorMessageProps {
  title?: string;
  message: string;
  children?: React.ReactNode;
}

export function ErrorMessage({ title = "Something went wrong", message, children }: ErrorMessageProps) {
  return (
    <div className={styles.error} role="alert">
      <span className={styles.icon} aria-hidden="true">⚠</span>
      <div className={styles.body}>
        <p className={styles.title}>{title}</p>
        <p className={styles.detail}>{message}</p>
        {children && <div style={{ marginTop: "0.5rem" }}>{children}</div>}
      </div>
    </div>
  );
}
