/**
 * Button — accessible, variant-driven button component.
 * Styles are in Button.module.css (manual CSS — no Tailwind).
 */
import React from "react";
import styles from "./Button.module.css";

type Variant = "primary" | "secondary" | "danger" | "ghost" | "outline";
type Size    = "sm" | "md" | "lg";

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?:  Variant;
  size?:     Size;
  loading?:  boolean;
  /** Render as any element while keeping button styles — pass e.g. `asChild={<a href="…" />}` */
  asChild?:  React.ReactElement<{ className?: string; children?: React.ReactNode }>;
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  asChild,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const cls = [styles.btn, styles[variant], styles[size], className].filter(Boolean).join(" ");

  const content = (
    <>
      {loading && <span className={styles.spinner} aria-hidden="true" />}
      {children}
    </>
  );

  if (asChild) {
    return React.cloneElement(asChild, {
      className: [cls, asChild.props.className].filter(Boolean).join(" "),
      children: content,
    });
  }

  return (
    <button className={cls} disabled={disabled || loading} {...rest}>
      {content}
    </button>
  );
}
