/**
 * The editor's in-browser mirror of moving rules (`applyEdit` for
 * `moveRules` / `moveSection`) against the same cases the server is
 * checked against (`tests/fixtures/rule_moves.json`): the preview must
 * show what saving would do.
 */
import { describe, expect, it } from 'vitest';
import shared from '../../../tests/fixtures/rule_moves.json';
import { applyEdit, newPosition, type Edit } from './edit-ops';
import type { StationTemplate } from './editor';

interface Station { template: StationTemplate; sidecar: { sections: { id: string; blocks: { kind: string; slot_index?: number; ref?: string }[] }[]; layout_order: string[] } }
interface Case {
  name: string;
  station: string;
  edit: Edit;
  expect: { error: string } | { order: string[]; sections: Record<string, (number | string)[]>; layout_order: string[] };
}
const fixtures = shared as unknown as { stations: Record<string, Station>; cases: Case[] };

describe('moving rules: the shared cases', () => {
  it.each(fixtures.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const station = fixtures.stations[c.station];
    const before = JSON.stringify(station);
    const out = applyEdit(station.template, station.sidecar, c.edit);
    if ('error' in c.expect) {
      // Refused by the server, so the preview changes nothing.
      expect(out.template).toBe(station.template);
      expect(out.sidecar).toBe(station.sidecar);
    } else {
      const sidecar = out.sidecar as Station['sidecar'];
      expect(out.template.layout.flat_slots!.map((b) => b.source)).toEqual(c.expect.order);
      expect(Object.fromEntries(sidecar.sections.map((s) => [
        s.id, s.blocks.map((b) => (b.kind === 'atomic' ? b.slot_index! : `macro:${b.ref}`)),
      ]))).toEqual(c.expect.sections);
      expect(sidecar.layout_order).toEqual(c.expect.layout_order);
    }
    // Pure: what it was given is untouched.
    expect(JSON.stringify(station)).toBe(before);
  });

  it('every case is checked (none skipped by a typo in its kind)', () => {
    expect(fixtures.cases.length).toBeGreaterThan(15);
    for (const c of fixtures.cases) expect(['moveRules', 'moveSection']).toContain(c.edit.kind);
  });
});

describe('newPosition', () => {
  it('is a reordering: every slot lands somewhere, once', () => {
    for (let n = 1; n <= 6; n++) {
      for (let f = 0; f < n; f++) {
        for (let c = 1; f + c <= n; c++) {
          for (let t = 0; t <= n; t++) {
            if (t >= f && t <= f + c) continue;
            const image = Array.from({ length: n }, (_, p) => newPosition(p, f, c, t)).sort((a, b) => a - b);
            expect(image).toEqual(Array.from({ length: n }, (_, p) => p));
          }
        }
      }
    }
  });
});
