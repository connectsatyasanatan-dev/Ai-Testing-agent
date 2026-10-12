import React from "react";
import styles from "./AppShell.module.css";

interface AppShellProps {
  header:  React.ReactNode;
  sidebar: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Top-level layout: sticky header + fixed-width sidebar + scrollable content.
 * Implemented in P4-01.
 */
export function AppShell({ header, sidebar, children }: AppShellProps) {
  return (
    <div className={styles.shell}>
      <header className={styles.header}>{header}</header>
      <nav className={styles.sidebar} aria-label="Main navigation">{sidebar}</nav>
      <main className={styles.main}>{children}</main>
    </div>
  );
}
