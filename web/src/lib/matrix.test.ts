import { describe, it, expect } from 'vitest';
import { buildMatrix, worstGrade, filterMatrix, sortMatrix, type MatrixRow } from './matrix';

function mkResult(over: any = {}) {
  return {
    path: '', slug: { provider: over.provider ?? 'ovh', region: over.region ?? 'zrh', stamp: 's1' },
    data: {
      provider: { product: 'vps', price_eur_month: over.price ?? 7.49, billing: 'monthly' },
      disk: { wal_fsync: { p999_us: over.fsync ?? 11206 } },
      cpu: { stall_p999_us: over.stall ?? 698, steal_pct_under_load: over.steal ?? 0 },
      run: { host_id: 'h1', timestamp_utc: '2026-07-17T00:00:00Z', submitter: 'x' },
      grades: {
        storage_class: over.storage ?? 'net-slow',
        categories: {
          disk: { grade: over.disk ?? 'D' }, cpu: { grade: over.cpu ?? 'B' },
          ram: { grade: over.ram ?? 'A' }, network: { grade: over.network ?? 'A' },
        },
        profiles: { postgres_oltp: { grade: over.oltp ?? 'D' }, redis_sentinel: { grade: 'D' } },
      },
    },
  };
}

describe('worstGrade — F beats ? doctrine', () => {
  it('F dominates a ? on another axis', () => expect(worstGrade(['F', '?', 'B', 'B'])).toBe('F'));
  it('? when no F present', () => expect(worstGrade(['?', 'B', 'A'])).toBe('?'));
  it('worst by rank otherwise', () => expect(worstGrade(['A', 'C', 'B'])).toBe('C'));
});

describe('buildMatrix', () => {
  const rows = buildMatrix([mkResult({ steal: 0 }), mkResult({ provider: 'windcloud', steal: 4.2, disk: 'F' })] as any);
  it('pulls measured fsync/stall/steal', () => {
    expect(rows[0].fsyncWorst).toBe(11206);
    expect(rows[0].stall).toBe(698);
    expect(rows[0].steal).toBe(0);
  });
  it('dedicated is measured steal == 0', () => {
    expect(rows[0].dedicated).toBe(true);
    expect(rows[1].dedicated).toBe(false);
  });
  it('overall is worst category (F beats ?)', () => {
    expect(rows[1].overall).toBe('F');
  });
  it('carries a captured-config object, not the whole raw file', () => {
    expect(rows[0].config).toHaveProperty('disk_scheduler');
    expect(rows[0].config).not.toHaveProperty('grades');
  });
});

describe('filterMatrix', () => {
  const rows = buildMatrix([
    mkResult({ provider: 'a', steal: 0, oltp: 'B', price: 5 }),
    mkResult({ provider: 'b', steal: 3, oltp: 'D', price: 50 }),
  ] as any);
  it('dedicatedOnly keeps only measured steal==0', () => {
    expect(filterMatrix(rows, { dedicatedOnly: true }).map((r) => r.provider)).toEqual(['a']);
  });
  it('minGrade on the selected profile', () => {
    expect(filterMatrix(rows, { profile: 'postgres_oltp', minGrade: 'B' }).map((r) => r.provider)).toEqual(['a']);
  });
  it('maxPrice filters', () => {
    expect(filterMatrix(rows, { maxPrice: 10 })).toHaveLength(1);
  });
});

describe('sortMatrix', () => {
  const rows = buildMatrix([mkResult({ fsync: 500 }), mkResult({ fsync: 100 })] as any);
  it('sorts by fsync ascending, new array', () => {
    const out = sortMatrix(rows, 'fsync', 'asc');
    expect(out[0].fsyncWorst).toBe(100);
    expect(rows[0].fsyncWorst).toBe(500); // input unmutated
  });
});
