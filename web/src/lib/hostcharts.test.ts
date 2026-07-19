import { describe, it, expect } from 'vitest';
import { hostChartSections, fmtValue } from './hostcharts';

// Minimal result fixture with the fields the shaper reads.
const result = {
  disk: {
    wal_fsync: { avg_us: 932, p50_us: 864, p99_us: 1843, p999_us: 4423.68, max_us: 12741, iops: 964 },
    rand_read_8k_qd1: { p999_us: 1056 },
    rand_read_8k: { p999_us: 8224, iops: 58657 },
    rand_write_8k: { p999_us: 30277, iops: 40882 },
    mixed_8k: { p999_us: 8847, iops: 36528 },
    seq_read: { bw_mbs: 5267 },
    seq_write: { bw_mbs: 1949 },
    steady_state: { first_min_iops: 51196, last_min_iops: 50254, degradation_pct: 1.84, duration_s: 1800 },
  },
  cpu: {
    single_thread_eps: 332.9,
    multi_thread_eps: 1227,
    stall_p99_us: 189, stall_p999_us: 698, stall_max_us: 11113,
    steady_state: { first_min_eps: 1323, last_min_eps: 1290, degradation_pct: 2, steal_pct: 0 },
  },
  ram: { bw_read_mbs: 37555, seq_read_mbs: 73261, seq_write_mbs: 44009, rnd_read_mbs: 7515, rnd_write_mbs: 685 },
  network: {
    targets: [
      { id: 'hetzner-fsn1', reachable: true, rtt_p99_ms: 2.2, loss_pct: 0 },
      { id: 'hetzner-ash', reachable: true, rtt_p99_ms: 95.3, loss_pct: 13 },
      { id: 'unreachable-one', reachable: false, rtt_p99_ms: null, loss_pct: null },
    ],
  },
  grades: {
    categories: {
      disk: { grade: 'C', bound_by: 'disk.wal_fsync.p999_us', metrics: { 'disk.wal_fsync.p999_us': { grade: 'C', value: 4423.68 } } },
      cpu: { grade: 'F', metrics: { 'cpu.single_thread_eps': { grade: 'F', value: 332.9 }, 'cpu.stall_p999_us': { grade: 'C', value: 698 } } },
      ram: { grade: 'B', metrics: { 'ram.bw_read_mbs': { grade: 'B', value: 37555 } } },
      network: { grade: 'A', metrics: {} },
    },
  },
};

describe('hostChartSections', () => {
  const sections = hostChartSections(result);

  it('produces the four category sections in order', () => {
    expect(sections.map((s) => s.key)).toEqual(['disk', 'cpu', 'ram', 'network']);
  });

  it('fsync ladder grades only the p99.9 bar', () => {
    const disk = sections.find((s) => s.key === 'disk')!;
    const ladder = disk.groups.find((g) => g.title.startsWith('WAL fsync'))!;
    const p999 = ladder.bars.find((b) => b.label === 'p99.9')!;
    const avg = ladder.bars.find((b) => b.label === 'avg')!;
    expect(p999.grade).toBe('C');
    expect(p999.value).toBe(4423.68);
    expect(avg.grade).toBeUndefined(); // ungraded percentile
  });

  it('single-thread eps carries its F grade', () => {
    const cpu = sections.find((s) => s.key === 'cpu')!;
    const tput = cpu.groups.find((g) => g.title.startsWith('Throughput'))!;
    expect(tput.bars.find((b) => b.label === 'single')!.grade).toBe('F');
  });

  it('network charts skip unreachable targets', () => {
    const net = sections.find((s) => s.key === 'network')!;
    const rtt = net.groups.find((g) => g.title.startsWith('Round-trip'))!;
    expect(rtt.bars.map((b) => b.label)).toEqual(['hetzner-fsn1', 'hetzner-ash']);
  });

  it('drops groups whose every value is null (no invented bars)', () => {
    const bare = hostChartSections({ disk: {}, cpu: {}, ram: {}, network: { targets: [] }, grades: { categories: {} } });
    expect(bare).toEqual([]);
  });
});

describe('fmtValue', () => {
  it('rounds large numbers and adds separators', () => {
    expect(fmtValue(12741.28)).toBe('12,741');
    expect(fmtValue(58657.04)).toBe('58,657');
  });
  it('keeps small values readable', () => {
    expect(fmtValue(0.5)).toBe('0.5');
    expect(fmtValue(2.22)).toBe('2.2');
  });
});
