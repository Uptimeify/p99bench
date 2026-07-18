import { describe, it, expect } from 'vitest';
import { loadIndex, loadResults, providerRuns } from './data';

describe('loadIndex', () => {
  it('returns the results rows with the expected shape', () => {
    const rows = loadIndex();
    expect(rows.length).toBeGreaterThan(0);
    const r = rows[0];
    expect(r).toHaveProperty('provider');
    expect(r).toHaveProperty('categories.disk');
    expect(r).toHaveProperty('profiles.postgres_oltp');
    expect(r).toHaveProperty('price_eur_month');
  });
});

describe('loadResults', () => {
  it('loads every result file with a parsed slug', () => {
    const files = loadResults();
    expect(files.length).toBeGreaterThan(0);
    const f = files[0];
    expect(f.slug.provider).toBeTruthy();
    expect(f.slug.region).toBeTruthy();
    expect(f.slug.stamp).toBeTruthy();
    // full result carries embedded grades
    expect(f.data).toHaveProperty('grades.categories.disk.grade');
  });
});

describe('providerRuns', () => {
  it('returns only that provider files', () => {
    const files = loadResults();
    const someProvider = files[0].slug.provider;
    const runs = providerRuns(someProvider);
    expect(runs.length).toBeGreaterThan(0);
    expect(runs.every((r) => r.slug.provider === someProvider)).toBe(true);
  });
});
