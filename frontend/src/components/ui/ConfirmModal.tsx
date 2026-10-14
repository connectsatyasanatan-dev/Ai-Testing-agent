import React, { useEffect, useRef } from "react";
import { Button } from "./Button";
import styles from "./ConfirmModal.module.css";

export interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning" | "primary";
  isLoading?: boolean;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

export function ConfirmModal({
  isOpen,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
  isLoading = false,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const iconCircleClass =
    variant === "danger"
      ? styles.iconCircleDanger
      : variant === "warning"
      ? styles.iconCircleWarning
      : styles.iconCirclePrimary;

  const iconSymbol =
    variant === "danger" ? "⚠️" : variant === "warning" ? "🔔" : "ℹ️";

  return (
    <div
      className={styles.overlay}
      onClick={(e) => {
        if (e.target === e.currentTarget && !isLoading) {
          onClose();
        }
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
    >
      <div className={styles.modalCard}>
        <div className={styles.modalHeader}>
          <div className={iconCircleClass} aria-hidden="true">
            {iconSymbol}
          </div>
          <div className={styles.headerContent}>
            <h3 id="confirm-modal-title" className={styles.title}>
              {title}
            </h3>
            <div className={styles.message}>{message}</div>
          </div>
        </div>

        <div className={styles.modalFooter}>
          <Button
            variant="secondary"
            size="sm"
            onClick={onClose}
            disabled={isLoading}
          >
            {cancelLabel}
          </Button>
          <Button
            ref={confirmBtnRef}
            variant={variant === "warning" ? "primary" : variant}
            size="sm"
            onClick={onConfirm}
            loading={isLoading}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
