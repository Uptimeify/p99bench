import { describe, it, expect } from 'vitest';
import { filterRows, sortRows, type TableFilter } from './table';
import type { IndexRow } from './data';

function row(over: Partial<IndexRow>): IndexRow {
  return {
    provider: 'x', region: 'r', product: 'p', storage_class: 'net-fast',
    machines: 1, runs: 1, fsync_p999_us_median: null, fsync_p999_us_worst: 1000,
    price_eur_month: 10,
    categories: { disk: 'B', cpu: 'B', ram: 'B', network: 'B' },
    profiles: { postgres_oltp: 'B' },
    categories_incomplete: {}, profiles_incomplete: {}, bound_by_counts: {},
    ...over,
  };
}

describe('filterRows', () => {
  it('filters by provider', () => {
    const rows = [row({ provider: 'ovh' }), row({ provider: 'hetzner' })];
    expect(filterRows(rows, { provider: 'ovh' })).toHaveLength(1);
  });
  it('filters by maxPrice', () => {
    const rows = [row({ price_eur_month: 5 }), row({ price_eur_month: 50 })];
    expect(filterRows(rows, { maxPrice: 10 })).toHaveLength(1);
  });
  it('minGrade B keeps A and B, drops C', () => {
    const rows = [
      row({ profiles: { postgres_oltp: 'A' } }),
      row({ profiles: { postgres_oltp: 'B' } }),
      row({ profiles: { postgres_oltp: 'C' } }),
    ];
    const f: TableFilter = { profile: 'postgres_oltp', minGrade: 'B' };
    expect(filterRows(rows, f)).toHaveLength(2);
  });
  it('minGrade never passes ?', () => {
    const rows = [row({ profiles: { postgres_oltp: '?' } })];
    const f: TableFilter = { profile: 'postgres_oltp', minGrade: 'D' };
    expect(filterRows(rows, f)).toHaveLength(0);
  });

  it('F beats ? — a failed category keeps the row visible even under the loosest filter (no profile selected)', () => {
    const rows = [row({ categories: { disk: 'F', cpu: '?', ram: 'B', network: 'B' } })];
    const f: TableFilter = { minGrade: 'F' };
    expect(filterRows(rows, f)).toHaveLength(1);
  });

  it('all-? categories (no F) are still excluded as unknown, even at minGrade F', () => {
    const rows = [row({ categories: { disk: '?', cpu: '?', ram: '?', network: '?' } })];
    const f: TableFilter = { minGrade: 'F' };
    expect(filterRows(rows, f)).toHaveLength(0);
  });

  it('missing selected-profile key falls back to ? and is excluded by any minGrade', () => {
    const rows = [row({ profiles: { postgres_oltp: 'A' } })]; // no redis_sentinel key
    const f: TableFilter = { profile: 'redis_sentinel', minGrade: 'D' };
    expect(filterRows(rows, f)).toHaveLength(0);
  });

  it('null price is excluded by maxPrice', () => {
    const rows = [row({ price_eur_month: null }), row({ price_eur_month: 5 })];
    expect(filterRows(rows, { maxPrice: 10 })).toHaveLength(1);
  });
});

describe('sortRows', () => {
  it('sorts by price ascending', () => {
    const rows = [row({ price_eur_month: 50 }), row({ price_eur_month: 5 })];
    const out = sortRows(rows, 'price', 'asc');
    expect(out[0].price_eur_month).toBe(5);
  });
  it('sorts by profile grade best-first', () => {
    const rows = [
      row({ profiles: { postgres_oltp: 'C' } }),
      row({ profiles: { postgres_oltp: 'A' } }),
    ];
    const out = sortRows(rows, 'profile', 'asc', 'postgres_oltp');
    expect(out[0].profiles.postgres_oltp).toBe('A');
  });

  it('sorts null price last, ascending', () => {
    const rows = [
      row({ price_eur_month: null }),
      row({ price_eur_month: 5 }),
      row({ price_eur_month: 50 }),
    ];
    const out = sortRows(rows, 'price', 'asc');
    expect(out.map((r) => r.price_eur_month)).toEqual([5, 50, null]);
  });
});
