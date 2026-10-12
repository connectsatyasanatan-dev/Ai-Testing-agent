import styles from "./ErrorMessage.module.css";

interface ErrorMessageProps {
  title?: string;
  message: string;
}

export function ErrorMessage({ title = "Something went wrong", message }: ErrorMessageProps) {
  return (
    <div className={styles.error} role="alert">
      <span className={styles.icon} aria-hidden="true">⚠</span>
      <div className={styles.body}>
        <p className={styles.title}>{title}</p>
        <p className={styles.detail}>{message}</p>
      </div>
    </div>
  );
}
