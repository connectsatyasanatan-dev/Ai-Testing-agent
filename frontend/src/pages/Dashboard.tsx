/**
 * Dashboard — placeholder page.
 * Full implementation in P4-02 (project/environment management UI).
 */
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { useHealth } from "@/hooks/useHealth";
import styles from "./Dashboard.module.css";

export default function Dashboard() {
  const { data, isLoading, error } = useHealth();

  return (
    <div className={styles.page}>
      <h1 className={styles.heading}>Dashboard</h1>

      {isLoading && <Spinner fullPage label="Connecting to API…" />}

      {error && (
        <ErrorMessage
          title="Cannot reach the API"
          message={error.message}
        />
      )}

      {data && (
        <div className={styles.grid}>
          <Card>
            <CardHeader title="API Status" description="Control-plane health" />
            <CardBody>
              <p>Status: <strong>{data.status}</strong></p>
              <p>Version: <strong>{data.version}</strong></p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Projects" description="Your test projects" />
            <CardBody>
              <EmptyState
                icon="🗂"
                title="No projects yet"
                description="Projects are created in P4-02."
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Environments" description="Target test endpoints & allowlists" />
            <CardBody>
              <p style={{ marginBottom: "1rem", color: "var(--color-text-muted)" }}>
                Configure environment URLs, SSRF allowlists, and secret manager references.
              </p>
              <Link to="/environments">
                <Button variant="primary" size="sm">
                  Manage Environments →
                </Button>
              </Link>
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Recent Runs" description="Latest test executions" />
            <CardBody>
              <EmptyState
                icon="▶"
                title="No runs yet"
                description="Run history appears after P1 is complete."
              />
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}
