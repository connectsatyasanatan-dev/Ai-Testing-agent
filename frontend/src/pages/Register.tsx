/**
 * Register.tsx — Enterprise SaaS Account Registration.
 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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

export default function Register() {
  const navigate = useNavigate();
  const { register, login, isRegistering } = useAuth();

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) return;

    if (password.length < 8) {
      setErrorMsg("Password must be at least 8 characters long.");
      return;
    }

    if (password !== confirmPassword) {
      setErrorMsg("Passwords do not match.");
      return;
    }

    setErrorMsg(null);
    try {
      await register({
        email: email.trim(),
        password,
        full_name: fullName.trim() || undefined,
      });
      // Automatically log in with new credentials
      await login({ email: email.trim(), password });
      navigate("/", { replace: true });
    } catch (err) {
      setErrorMsg(
        err instanceof Error
          ? err.message
          : "Registration failed. User may already exist."
      );
    }
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
            Start autonomous testing in <span className={styles.gradientText}>minutes</span>
          </h1>
          <p className={styles.heroDescription}>
            Join software engineering teams using UITP to verify APIs, execute end-to-end browser journeys, and safeguard their delivery pipeline.
          </p>

          <div className={styles.featureList}>
            <div className={styles.featureItem}>
              <div className={styles.featureIcon}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                  <polyline points="22 4 12 14.01 9 11.01" />
                </svg>
              </div>
              <div className={styles.featureText}>
                <span className={styles.featureTitle}>No Credit Card Required</span>
                <span className={styles.featureDesc}>
                  Deploy in your private infrastructure or cloud with complete tenant data isolation.
                </span>
              </div>
            </div>

            <div className={styles.featureItem}>
              <div className={styles.featureIcon}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                  <line x1="8" y1="21" x2="16" y2="21" />
                  <line x1="12" y1="17" x2="12" y2="21" />
                </svg>
              </div>
              <div className={styles.featureText}>
                <span className={styles.featureTitle}>Browser & API Adapters Built-in</span>
                <span className={styles.featureDesc}>
                  First-class declarative primitives with automated failure capture and retention lifecycle.
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className={styles.heroFooter}>
          <span>Universal AI Testing Platform</span>
          <span>•</span>
          <span>Open Standards</span>
        </div>
      </section>

      {/* ── Right Form ─────────────────────────────────────────────────────── */}
      <main className={styles.formSection}>
        <div className={styles.formCard}>
          <div className={styles.formHeader}>
            <h2 className={styles.formTitle}>Create your account</h2>
            <p className={styles.formSubtitle}>
              Get started with an isolated tenant organization workspace.
            </p>
          </div>

          {errorMsg && <ErrorMessage title="Registration Failed" message={errorMsg} />}

          <form onSubmit={(e) => { void handleSubmit(e); }} className={styles.form} noValidate>
            <div className={styles.formGroup}>
              <label htmlFor="register-name" className={styles.label}>
                Full name
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="register-name"
                  type="text"
                  autoFocus
                  placeholder="Jane Doe"
                  className={styles.input}
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                />
              </div>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="register-email" className={styles.label}>
                Work email
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="register-email"
                  type="email"
                  autoComplete="email"
                  required
                  placeholder="name@company.com"
                  className={styles.input}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>

            <div className={styles.formGroup}>
              <label htmlFor="register-password" className={styles.label}>
                Password (min 8 chars)
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="register-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
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

            <div className={styles.formGroup}>
              <label htmlFor="register-confirm" className={styles.label}>
                Confirm password
              </label>
              <div className={styles.inputWrapper}>
                <input
                  id="register-confirm"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  placeholder="••••••••••••"
                  className={styles.input}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </div>
            </div>

            <button
              type="submit"
              className={styles.submitBtn}
              disabled={isRegistering || !email.trim() || !password || !confirmPassword}
            >
              {isRegistering ? (
                <>
                  <Spinner size="sm" label="Creating account..." />
                  Creating account...
                </>
              ) : (
                "Create Account"
              )}
            </button>
          </form>

          <div className={styles.formFooter}>
            Already have an account?{" "}
            <Link to="/login" className={styles.link}>
              Sign in
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
