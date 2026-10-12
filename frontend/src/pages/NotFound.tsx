import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import styles from "./NotFound.module.css";

export default function NotFound() {
  return (
    <div className={styles.page}>
      <h1 className={styles.code}>404</h1>
      <h2 className={styles.title}>Page not found</h2>
      <p className={styles.message}>The page you're looking for doesn't exist.</p>
      <Button asChild={<Link to="/" />} variant="primary">
        Go home
      </Button>
    </div>
  );
}
