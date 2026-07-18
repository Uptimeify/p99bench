import { describe, it, expect } from 'vitest';
import { priceVsGrade, fsyncBars, sustainedBars } from './charts';

const row = (over: any) => ({
  provider: 'p', region: 'r', product: 'x', storage_class: 'net-fast',
  machines: 1, runs: 1, fsync_p999_us_median: null, fsync_p999_us_worst: over.fs ?? 1000,
  price_eur_month: over.price ?? 10,
  categories: {}, profiles: { postgres_oltp: over.grade ?? 'B' },
  categories_incomplete: {}, profiles_incomplete: {}, bound_by_counts: {},
});

describe('priceVsGrade', () => {
  it('maps grade to rank and skips ?', () => {
    const pts = priceVsGrade(
      [row({ grade: 'A', price: 5 }), row({ grade: '?', price: 9 })] as any,
      'postgres_oltp',
    );
    expect(pts).toHaveLength(1);
    expect(pts[0]).toMatchObject({ x: 5, y: 0 });
  });
});

describe('fsyncBars', () => {
  it('sorts ascending by worst fsync', () => {
    const out = fsyncBars([row({ fs: 500 }), row({ fs: 100 })] as any);
    expect(out.values).toEqual([100, 500]);
  });
});

describe('sustainedBars', () => {
  it('pulls first/last min iops', () => {
    const runs = [{ slug: { provider: 'p', region: 'r', stamp: 's' },
      data: { disk: { steady_state: { first_min_iops: 200, last_min_iops: 150 } } } }];
    const out = sustainedBars(runs as any);
    expect(out.firstMin).toEqual([200]);
    expect(out.lastMin).toEqual([150]);
  });
});
