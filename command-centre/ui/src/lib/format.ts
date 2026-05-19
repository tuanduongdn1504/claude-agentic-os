// Small pure formatters used across panels.

// Timezone for all clock displays. Stored once so changing it is a one-liner.
const TZ = 'Asia/Ho_Chi_Minh'; // UTC+7

/**
 * Backend timestamps come in two flavours:
 *   - ISO with explicit zone:   `2026-05-15T07:01:35.411Z` / `…+00:00`
 *   - SQLite `datetime('now')`: `2026-05-15 16:35:36` (UTC, NO `Z` marker)
 *
 * The second form is parsed by JS as **local** time, which silently shifted
 * every "log clock" 7 hours off. Normalize to a UTC-marked ISO string so
 * `new Date(...)` always anchors to the correct moment.
 */
function toUtcDate(iso: string): Date {
  // Already explicit (has Z, or a +/- offset on the time portion).
  if (/Z$|[+-]\d\d:?\d\d$/.test(iso)) return new Date(iso);
  // SQLite-style "YYYY-MM-DD HH:MM:SS[.fff]" → treat as UTC.
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2}(?:\.\d+)?)$/);
  if (m) return new Date(`${m[1]}T${m[2]}Z`);
  // Date-only or anything else: fall back to native parse.
  return new Date(iso);
}

/** Format a UTC ISO string as HH:MM:SS in GMT+7. */
export function fmtTimeUTC7(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    return toUtcDate(iso).toLocaleTimeString('en-GB', { timeZone: TZ, hour12: false });
  } catch {
    return '—';
  }
}

/** Format a UTC ISO string as YYYY-MM-DD HH:MM:SS in GMT+7. */
export function fmtDateTimeUTC7(iso: string | null | undefined): string {
  if (!iso) return '—';
  try {
    const d = toUtcDate(iso);
    const date = d.toLocaleDateString('en-CA', { timeZone: TZ });
    const time = d.toLocaleTimeString('en-GB', { timeZone: TZ, hour12: false });
    return `${date} ${time}`;
  } catch {
    return '—';
  }
}

/** Format a UTC ISO string as YYYY-MM-DD date in GMT+7 (for grouping / comparison). */
export function localDateUTC7(iso: string | null | undefined): string {
  if (!iso) return 'unknown';
  try {
    return toUtcDate(iso).toLocaleDateString('en-CA', { timeZone: TZ });
  } catch {
    return 'unknown';
  }
}

export function fmtCount(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0)}M`;
}

export function fmtUsd(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n < 10) return `$${n.toFixed(2)}`;
  if (n < 1000) return `$${n.toFixed(1)}`;
  return `$${Math.round(n).toLocaleString()}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)}s`;
  return `${Math.floor(ms / 60_000)}m ${Math.floor((ms % 60_000) / 1000)}s`;
}

export function fmtAgeSeconds(s: number | null | undefined): string {
  if (s == null) return 'never';
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * "X minutes ago" from a server ISO string. Uses the same UTC normalization
 * as the clock formatters so SQLite-style naive timestamps don't drift 7h.
 */
export function fmtAgoFromIso(iso: string | null | undefined): string {
  if (!iso) return 'unknown';
  const t = toUtcDate(iso).getTime();
  if (Number.isNaN(t)) return iso;
  return fmtAgeSeconds((Date.now() - t) / 1000);
}

export function fmtPct(frac: number | null | undefined, digits = 1): string {
  if (frac == null) return '—';
  return `${(frac * 100).toFixed(digits)}%`;
}

export function fmtBytes(b: number | null | undefined): string {
  if (b == null) return '—';
  if (b < 1024) return `${b}B`;
  if (b < 1_048_576) return `${(b / 1024).toFixed(1)}KB`;
  if (b < 1_073_741_824) return `${(b / 1_048_576).toFixed(1)}MB`;
  return `${(b / 1_073_741_824).toFixed(2)}GB`;
}

export function cwdShort(cwd: string | null | undefined): string {
  if (!cwd) return '—';
  // Replace /Users/<name> with ~.
  return cwd.replace(/^\/Users\/[^/]+/, '~');
}
