export type Grade = 'A' | 'B' | 'C' | 'D' | 'F' | '?';

export const GRADE_ORDER: Grade[] = ['A', 'B', 'C', 'D', 'F', '?'];

// Desaturated, dark-mode analytical ramp — muted emerald (A) → desaturated crimson (F).
// No neon; these read as functional health states on a #09090b surface.
const COLORS: Record<string, string> = {
  A: '#3d9a63', // muted emerald
  B: '#7aa552', // muted olive-green
  C: '#c2a34e', // muted amber
  D: '#c47a4a', // muted orange
  F: '#b24a45', // desaturated crimson
};

const GREY = '#64748b'; // slate — unmeasured, never on the A–F scale

// `?` means "not measured", not "bad" — never color it on the green→red scale.
export function gradeColor(g: string): string {
  return COLORS[g] ?? GREY;
}

export function gradeLabel(g: string): string {
  return g === '?' ? 'unknown' : g;
}
