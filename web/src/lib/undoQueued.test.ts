import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Edit } from './edit-ops';
import type { Sidecar, StationTemplate } from './editor';

const { confirmDialogMock } = vi.hoisted(() => ({
  confirmDialogMock: vi.fn(async (_o: { title: string; body: string; confirmLabel?: string }) => true),
}));
vi.mock('$lib/confirm', () => ({ confirmDialog: confirmDialogMock }));

import { confirmUndo, movesRules } from './undoQueued';

const template = {
  station: 'ts', name: 'Test', sources: [{ name: 'A', colour: '#abc', role: 'content' }],
  layout: {
    unit: 'slot', unit_labels: [], row_start_offsets: [],
    flat_slots: [
      { source: 'A', strategy: 'weighted', size: 1 },
      { source: 'A', strategy: 'weighted', size: 1 },
      { source: 'A', strategy: 'weighted', size: 1 },
    ],
  },
} as unknown as StationTemplate;
const sidecar = {
  schema_version: 1,
  sections: [{ id: 'day', name: 'The day', blocks: [0, 1, 2].map((i) => ({ kind: 'atomic', slot_index: i })) }],
  macros: [],
  layout_order: ['day'],
} as unknown as Sidecar;
const remove = (slot: number): Edit => ({ kind: 'structuralBlockEdit', edit: { op: 'remove', slot_index: slot } });
const size = (slot: number, n: number): Edit => ({ kind: 'structuralBlockEdit', edit: { op: 'size', slot_index: slot, new_size: n } });

describe('confirmUndo', () => {
  beforeEach(() => {
    confirmDialogMock.mockReset();
    confirmDialogMock.mockResolvedValue(true);
  });

  it('nothing built on it: just that, without asking', async () => {
    expect(await confirmUndo([size(0, 2), size(1, 3)], template, sidecar, {}, [0])).toEqual([0]);
    expect(confirmDialogMock).not.toHaveBeenCalled();
  });

  it('nothing to undo: null', async () => {
    expect(await confirmUndo([size(0, 2)], template, sidecar, {}, [])).toBeNull();
  });

  it('later changes built on it: named, and asked about; yes takes them too', async () => {
    // Removing rule 1 makes rule 2 the first: both resizes are of rules
    // after it.
    const log = [remove(0), size(0, 3), size(1, 4)];
    expect(await confirmUndo(log, template, sidecar, {}, [0])).toEqual([0, 1, 2]);
    const asked = confirmDialogMock.mock.calls[0][0];
    expect(asked.title).toBe('Undo 2 later changes too?');
    expect(asked.body).toBe(
      'Undoing: Removed The day, rule 1: Shuffled from A, no artist twice in 10\n\n' +
      'These later changes build on it, and without it would change a different rule or fail to save:\n\n' +
      '• The day, rule 1: 3 songs\n• The day, rule 2: 4 songs',
    );
    expect(asked.confirmLabel).toBe('Undo all 3');
  });

  it('one later change: "Undo both"', async () => {
    await confirmUndo([remove(0), size(0, 3)], template, sidecar, {}, [0]);
    expect(confirmDialogMock.mock.calls[0][0]).toMatchObject({ title: 'Undo a later change too?', confirmLabel: 'Undo both' });
  });

  it('dropping a failed edit: the question says drop', async () => {
    await confirmUndo([remove(0), size(0, 3)], template, sidecar, {}, [0], { drop: true });
    const asked = confirmDialogMock.mock.calls[0][0];
    expect(asked).toMatchObject({ title: 'Drop a later change too?', confirmLabel: 'Drop both' });
    expect(asked.body).toMatch(/^Dropping: Removed The day, rule 1/);
  });

  it('told no: null, nothing undone', async () => {
    confirmDialogMock.mockResolvedValue(false);
    expect(await confirmUndo([remove(0), size(0, 3)], template, sidecar, {}, [0])).toBeNull();
  });

  it('part of a change of several edits goes: those edits are named, not the whole change', async () => {
    // Remove rule 2; then one change to the two rules left (identical),
    // so one line in the list but two edits. Only the edit to the rule
    // after the removed one builds on the remove.
    const patch = (slot: number): Edit => ({ kind: 'patchBlock', address: { slot_index: slot }, fields: { window: 3 } });
    const log = [remove(1), patch(0), patch(1)];
    expect(await confirmUndo(log, template, sidecar, {}, [0])).toEqual([0, 2]);
    const { body } = confirmDialogMock.mock.calls[0][0];
    expect(body).toMatch(/:\n\n• The day, rule 2: Shuffled from A, no artist twice in 3$/);
    expect(body).not.toContain('just like it');
  });
});

describe('movesRules', () => {
  it('a rule added or removed moves the rules after it; a size change doesn\'t', () => {
    const log = [size(0, 2), remove(1)];
    expect(movesRules(log, [0])).toBe(false);
    expect(movesRules(log, [0, 1])).toBe(true);
  });
});
