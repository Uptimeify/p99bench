import { describe, it, expect } from 'vitest';
import { fsyncCdf, xFrac, yFrac, LAT_MAX } from './cdf';

const result = { disk: { wal_fsync: { p50_us: 864, p99_us: 1843, p999_us: 4423.68, max_us: 12741 } } };

describe('fsyncCdf', () => {
  it('emits the four measured percentile points, p99.9 graded', () => {
    const pts = fsyncCdf(result);
    expect(pts.map((p) => p.label)).toEqual(['p50', 'p99', 'p99.9', 'max']);
    expect(pts.find((p) => p.label === 'p99.9')!.graded).toBe(true);
    expect(pts[0].pct).toBe(0.5);
  });
  it('drops percentiles that were not measured', () => {
    const pts = fsyncCdf({ disk: { wal_fsync: { p50_us: 800, p999_us: null } } });
    expect(pts.map((p) => p.label)).toEqual(['p50']);
  });
  it('returns [] when fsync absent (no invented curve)', () => {
    expect(fsyncCdf({ disk: {} })).toEqual([]);
  });
});

describe('xFrac / yFrac', () => {
  it('x is monotonic increasing in latency and clamped to [0,1]', () => {
    expect(xFrac(100)).toBe(0); // below domain, clamped
    expect(xFrac(1000)).toBeGreaterThan(xFrac(500));
    expect(xFrac(LAT_MAX)).toBeCloseTo(1, 5);
  });
  it('y is monotonic and pins max (pct=1) to the top', () => {
    expect(yFrac(0.5)).toBeLessThan(yFrac(0.99));
    expect(yFrac(0.99)).toBeLessThan(yFrac(0.999));
    expect(yFrac(1)).toBe(1);
  });
});
