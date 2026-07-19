// "Marketing claim vs measured reality" rows for a host.
//
// The LEFT side is the GENERIC industry claim pattern (from README/METHODOLOGY),
// NOT a quote attributed to this specific provider — we don't store per-product ad
// copy and won't invent it. The RIGHT side is this host's REAL measured counter-metric.

import { fmtValue } from './hostcharts';

export interface DecoderRow {
  claim: string;    // typical marketing claim
  metric: string;   // what p99bench measures instead
  reality: string;  // this host's measured value
  grade?: string;   // grade of the backing metric, when graded
}

function n(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
function gradeOf(result: any, cat: string, key: string): string | undefined {
  return result?.grades?.categories?.[cat]?.metrics?.[key]?.grade;
}

export function decoderRows(result: any): DecoderRow[] {
  const d = result?.disk ?? {};
  const c = result?.cpu ?? {};
  const f = d.wal_fsync ?? {};
  const qd1 = d.rand_read_8k_qd1 ?? {};
  const ss = d.steady_state ?? {};
  const rows: (DecoderRow | null)[] = [
    n(f.p999_us) != null ? {
      claim: '"Up to N,000 IOPS"',
      metric: 'fsync p99.9 latency at QD1',
      reality: `${fmtValue(f.p999_us)} µs`,
      grade: gradeOf(result, 'disk', 'disk.wal_fsync.p999_us'),
    } : null,
    n(qd1.p50_us) != null && n(qd1.p999_us) != null ? {
      claim: '"NVMe SSD — low latency"',
      metric: 'p99.9 vs median, not the mean',
      reality: `p50 ${fmtValue(qd1.p50_us)} µs → p99.9 ${fmtValue(qd1.p999_us)} µs`,
      grade: gradeOf(result, 'disk', 'disk.rand_read_8k_qd1.p99_us'),
    } : null,
    n(c.steal_pct_under_load) != null ? {
      claim: '"Dedicated vCPUs"',
      metric: 'CPU steal measured under full load',
      reality: `${c.steal_pct_under_load}% steal`,
    } : null,
    n(c.single_thread_eps) != null ? {
      claim: '"High-performance cores"',
      metric: 'single-thread throughput',
      reality: `${fmtValue(c.single_thread_eps)} eps`,
      grade: gradeOf(result, 'cpu', 'cpu.single_thread_eps'),
    } : null,
    n(ss.degradation_pct) != null ? {
      claim: '"Fast" (short benchmark)',
      metric: 'IOPS drop over 30 min sustained load',
      reality: `${ss.degradation_pct}% degradation`,
    } : null,
    n(c.stall_p999_us) != null ? {
      claim: '"No noisy neighbours"',
      metric: 'scheduler stall p99.9',
      reality: `${fmtValue(c.stall_p999_us)} µs`,
      grade: gradeOf(result, 'cpu', 'cpu.stall_p999_us'),
    } : null,
  ];
  return rows.filter((r): r is DecoderRow => r !== null);
}
