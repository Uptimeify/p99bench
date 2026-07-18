import type { IndexRow, ResultFile } from './data';
import { gradeColor, GRADE_ORDER } from './grades';

export function priceVsGrade(rows: IndexRow[], profile: string) {
  return rows
    .map((r) => ({ r, g: r.profiles[profile] ?? '?' }))
    .filter(({ g }) => g !== '?' && GRADE_ORDER.includes(g as any))
    .map(({ r, g }) => ({
      x: r.price_eur_month ?? 0,
      y: GRADE_ORDER.indexOf(g as any), // A=0 .. F=4
      label: `${r.provider}/${r.region}`,
      color: gradeColor(g),
    }));
}

export function fsyncBars(rows: IndexRow[]) {
  const pairs = rows
    .map((r) => ({ label: `${r.provider}/${r.region}`, v: r.fsync_p999_us_worst ?? Infinity }))
    .filter((p) => Number.isFinite(p.v))
    .sort((a, b) => a.v - b.v);
  return { labels: pairs.map((p) => p.label), values: pairs.map((p) => p.v) };
}

export function sustainedBars(runs: ResultFile[]) {
  const rows = runs
    .map((r) => ({
      label: `${r.slug.provider}/${r.slug.region}`,
      first: r.data?.disk?.steady_state?.first_min_iops,
      last: r.data?.disk?.steady_state?.last_min_iops,
    }))
    .filter((r) => typeof r.first === 'number' && typeof r.last === 'number');
  return {
    labels: rows.map((r) => r.label),
    firstMin: rows.map((r) => r.first as number),
    lastMin: rows.map((r) => r.last as number),
  };
}
