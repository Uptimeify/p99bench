import type { ResultFile } from './data';

// Mirrors tools/aggregate.py: never report a spread we cannot support.
export const MIN_RUNS_FOR_SPREAD = 3;

export function median(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export interface Spread {
  median: number | null;
  worst: number | null;
  showSpread: boolean;
}

export function fsyncSpread(runs: ResultFile[]): Spread {
  const vals = runs
    .map((r) => r.data?.disk?.wal_fsync?.p999_us)
    .filter((v): v is number => typeof v === 'number');
  return {
    median: median(vals),
    worst: vals.length ? Math.max(...vals) : null,
    showSpread: runs.length >= MIN_RUNS_FOR_SPREAD,
  };
}
