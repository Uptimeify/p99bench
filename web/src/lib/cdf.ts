// Tail-latency distribution for a host's fsync, expressed as CDF points.
// We store only measured percentiles (p50/p99/p99.9/max) — NOT a full histogram —
// so these are the *actual* CDF points; the line between them is drawn straight and
// labelled as interpolation. No invented density (that would be a PDF we can't back).

export interface CdfPoint {
  label: string;
  pct: number;   // cumulative probability at this latency (0.5, 0.99, 0.999, 1.0)
  lat: number;   // latency µs
  graded?: boolean;
}

export function fsyncCdf(result: any): CdfPoint[] {
  const f = result?.disk?.wal_fsync ?? {};
  const pts: CdfPoint[] = [
    { label: 'p50', pct: 0.5, lat: f.p50_us },
    { label: 'p99', pct: 0.99, lat: f.p99_us },
    { label: 'p99.9', pct: 0.999, lat: f.p999_us, graded: true },
    { label: 'max', pct: 1, lat: f.max_us },
  ];
  return pts.filter((p) => typeof p.lat === 'number' && p.lat > 0);
}

// Fixed axis domain so every host's chart is directly comparable across pages:
// a tight distribution sits left, an exploded tail sweeps right.
export const LAT_MIN = 200;      // µs
export const LAT_MAX = 500_000;  // µs (0.5 s)
export const Y_TAIL = 3.2;       // -log10(1-p) cap; max is pinned to the top

export function xFrac(lat: number): number {
  const lo = Math.log10(LAT_MIN);
  const hi = Math.log10(LAT_MAX);
  return Math.min(Math.max((Math.log10(lat) - lo) / (hi - lo), 0), 1);
}

// Tail-expanded vertical position: p50 low, p99/p99.9 stretched toward the top,
// max pinned to 1. Makes the tail legible instead of crushed against y=1.
export function yFrac(pct: number): number {
  if (pct >= 1) return 1;
  return Math.min(-Math.log10(1 - pct) / Y_TAIL, 1);
}

// x-axis decade ticks that fall inside the domain, with human labels.
export const X_TICKS = [
  { lat: 1_000, label: '1ms' },
  { lat: 10_000, label: '10ms' },
  { lat: 100_000, label: '100ms' },
];
export const Y_TICKS = [
  { pct: 0.5, label: '50' },
  { pct: 0.99, label: '99' },
  { pct: 0.999, label: '99.9' },
  { pct: 1, label: '100' },
];
