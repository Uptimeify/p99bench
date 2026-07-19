// Shapes a full result JSON into chart-ready bar groups for the host detail page.
// Pure + build-time; no DOM, no Node. The detail page renders these with BarChart.astro.
//
// A bar is one measured value. `grade` is set only when that exact metric is graded
// (grades.categories.<cat>.metrics.<dotted.key>.grade) — those bars are colored by the
// A-F palette; ungraded bars use a single sequential accent, never a rainbow.

export interface Bar {
  label: string;
  value: number | null;
  unit: string;
  grade?: string; // 'A'..'F'|'?' when this metric is graded
}

export interface BarGroup {
  title: string;
  note?: string;
  bars: Bar[];
}

export interface ChartSection {
  key: string; // disk | cpu | ram | network
  grade: string;
  boundBy?: string;
  groups: BarGroup[];
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

// grade of a graded metric key like "disk.wal_fsync.p999_us", or undefined.
function gradeOf(result: any, cat: string, metricKey: string): string | undefined {
  return result?.grades?.categories?.[cat]?.metrics?.[metricKey]?.grade;
}

function bar(label: string, value: unknown, unit: string, grade?: string): Bar {
  return { label, value: num(value), unit, grade };
}

// Drop groups whose every bar is null (metric absent on this host).
function live(groups: BarGroup[]): BarGroup[] {
  return groups
    .map((g) => ({ ...g, bars: g.bars.filter((b) => b.value !== null) }))
    .filter((g) => g.bars.length > 0);
}

function diskSection(result: any): ChartSection {
  const d = result.disk ?? {};
  const f = d.wal_fsync ?? {};
  const ss = d.steady_state ?? {};
  const groups = live([
    {
      title: 'WAL fsync latency ladder (µs, QD1)',
      note: 'Every COMMIT waits on one fdatasync. p99.9 is the graded metric — the tail that stalls a database.',
      bars: [
        bar('avg', f.avg_us, 'µs'),
        bar('p50', f.p50_us, 'µs'),
        bar('p99', f.p99_us, 'µs'),
        bar('p99.9', f.p999_us, 'µs', gradeOf(result, 'disk', 'disk.wal_fsync.p999_us')),
        bar('max', f.max_us, 'µs'),
      ],
    },
    {
      title: '8K random latency p99.9 by operation (µs)',
      bars: [
        bar('read QD1', d.rand_read_8k_qd1?.p999_us, 'µs'),
        bar('read', d.rand_read_8k?.p999_us, 'µs'),
        bar('write', d.rand_write_8k?.p999_us, 'µs'),
        bar('mixed', d.mixed_8k?.p999_us, 'µs'),
      ],
    },
    {
      title: 'Throughput',
      bars: [
        bar('seq read', d.seq_read?.bw_mbs, 'MB/s'),
        bar('seq write', d.seq_write?.bw_mbs, 'MB/s'),
      ],
    },
    {
      title: 'Random IOPS',
      bars: [
        bar('read 8K', d.rand_read_8k?.iops, 'IOPS'),
        bar('write 8K', d.rand_write_8k?.iops, 'IOPS'),
        bar('mixed 8K', d.mixed_8k?.iops, 'IOPS'),
        bar('fsync', f.iops, 'IOPS'),
      ],
    },
    {
      title: 'Sustained IOPS — first vs last minute of 30 min',
      note: ss.degradation_pct != null ? `${ss.degradation_pct}% degradation over ${Math.round((ss.duration_s ?? 0) / 60)} min` : undefined,
      bars: [
        bar('first min', ss.first_min_iops, 'IOPS'),
        bar('last min', ss.last_min_iops, 'IOPS'),
      ],
    },
  ]);
  return { key: 'disk', grade: result?.grades?.categories?.disk?.grade ?? '?', boundBy: result?.grades?.categories?.disk?.bound_by, groups };
}

function cpuSection(result: any): ChartSection {
  const c = result.cpu ?? {};
  const ss = c.steady_state ?? {};
  const groups = live([
    {
      title: 'Throughput (events/s)',
      bars: [
        bar('single', c.single_thread_eps, 'eps', gradeOf(result, 'cpu', 'cpu.single_thread_eps')),
        bar('multi', c.multi_thread_eps, 'eps'),
      ],
    },
    {
      title: 'Scheduler stall ladder (µs)',
      note: 'How long the kernel keeps a runnable thread waiting. p99.9 is graded.',
      bars: [
        bar('p99', c.stall_p99_us, 'µs'),
        bar('p99.9', c.stall_p999_us, 'µs', gradeOf(result, 'cpu', 'cpu.stall_p999_us')),
        bar('max', c.stall_max_us, 'µs'),
      ],
    },
    {
      title: 'Sustained throughput — first vs last minute',
      note: ss.degradation_pct != null ? `${ss.degradation_pct}% degradation · steal ${ss.steal_pct ?? 0}%` : undefined,
      bars: [
        bar('first min', ss.first_min_eps, 'eps'),
        bar('last min', ss.last_min_eps, 'eps'),
      ],
    },
  ]);
  return { key: 'cpu', grade: result?.grades?.categories?.cpu?.grade ?? '?', boundBy: result?.grades?.categories?.cpu?.bound_by, groups };
}

function ramSection(result: any): ChartSection {
  const r = result.ram ?? {};
  const groups = live([
    {
      title: 'Memory bandwidth (MB/s)',
      note: 'bw read is measured outside cache (the graded metric); seq/rnd are raw STREAM-style figures.',
      bars: [
        bar('bw read', r.bw_read_mbs, 'MB/s', gradeOf(result, 'ram', 'ram.bw_read_mbs')),
        bar('seq read', r.seq_read_mbs, 'MB/s'),
        bar('seq write', r.seq_write_mbs, 'MB/s'),
        bar('rnd read', r.rnd_read_mbs, 'MB/s'),
        bar('rnd write', r.rnd_write_mbs, 'MB/s'),
      ],
    },
  ]);
  return { key: 'ram', grade: result?.grades?.categories?.ram?.grade ?? '?', boundBy: result?.grades?.categories?.ram?.bound_by, groups };
}

function networkSection(result: any): ChartSection {
  const targets: any[] = Array.isArray(result?.network?.targets) ? result.network.targets : [];
  const reachable = targets.filter((t) => t && t.reachable);
  const groups = live([
    {
      title: 'Round-trip p99 by target (ms)',
      note: 'Same fixed target list from every host — distance is constant, so peering and neighbour noise surface.',
      bars: reachable.map((t) => bar(t.id ?? '?', t.rtt_p99_ms, 'ms')),
    },
    {
      title: 'Packet loss by target (%)',
      bars: reachable.map((t) => bar(t.id ?? '?', t.loss_pct, '%')),
    },
  ]);
  return { key: 'network', grade: result?.grades?.categories?.network?.grade ?? '?', boundBy: result?.grades?.categories?.network?.bound_by, groups };
}

export function hostChartSections(result: any): ChartSection[] {
  return [diskSection(result), cpuSection(result), ramSection(result), networkSection(result)]
    .filter((s) => s.groups.length > 0);
}

// Compact human number for a bar label: 12741.28 -> "12,741", 0.5 -> "0.5", 58657.04 -> "58,657".
export function fmtValue(v: number): string {
  const abs = Math.abs(v);
  const rounded = abs >= 100 ? Math.round(v) : abs >= 1 ? Math.round(v * 10) / 10 : Math.round(v * 100) / 100;
  return rounded.toLocaleString('en-US');
}
