export type Grade = 'A' | 'B' | 'C' | 'D' | 'F' | '?';

export const GRADE_ORDER: Grade[] = ['A', 'B', 'C', 'D', 'F', '?'];

const COLORS: Record<string, string> = {
  A: '#1a9850', // green
  B: '#91cf60',
  C: '#fee08b', // amber
  D: '#fc8d59',
  F: '#d73027', // red
};

const GREY = '#8a8a8a';

// `?` means "not measured", not "bad" — never color it on the green→red scale.
export function gradeColor(g: string): string {
  return COLORS[g] ?? GREY;
}

export function gradeLabel(g: string): string {
  return g === '?' ? 'unknown' : g;
}
