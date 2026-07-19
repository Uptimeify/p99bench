// Row model for the core performance/price matrix. Built at build time from the
// full result files (per run), so every column is a MEASURED value — no modelled,
// predicted, or invented figures. Browser-safe: pure data + grades (no node:fs).

import { GRADE_ORDER } from './grades';
import type { ResultFile } from './data';

export interface MatrixRow {
  provider: string;
  region: string;
  product: string;
  stamp: string;
  price: number | null;
  billing: string | null;
  storageClass: string;
  fsyncWorst: number | null; // disk.wal_fsync.p999_us
  stall: number | null;      // cpu.stall_p999_us
  steal: number | null;      // cpu.steal_pct_under_load
  dedicated: boolean;        // measured no-overcommit: steal == 0
  cats: Record<string, string>;
  overall: string;           // worst of the four category grades (F beats ?)
  profiles: Record<string, string>;
  hostId: string | null;
  timestamp: string | null;
  submitter: string | null;
  notes: string | null;
  config: Record<string, unknown>;
}

function n(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function gradeRank(g: string): number {
  const i = GRADE_ORDER.indexOf(g as any);
  return i === -1 ? GRADE_ORDER.length : i;
}

// Worst-wins across category grades, honouring the doctrine: a real F dominates an
// unmeasured '?' (F beats ?). Mirrors tools/grade.py's rollup precedence.
export function worstGrade(grades: string[]): string {
  if (grades.length === 0) return '?';
  if (grades.includes('F')) return 'F';
  if (grades.includes('?')) return '?';
  return grades.reduce((w, g) => (gradeRank(g) > gradeRank(w) ? g : w), 'A');
}

export function buildMatrix(results: ResultFile[]): MatrixRow[] {
  return results.map((r) => {
    const d = r.data ?? {};
    const cat = d.grades?.categories ?? {};
    const cats: Record<string, string> = {
      disk: cat.disk?.grade ?? '?',
      cpu: cat.cpu?.grade ?? '?',
      ram: cat.ram?.grade ?? '?',
      network: cat.network?.grade ?? '?',
    };
    const profiles: Record<string, string> = {};
    for (const [k, v] of Object.entries<any>(d.grades?.profiles ?? {})) profiles[k] = v?.grade ?? '?';
    const steal = n(d.cpu?.steal_pct_under_load);
    return {
      provider: r.slug.provider,
      region: r.slug.region,
      product: d.provider?.product ?? r.slug.stamp,
      stamp: r.slug.stamp,
      price: n(d.provider?.price_eur_month),
      billing: d.provider?.billing ?? null,
      storageClass: d.grades?.storage_class ?? '—',
      fsyncWorst: n(d.disk?.wal_fsync?.p999_us),
      stall: n(d.cpu?.stall_p999_us),
      steal,
      dedicated: steal === 0,
      cats,
      overall: worstGrade(Object.values(cats)),
      profiles,
      hostId: d.run?.host_id ?? null,
      timestamp: d.run?.timestamp_utc ?? null,
      submitter: d.run?.submitter ?? null,
      notes: d.run?.notes ?? null,
      config: {
        product: d.provider?.product ?? null,
        price_eur_month: d.provider?.price_eur_month ?? null,
        billing: d.provider?.billing ?? null,
        storage_tier: d.provider?.storage_tier ?? null,
        storage_class: d.grades?.storage_class ?? null,
        disk_device_model: d.disk?.device_model ?? null,
        disk_scheduler: d.disk?.scheduler ?? null,
        disk_rotational: d.disk?.rotational ?? null,
        disk_target_fs: d.disk?.target_fs ?? null,
        disk_is_boot_volume: d.disk?.is_boot_volume ?? null,
        cpu_clock_idle_mhz: d.cpu?.clock_idle_mhz ?? null,
        cpu_clock_under_load_mhz: d.cpu?.clock_under_load_mhz ?? null,
        cpu_steal_pct_under_load: d.cpu?.steal_pct_under_load ?? null,
        cpu_scaling_efficiency: d.cpu?.scaling_efficiency ?? null,
        ram_populated_slots: d.ram?.populated_slots ?? null,
        ram_type: d.ram?.type ?? null,
        host_id: d.run?.host_id ?? null,
        tool_version: d.run?.tool_version ?? null,
        schema_version: d.schema_version ?? null,
      },
    };
  });
}

export interface MatrixFilter {
  provider?: string;
  storageClass?: string;
  profile?: string;
  minGrade?: string;
  maxPrice?: number;
  dedicatedOnly?: boolean;
}

function gradeFor(row: MatrixRow, profile?: string): string {
  return profile ? (row.profiles[profile] ?? '?') : row.overall;
}

export function filterMatrix(rows: MatrixRow[], f: MatrixFilter): MatrixRow[] {
  return rows.filter((row) => {
    if (f.provider && row.provider !== f.provider) return false;
    if (f.storageClass && row.storageClass !== f.storageClass) return false;
    if (f.maxPrice != null && (row.price ?? Infinity) > f.maxPrice) return false;
    if (f.dedicatedOnly && !row.dedicated) return false;
    if (f.minGrade) {
      const g = gradeFor(row, f.profile);
      if (g === '?') return false; // unknown never clears a minimum-grade bar
      if (gradeRank(g) > gradeRank(f.minGrade)) return false;
    }
    return true;
  });
}

export type MatrixSortKey =
  | 'provider' | 'price' | 'fsync' | 'stall' | 'steal' | 'overall' | 'profile';

export function sortMatrix(
  rows: MatrixRow[],
  key: MatrixSortKey,
  dir: 'asc' | 'desc',
  profile?: string,
): MatrixRow[] {
  const s = dir === 'asc' ? 1 : -1;
  const numCmp = (a: number | null, b: number | null) => (a ?? Infinity) - (b ?? Infinity);
  const cmp = (a: MatrixRow, b: MatrixRow): number => {
    switch (key) {
      case 'provider': return s * (`${a.provider}/${a.region}`).localeCompare(`${b.provider}/${b.region}`);
      case 'price': return s * numCmp(a.price, b.price);
      case 'fsync': return s * numCmp(a.fsyncWorst, b.fsyncWorst);
      case 'stall': return s * numCmp(a.stall, b.stall);
      case 'steal': return s * numCmp(a.steal, b.steal);
      case 'overall': return s * (gradeRank(a.overall) - gradeRank(b.overall));
      case 'profile': return s * (gradeRank(gradeFor(a, profile)) - gradeRank(gradeFor(b, profile)));
    }
  };
  return [...rows].sort(cmp);
}
