// Small pure formatters used across panels.

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
