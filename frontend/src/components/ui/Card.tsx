import React from "react";
import styles from "./Card.module.css";

interface CardProps { children: React.ReactNode; className?: string; }
interface CardHeaderProps { title: string; description?: string; className?: string; }
interface CardBodyProps   { children: React.ReactNode; className?: string; }
interface CardFooterProps { children: React.ReactNode; className?: string; }

export function Card({ children, className = "" }: CardProps) {
  return <div className={[styles.card, className].filter(Boolean).join(" ")}>{children}</div>;
}
export function CardHeader({ title, description, className = "" }: CardHeaderProps) {
  return (
    <div className={[styles.header, className].filter(Boolean).join(" ")}>
      <h3 className={styles.title}>{title}</h3>
      {description && <p className={styles.description}>{description}</p>}
    </div>
  );
}
export function CardBody({ children, className = "" }: CardBodyProps) {
  return <div className={[styles.body, className].filter(Boolean).join(" ")}>{children}</div>;
}
export function CardFooter({ children, className = "" }: CardFooterProps) {
  return <div className={[styles.footer, className].filter(Boolean).join(" ")}>{children}</div>;
}
