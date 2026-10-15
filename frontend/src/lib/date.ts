/**
 * Date and time formatting utilities for UTC timestamps from backend/SQLite.
 * Ensures timestamps lacking 'Z' are correctly interpreted as UTC and displayed
 * in the user's local timezone.
 */

export function parseUtcDate(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null;
  const trimmed = String(dateStr).trim();
  if (!trimmed) return null;

  const cleanStr = trimmed.includes(" ") && !trimmed.includes("T")
    ? trimmed.replace(" ", "T")
    : trimmed;
  const hasTz = /[Zz]$|[+-]\d{2}:?\d{2}$/.test(cleanStr);
  const isoStr = hasTz ? cleanStr : `${cleanStr}Z`;
  const d = new Date(isoStr);
  return isNaN(d.getTime()) ? new Date(dateStr) : d;
}

export function formatUtcDateTime(dateStr: string | null | undefined): string {
  const d = parseUtcDate(dateStr);
  if (!d) return "—";
  return d.toLocaleString();
}

export function formatUtcDateOnly(dateStr: string | null | undefined): string {
  const d = parseUtcDate(dateStr);
  if (!d) return "—";
  return d.toLocaleDateString();
}

export function formatUtcDuration(
  startedAt?: string | null,
  finishedAt?: string | null
): string {
  if (!startedAt || !finishedAt) return "—";
  try {
    const start = parseUtcDate(startedAt)?.getTime();
    const finish = parseUtcDate(finishedAt)?.getTime();
    if (!start || !finish) return "—";
    const diff = Math.max(0, Math.round((finish - start) / 1000));
    return `${diff}s`;
  } catch {
    return "—";
  }
}
