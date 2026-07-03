import { describe, it, expect } from 'vitest';
import * as UI from '../components/ui/index.js';
import { MANIFEST } from './manifest.jsx';
import fs from 'node:fs';

const EXPORTS = Object.keys(UI).sort();
const NAMES = MANIFEST.map((e) => e.name).sort();

describe('design-system manifest coverage', () => {
  it('has exactly one entry per ui/ export (no missing, no orphan)', () => {
    expect(NAMES).toEqual(EXPORTS);
  });
  it('every entry has group, summary, props[], Specimen', () => {
    for (const e of MANIFEST) {
      expect(e.group && e.summary).toBeTruthy();
      expect(Array.isArray(e.props)).toBe(true);
      expect(typeof e.Specimen).toBe('function');
    }
  });
  it('DESIGN.md §14 lists every primitive', () => {
    const md = fs.readFileSync(new URL('../../DESIGN.md', import.meta.url), 'utf8');
    const s14 = md.slice(md.indexOf('## 14.'), md.indexOf('## 15.'));
    for (const n of NAMES) expect(s14).toContain('`' + n + '`');
  });
});
