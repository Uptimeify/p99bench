import { describe, it, expect } from 'vitest';
import { buildMatrix, worstGrade, filterMatrix, sortMatrix } from './matrix';

// A raw result file (per run).
function mkResult(over: any = {}) {
  return {
    path: '', slug: { provider: over.provider ?? 'ovh', region: over.region ?? 'zrh', stamp: over.stamp ?? 's1' },
    data: {
      provider: { product: over.product ?? 'vps', price_eur_month: over.price ?? 7.49, billing: 'monthly' },
      disk: { wal_fsync: { p999_us: over.fsync ?? 11206 }, scheduler: 'none' },
      cpu: { stall_p999_us: over.stall ?? 698, steal_pct_under_load: over.steal ?? 0 },
      run: { host_id: over.host ?? 'h1', timestamp_utc: '2026-07-17T00:00:00Z', submitter: 'x' },
    },
  };
}

// A merged index.json row (per provider/region/product).
function mkIndex(over: any = {}) {
  return {
    provider: over.provider ?? 'ovh', region: over.region ?? 'zrh', product: over.product ?? 'vps',
    storage_class: over.storage ?? 'net-slow', machines: 1, runs: over.runs ?? 1,
    fsync_p999_us_median: over.fsyncMedian ?? null, fsync_p999_us_worst: over.fsyncWorst ?? 11206,
    price_eur_month: over.price ?? 7.49,
    categories: { disk: over.disk ?? 'D', cpu: over.cpu ?? 'B', ram: over.ram ?? 'A', network: over.network ?? 'A' },
    categories_incomplete: {}, profiles: { postgres_oltp: over.oltp ?? 'D', redis_sentinel: 'D' },
    profiles_incomplete: {}, bound_by_counts: {},
  };
}

describe('worstGrade — F beats ? doctrine', () => {
  it('F dominates a ? on another axis', () => expect(worstGrade(['F', '?', 'B', 'B'])).toBe('F'));
  it('? when no F present', () => expect(worstGrade(['?', 'B', 'A'])).toBe('?'));
  it('worst by rank otherwise', () => expect(worstGrade(['A', 'C', 'B'])).toBe('C'));
});

describe('buildMatrix — merges same-machine runs like RESULTS', () => {
  it('collapses two runs of one product into a single row', () => {
    const index = [mkIndex({ provider: 'netcup', region: 'nue', product: 'RS', runs: 2, fsyncWorst: 1794, fsyncMedian: 1712 })];
    const results = [
      mkResult({ provider: 'netcup', region: 'nue', product: 'RS', stamp: 'a', fsync: 1630, stall: 97, steal: 0 }),
      mkResult({ provider: 'netcup', region: 'nue', product: 'RS', stamp: 'b', fsync: 1794, stall: 130, steal: 0.4 }),
    ];
    const rows = buildMatrix(index, results);
    expect(rows).toHaveLength(1);            // one merged row, not two
    expect(rows[0].runs).toBe(2);
    expect(rows[0].fsyncWorst).toBe(1794);   // from index (merged worst)
    expect(rows[0].fsyncMedian).toBe(1712);
    expect(rows[0].stall).toBe(130);         // worst stall across the two runs
    expect(rows[0].steal).toBe(0.4);         // worst steal across runs
    expect(rows[0].dedicated).toBe(false);   // one run had steal>0
    expect(rows[0].stamp).toBe('b');         // representative = worst-fsync run
  });

  it('produces one row per index entry', () => {
    const index = [mkIndex({ provider: 'a', product: 'p1' }), mkIndex({ provider: 'b', product: 'p2' })];
    const results = [mkResult({ provider: 'a', product: 'p1' }), mkResult({ provider: 'b', product: 'p2' })];
    expect(buildMatrix(index, results)).toHaveLength(2);
  });

  it('overall is worst category; config excludes the full raw file', () => {
    const rows = buildMatrix([mkIndex({ disk: 'F' })], [mkResult({})]);
    expect(rows[0].overall).toBe('F');
    expect(rows[0].config).toHaveProperty('disk_scheduler');
    expect(rows[0].config).not.toHaveProperty('grades');
  });
});

describe('filterMatrix / sortMatrix', () => {
  const rows = buildMatrix(
    [mkIndex({ provider: 'a', product: 'p1', oltp: 'B', price: 5 }),
     mkIndex({ provider: 'b', product: 'p2', oltp: 'D', price: 50 })],
    [mkResult({ provider: 'a', product: 'p1', steal: 0 }),
     mkResult({ provider: 'b', product: 'p2', steal: 3 })],
  );
  it('dedicatedOnly keeps only measured steal==0', () => {
    expect(filterMatrix(rows, { dedicatedOnly: true }).map((r) => r.provider)).toEqual(['a']);
  });
  it('minGrade on the selected profile', () => {
    expect(filterMatrix(rows, { profile: 'postgres_oltp', minGrade: 'B' }).map((r) => r.provider)).toEqual(['a']);
  });
  it('sorts by price, new array', () => {
    const out = sortMatrix(rows, 'price', 'asc');
    expect(out[0].price).toBe(5);
  });
});
