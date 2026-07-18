import { describe, it, expect } from 'vitest';
import { median, fsyncSpread, MIN_RUNS_FOR_SPREAD } from './aggregate';

const mk = (p999: number) => ({ path: '', slug: { provider: 'x', region: 'r', stamp: 's' },
  data: { disk: { wal_fsync: { p999_us: p999 } } } });

describe('median', () => {
  it('odd length', () => expect(median([3, 1, 2])).toBe(2));
  it('even length averages middle two', () => expect(median([1, 2, 3, 4])).toBe(2.5));
  it('empty is null', () => expect(median([])).toBeNull());
});

describe('fsyncSpread', () => {
  it('hides spread below MIN_RUNS_FOR_SPREAD', () => {
    const runs = [mk(100), mk(200)];
    const s = fsyncSpread(runs as any);
    expect(s.showSpread).toBe(false);
    expect(s.worst).toBe(200);
  });
  it('shows spread at or above the threshold', () => {
    const runs = Array.from({ length: MIN_RUNS_FOR_SPREAD }, (_, i) => mk(100 * (i + 1)));
    const s = fsyncSpread(runs as any);
    expect(s.showSpread).toBe(true);
  });
  it('gates on non-null value count, not run count (matches tools/aggregate.py)', () => {
    // 3 runs, but one has no numeric p999_us (stage skipped/failed) -> only 2 usable values.
    const runs = [
      mk(100),
      mk(200),
      { path: '', slug: { provider: 'x', region: 'r', stamp: 's' }, data: { disk: { wal_fsync: {} } } },
    ];
    const s = fsyncSpread(runs as any);
    expect(s.showSpread).toBe(false);
    expect(s.worst).toBe(200);
  });
});
