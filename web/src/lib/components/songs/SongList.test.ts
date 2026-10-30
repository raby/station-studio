import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import type { Trace, TraceSlot } from '$lib/api';
import type { Sidecar, StationTemplate } from '$lib/editor';
import SongList from './SongList.svelte';

// A jingle, then a two-song macro of picks and one more pick. (A section
// needs a single block to anchor on: all-macro sections can't be placed.)
const template: StationTemplate = {
  station: 'ts',
  name: 'Test',
  sources: [
    { name: 'JINGLES', colour: '#123456', role: 'content' },
    { name: 'POP', colour: '#abcdef', role: 'content' },
  ],
  layout: {
    unit: 'slot', unit_labels: [], row_start_offsets: [],
    flat_slots: [
      { source: 'JINGLES', strategy: 'jingle', size: 1, genre: 'Hour' },
      { source: 'POP', strategy: 'pick', size: 1 },
      { source: 'POP', strategy: 'pick', size: 1 },
      { source: 'POP', strategy: 'pick', size: 1 },
    ],
  },
};
const sidecar: Sidecar = {
  schema_version: 1,
  sections: [
    { id: 'top', name: 'Top of the hour', blocks: [{ kind: 'atomic', slot_index: 0 }] },
    { id: 'music', name: 'Music', blocks: [{ kind: 'macro', ref: 'pops' }, { kind: 'atomic', slot_index: 3 }] },
  ],
  macros: [{ id: 'pops', name: 'Two pops', repeat: 2, spec: { source: 'POP', strategy: 'pick', size: 1 } }],
  layout_order: ['top', 'music'],
};

function slot(i: number, over: Partial<TraceSlot> = {}): TraceSlot {
  const b = template.layout.flat_slots![i];
  return {
    // Numbered as the engine numbers traces: from 1.
    slot: i + 1, unit: 'slot', row: 1, block: i + 1, source: b.source, strategy: b.strategy, pool_index: i,
    track_id: 10 + i, track_name: `Song ${i}`, track_artist: i === 1 ? null : `Artist ${i}`,
    reason: `reason ${i}`, ...over,
  };
}
const week = (slots: TraceSlot[]): Trace => ({
  trace_id: 't', station: 'ts', week: 1, rng: 'r', seed: null, generated_at: '', slots,
});

describe('<SongList>', () => {
  it('shows each song with its rule and why, under section names', () => {
    render(SongList, { props: { trace: week([slot(0), slot(1), slot(2)]), template, sidecar } });
    expect(screen.getAllByTestId('song-section').map((h) => h.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
      'Top of the hour 1 song',
      'Music 2 songs',
    ]);
    const rules = screen.getAllByTestId('song-rule');
    expect(rules[0]).toHaveTextContent('Jingle from JINGLES: Hour');
    expect(rules[1]).toHaveTextContent('Picked from POP · Two pops');
    expect(screen.getAllByTestId('song-why')[2]).toHaveTextContent('Why: reason 2');
    expect(screen.queryByTestId('song-change')).toBeNull();
    // The second list carries on the numbering.
    expect(screen.getAllByRole('list')[1]).toHaveAttribute('start', '2');
  });

  it('marks changes against the saved week and notes removed songs', () => {
    const saved = week([slot(0), slot(1), slot(2)]);
    const edited = week([slot(0), slot(1, { track_id: 99, track_name: 'Swapped' })]);
    render(SongList, { props: { trace: edited, template, sidecar, saved } });
    const changes = screen.getAllByTestId('song-change');
    expect(changes).toHaveLength(1);
    expect(changes[0]).toHaveTextContent('changed');
    expect(screen.getByTestId('song-removed')).toHaveTextContent('1 song from the saved week is no longer in it.');
  });

  it('a click selects the song and hands its row to the page', async () => {
    const onselect = vi.fn();
    const { rerender } = render(SongList, { props: { trace: week([slot(0), slot(1)]), template, sidecar, onselect } });
    const rows = screen.getAllByTestId('song-row');
    await fireEvent.click(rows[1]);
    expect(onselect).toHaveBeenCalledWith(expect.objectContaining({ position: 2, macroName: 'Two pops' }));
    await rerender({ selected: 2 });
    expect(rows[1]).toHaveAttribute('aria-pressed', 'true');
    expect(rows[0]).toHaveAttribute('aria-pressed', 'false');
  });

  it("falls back to the trace's own rule when the block isn't in the template", () => {
    const stray = slot(0, { slot: 8, block: 8, strategy: 'window', source: 'POP' });
    render(SongList, { props: { trace: week([stray]), template, sidecar } });
    expect(screen.getByTestId('song-rule')).toHaveTextContent('window from POP');
    expect(screen.getByTestId('song-section')).toHaveTextContent('Not in a section');
  });

  it('shows the rule a song was actually picked with when the block has changed since', () => {
    const older = slot(1, { strategy: 'jingle', source: 'JINGLES' }); // block 1 is a POP pick now
    render(SongList, { props: { trace: week([slot(0), older]), template, sidecar } });
    const rules = screen.getAllByTestId('song-rule');
    expect(rules[1]).toHaveTextContent('jingle from JINGLES');
    expect(rules[1]).toHaveTextContent('rule changed since');
    expect(screen.getAllByTestId('song-rule-changed')).toHaveLength(1);
  });
});
