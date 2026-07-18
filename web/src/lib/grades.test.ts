import { describe, it, expect } from 'vitest';
import { gradeColor, gradeLabel, GRADE_ORDER } from './grades';

describe('gradeColor', () => {
  it('maps A..F to distinct non-grey colors', () => {
    const colors = ['A', 'B', 'C', 'D', 'F'].map(gradeColor);
    expect(new Set(colors).size).toBe(5);
  });
  it('maps ? to neutral grey', () => {
    expect(gradeColor('?')).toBe('#8a8a8a');
  });
  it('maps unknown input to grey', () => {
    expect(gradeColor('Z')).toBe('#8a8a8a');
  });
});

describe('gradeLabel', () => {
  it('labels ? as unknown', () => {
    expect(gradeLabel('?')).toBe('unknown');
  });
  it('passes A..F through', () => {
    expect(gradeLabel('A')).toBe('A');
  });
});

describe('GRADE_ORDER', () => {
  it('orders best to worst with ? last', () => {
    expect(GRADE_ORDER).toEqual(['A', 'B', 'C', 'D', 'F', '?']);
  });
});
