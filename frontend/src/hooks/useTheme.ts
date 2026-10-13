/**
 * useTheme — persisted dark/light theme toggle.
 * Stores preference in localStorage and applies [data-theme] on <html>.
 */
import { useEffect, useState } from "react";

type Theme = "dark" | "light";

const STORAGE_KEY = "uitp-theme";
const DEFAULT_THEME: Theme = "dark";

function getInitialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY) as Theme | null;
    if (stored === "dark" || stored === "light") return stored;
  } catch {
    /* storage unavailable */
  }
  return DEFAULT_THEME;
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* storage unavailable */
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleTheme = () =>
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));

  return { theme, toggleTheme };
}
