import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname, relative, sep } from 'node:path';

// this file: <root>/web/src/lib/data.ts -> repo root is three levels up.
const REPO_ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const RESULTS_DIR = join(REPO_ROOT, 'results');
const INDEX_JSON = join(REPO_ROOT, 'data', 'index.json');

export interface IndexRow {
  provider: string;
  region: string;
  product: string;
  storage_class: string;
  machines: number;
  runs: number;
  fsync_p999_us_median: number | null;
  fsync_p999_us_worst: number | null;
  price_eur_month: number | null;
  categories: Record<string, string>;
  profiles: Record<string, string>;
  categories_incomplete: Record<string, boolean>;
  profiles_incomplete: Record<string, boolean>;
  bound_by_counts: Record<string, number>;
}

export interface HostSlug {
  provider: string;
  region: string;
  stamp: string;
}

export interface ResultFile {
  path: string;
  slug: HostSlug;
  data: any;
}

export function loadIndex(): IndexRow[] {
  const parsed = JSON.parse(readFileSync(INDEX_JSON, 'utf8'));
  return parsed.results as IndexRow[];
}

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (name.endsWith('.json')) out.push(full);
  }
  return out;
}

export function loadResults(): ResultFile[] {
  return walk(RESULTS_DIR).map((path) => {
    // results/<provider>/<region>/<stamp>.json
    const rel = relative(RESULTS_DIR, path).split(sep);
    const [provider, region, file] = rel;
    const stamp = file.replace(/\.json$/, '');
    return {
      path,
      slug: { provider, region, stamp },
      data: JSON.parse(readFileSync(path, 'utf8')),
    };
  });
}

export function providerRuns(provider: string): ResultFile[] {
  return loadResults().filter((r) => r.slug.provider === provider);
}
