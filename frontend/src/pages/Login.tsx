/**
 * Login.tsx — Enterprise SaaS Authentication Screen.
 */
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ErrorMessage } from "@/components/ui/ErrorMessage";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/hooks/useAuth";
import styles from "./Login.module.css";

function EyeIcon({ visible }: { visible: boolean }) {
  if (visible) {
    return (
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
        <line x1="1" y1="1" x2="23" y2="23" />
      </svg>
    );
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function CheckShieldIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <polyline points="9 12 11 14 15 10" />
    </svg>
  );
}

function TerminalIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <polyline points="4 17 10 11 4 5" />
      <line x1="12" y1="19" x2="20" y2="19" />
    </svg>
  );
}

function EyeLockIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login, isLoggingIn } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const state = location.state as { from?: { pathname?: string } } | null;
  const from = state?.from?.pathname || "/";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) return;

    setErrorMsg(null);
    try {
      await login({ email: email.trim(), password });
      navigate(from, { replace: true });
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "Invalid email or password. Please verify and try again."
      );
    }
  };

  const handleQuickFill = (demoEmail: string, demoPass: string) => {
    setEmail(demoEmail);
    setPassword(demoPass);
    setErrorMsg(null);
  };

  return (
    <div className={styles.pageWrapper}>
      {/* ── Left Hero ──────────────────────────────────────────────────────── */}
      <section className={styles.heroSection} aria-label="Platform introduction">
        <div className={styles.brand}>
          <div className={styles.brandMark}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          </div>
          <span className={styles.brandName}>UITP</span>
        </div>

        <div className={styles.heroContent}>
          <h1 className={styles.heroTitle}>
            Universal Autonomous <span className={styles.gradientText}>AI Testing Platform</span>
          </h1>
          <p className={styles.heroDescription}>
            The deterministic, security-first testing control plane for modern engineering teams.
            Safely execute web journeys, validate API schemas, and inspect telemetry with tamper-evident evidence.
          </p>

          <div className={styles.featureList}>
            <div className={styles.featureItem}>
              <div className={styles.featureIcon}>
                <TerminalIcon />
              </div>
              <div className={styles.featureText}>
                <span className={styles.featureTitle}>Dual Adapter Architecture</span>
                <span className={styles.featureDesc}>
                  Seamlessly test declarative HTTP APIs and headless Chromium browser journeys.
                </span>
              </div>
            </div>

            <div className={styles.featureItem}>
              <div className={styles.featureIcon}>
                <CheckShieldIcon />
              </div>
              <div className={styles.featureText}>
                <span className={styles.featureTitle}>SSRF & Rebinding Defenses</span>
                <span className={styles.featureDesc}>
                  Strict target allowlists, DNS IP-pinning, and isolated container execution boundaries.
                </span>
              </div>
            </div>

            <div className={styles.featureItem}>
              <div className={styles.featureIcon}>
                <EyeLockIcon />
              </div>
              <div className={styles.featureText}>
                <span className={styles.featureTitle}>Zero-Data-Leak Redaction</span>
                <span className={styles.featureDesc}>
                  Automated scrubbing of passwords, authorization tokens, and private artifacts.
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className={styles.heroFooter}>
          <span>Enterprise v0.1.0</span>
          <span>•</span>
          <span>SOC2 / ISO 27001 Ready</span>
          <span>•</span>
          <span>Immutable Audit Logging</span>
        </div>
      </section>

      {/* ── Right Form ─────────────────────────────────────────────────────── */}
      <main className={styles.formSection}>
        <div className={styles.formCard}>
          <div className={styles.formHeader}>
            <h2 className={styles.formTitle}>Sign in to your account</h2>
            <p className={styles.formSubtitle}>
              Enter your credentials to access your testing workspaces and plans.
            </p>
          </div>

          {errorMsg && <ErrorMessage title="Authentication Failed" message={errorMsg} />}

          <form onSubmit={(e) => { void handleSubmit(e); }} className={styles.form} noValidate>
            <div className={styles.formGroup}>
              <label htmlFor="login-email" className={styles.label}>
                Email address
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="login-email"
                  type="email"
                  autoComplete="email"
                  autoFocus
                  required
                  placeholder="name@company.com"
                  className={styles.input}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="login-password" className={styles.label}>
                Password
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  placeholder="••••••••••••"
                  className={styles.input}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className={styles.togglePassword}
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  <EyeIcon visible={showPassword} />
                </button>
              </div>
            </div>

            <div className={styles.optionsRow}>
              <label className={styles.rememberMe}>
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                />
                <span>Remember this session</span>
              </label>
            </div>

            <button
              type="submit"
              className={styles.submitBtn}
              disabled={isLoggingIn || !email.trim() || !password}
            >
              {isLoggingIn ? (
                <>
                  <Spinner size="sm" label="Signing in..." />
                  Signing in...
                </>
              ) : (
                "Sign In"
              )}
            </button>
          </form>

          {/* Quick fill demo accounts */}
          <div className={styles.demoSection}>
            <span className={styles.demoLabel}>Demo Accounts:</span>
            <div className={styles.demoPills}>
              <button
                type="button"
                className={styles.demoPill}
                onClick={() => handleQuickFill("admin@example.com", "AdminPassword123!")}
              >
                Admin
              </button>
              <button
                type="button"
                className={styles.demoPill}
                onClick={() => handleQuickFill("dev@example.com", "DevPassword123!")}
              >
                Developer
              </button>
            </div>
          </div>

          <div className={styles.formFooter}>
            Don't have an account?{" "}
            <Link to="/register" className={styles.link}>
              Create an account
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
