import { GRADE_ORDER } from './grades';
import type { IndexRow } from './data';

export interface TableFilter {
  provider?: string;
  storage_class?: string;
  profile?: string;
  minGrade?: string;
  maxPrice?: number;
}

export type SortKey = 'provider' | 'price' | 'fsync' | 'profile';

function gradeRank(g: string): number {
  const i = GRADE_ORDER.indexOf(g as any);
  return i === -1 ? GRADE_ORDER.length : i;
}

// Worst category grade for a row (used when no profile is selected).
function worstCategory(r: IndexRow): string {
  return Object.values(r.categories).reduce(
    (worst, g) => (gradeRank(g) > gradeRank(worst) ? g : worst),
    'A',
  );
}

function gradeFor(r: IndexRow, profile?: string): string {
  return profile ? (r.profiles[profile] ?? '?') : worstCategory(r);
}

export function filterRows(rows: IndexRow[], f: TableFilter): IndexRow[] {
  return rows.filter((r) => {
    if (f.provider && r.provider !== f.provider) return false;
    if (f.storage_class && r.storage_class !== f.storage_class) return false;
    if (f.maxPrice != null && (r.price_eur_month ?? Infinity) > f.maxPrice) return false;
    if (f.minGrade) {
      const g = gradeFor(r, f.profile);
      // `?` is unknown, never passes a minimum-grade bar.
      if (g === '?') return false;
      if (gradeRank(g) > gradeRank(f.minGrade)) return false;
    }
    return true;
  });
}

export function sortRows(
  rows: IndexRow[],
  key: SortKey,
  dir: 'asc' | 'desc',
  profile?: string,
): IndexRow[] {
  const sign = dir === 'asc' ? 1 : -1;
  const cmp = (a: IndexRow, b: IndexRow): number => {
    switch (key) {
      case 'provider':
        return sign * a.provider.localeCompare(b.provider);
      case 'price':
        return sign * ((a.price_eur_month ?? Infinity) - (b.price_eur_month ?? Infinity));
      case 'fsync':
        return sign * ((a.fsync_p999_us_worst ?? Infinity) - (b.fsync_p999_us_worst ?? Infinity));
      case 'profile':
        // asc = best grade first (A before F)
        return sign * (gradeRank(gradeFor(a, profile)) - gradeRank(gradeFor(b, profile)));
    }
  };
  return [...rows].sort(cmp);
}
