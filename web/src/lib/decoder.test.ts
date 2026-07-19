import { describe, it, expect } from 'vitest';
import { decoderRows } from './decoder';

const result = {
  disk: {
    wal_fsync: { p999_us: 4423.68 },
    rand_read_8k_qd1: { p50_us: 236, p999_us: 1056 },
    steady_state: { degradation_pct: 1.84 },
  },
  cpu: { steal_pct_under_load: 0, single_thread_eps: 332.9, stall_p999_us: 698 },
  grades: {
    categories: {
      disk: { metrics: { 'disk.wal_fsync.p999_us': { grade: 'C' }, 'disk.rand_read_8k_qd1.p99_us': { grade: 'A' } } },
      cpu: { metrics: { 'cpu.single_thread_eps': { grade: 'F' }, 'cpu.stall_p999_us': { grade: 'C' } } },
    },
  },
};

describe('decoderRows', () => {
  const rows = decoderRows(result);

  it('pairs a generic claim with the measured reality (fsync)', () => {
    const r = rows[0];
    expect(r.claim).toMatch(/IOPS/);
    expect(r.reality).toBe('4,424 µs');
    expect(r.grade).toBe('C');
  });

  it('surfaces measured steal for the "dedicated" claim', () => {
    const r = rows.find((x) => x.claim.includes('Dedicated'))!;
    expect(r.reality).toBe('0% steal');
  });

  it('carries the backing grade where the metric is graded', () => {
    expect(rows.find((x) => x.metric.includes('single-thread'))!.grade).toBe('F');
  });

  it('never attributes the claim to a specific provider (generic left side)', () => {
    for (const r of rows) expect(r.claim).not.toMatch(/hetzner|ovh|netcup|windcloud|tilaa|upcloud/i);
  });

  it('omits rows whose metric was not measured', () => {
    const bare = decoderRows({ disk: {}, cpu: {}, grades: { categories: {} } });
    expect(bare).toEqual([]);
  });
});
