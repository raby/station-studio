import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import type { Trace, TraceSlot } from '$lib/api';
import type { Edit } from '$lib/edit-ops';
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

  it('marks every song made by the related rule', () => {
    render(SongList, { props: { trace: week([slot(0), slot(1), slot(2)]), template, sidecar, related: 'macro:pops' } });
    const rows = screen.getAllByTestId('song-row');
    expect(rows.map((r) => r.classList.contains('related'))).toEqual([false, true, true]);
  });
});

describe('<SongList> moving songs', () => {
  // Top of the hour: the jingle (rule 0). Music: the two-pop macro
  // (rules 1-2), then a pick (rule 3).
  const four = () => week([slot(0), slot(1), slot(2), slot(3)]);
  function setup(props: Record<string, unknown> = {}) {
    const onmove = vi.fn();
    const r = render(SongList, { props: { trace: four(), template, sidecar, onmove, ...props } });
    return { onmove, ...r };
  }
  const grips = () => screen.getAllByTestId('song-grip');
  const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k });
  const status = () => screen.getByTestId('song-move-status').textContent;

  it("off unless the page can take moves: no grips, no note", () => {
    render(SongList, { props: { trace: four(), template, sidecar } });
    expect(screen.queryByTestId('song-grip')).toBeNull();
    expect(screen.queryByTestId('section-grip')).toBeNull();
    expect(screen.queryByTestId('song-move-note')).toBeNull();
  });

  it('a grip on every song, one on each section that plays together, and how to use them', () => {
    setup();
    expect(grips()).toHaveLength(4);
    expect(grips()[3]).toHaveAccessibleName('Move Song 3');
    expect(screen.getAllByTestId('section-grip').map((g) => g.getAttribute('aria-label')))
      .toEqual(['Move section Top of the hour', 'Move section Music']);
    expect(screen.getByTestId('song-move-note')).toHaveTextContent("Drag a song's grip");
  });

  it('from the keyboard: Space picks up, the arrows choose, Space drops; the move is the rule, to a place between rules', async () => {
    const { onmove } = setup();
    const grip = grips()[3];
    await key(grip, ' ');
    expect(status()).toMatch(/^Picked up Song 3\./);
    // What's carried is marked, so it's plain what's on the move.
    expect(screen.getAllByTestId('song-row').map((r) => r.closest('li')!.classList.contains('carried')))
      .toEqual([false, false, false, true]);
    // Its own place first; up: before the macro's first song, in Music.
    await key(grip, 'ArrowUp');
    expect(status()).toBe('before Song 1 in Music');
    expect(screen.getAllByTestId('song-drop-line')).toHaveLength(1);
    // Up again: the end of Top of the hour (the same place in play, the
    // other section).
    await key(grip, 'ArrowUp');
    expect(status()).toBe('at the end of Top of the hour');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 3, count: 1, to: 1, sectionId: 'top' });
    expect(status()).toBe('Moved Song 3 at the end of Top of the hour.');
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
  });

  it('Escape, or leaving the grip, puts it back; dropping where it was moves nothing', async () => {
    const { onmove } = setup();
    const grip = grips()[0];
    await key(grip, 'Enter');
    await key(grip, 'ArrowDown');
    await key(grip, 'Escape');
    expect(status()).toBe('Put back.');
    await key(grip, ' ');
    await key(grip, 'ArrowDown');
    await fireEvent.blur(grip);
    expect(status()).toBe('Put back.');
    await key(grip, ' ');
    await key(grip, ' ');
    expect(status()).toBe('Put back where it was.');
    expect(onmove).not.toHaveBeenCalled();
  });

  it("a macro's song moves all of the macro", async () => {
    const { onmove } = setup();
    const grip = grips()[2]; // the macro's second song
    await key(grip, ' ');
    await key(grip, 'End');
    expect(status()).toBe('at the end of Music');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 1, count: 2, to: 4, sectionId: 'music' });
  });

  it('shift-click selects the songs between, and the whole of their rules; their grips move them all', async () => {
    const { onmove } = setup();
    const rows = screen.getAllByTestId('song-row');
    await fireEvent.click(rows[2]);                     // the macro's second song
    await fireEvent.click(rows[3], { shiftKey: true }); // to the pick
    // The macro's first song comes too: a macro moves whole.
    expect(screen.getAllByTestId('song-row').map((r) => r.closest('li')!.classList.contains('in-selection')))
      .toEqual([false, true, true, true]);
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('3 songs selected');
    expect(grips()[1]).toHaveAccessibleName('Move 3 selected songs');
    await key(grips()[3], ' ');
    await key(grips()[3], 'Home');
    await key(grips()[3], ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 1, count: 3, to: 0, sectionId: 'top' });
    // Moved: the selection's gone.
    expect(document.querySelector('.in-selection')).toBeNull();
  });

  it('a new list (an Undo, say) clears a selection: its rules may be other songs now', async () => {
    const { rerender } = setup();
    const rows = screen.getAllByTestId('song-row');
    await fireEvent.click(rows[0]);
    await fireEvent.click(rows[1], { shiftKey: true });
    expect(document.querySelectorAll('.in-selection').length).toBeGreaterThan(0);
    await rerender({ trace: four() });
    expect(document.querySelector('.in-selection')).toBeNull();
  });

  it('Escape clears a selection', async () => {
    setup();
    const rows = screen.getAllByTestId('song-row');
    await fireEvent.click(rows[0]);
    await fireEvent.click(rows[1], { shiftKey: true });
    expect(document.querySelectorAll('.in-selection').length).toBeGreaterThan(0);
    await fireEvent.keyDown(screen.getByTestId('song-list'), { key: 'Escape' });
    expect(document.querySelector('.in-selection')).toBeNull();
  });

  it('a section moves before another section, or to the end', async () => {
    const { onmove } = setup();
    const music = screen.getAllByTestId('section-grip')[1];
    await key(music, ' ');
    await key(music, 'ArrowUp');
    expect(status()).toBe('before Top of the hour');
    await key(music, ' ');
    expect(onmove).toHaveBeenCalledWith({
      kind: 'moveSection', sectionId: 'music', beforeSectionId: 'top', from: 1, count: 3, to: 0,
    });
    const top = screen.getAllByTestId('section-grip')[0];
    await key(top, ' ');
    await key(top, 'ArrowDown');
    expect(status()).toBe('at the end');
    await key(top, ' ');
    expect(onmove).toHaveBeenLastCalledWith({
      kind: 'moveSection', sectionId: 'top', beforeSectionId: null, from: 0, count: 1, to: 4,
    });
  });

  it('with a pointer: drag a grip over a song, a line shows where, and the drop is the move', async () => {
    const { onmove } = setup();
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    await fireEvent.dragStart(grips()[0], { dataTransfer });
    const target = screen.getAllByTestId('song-row')[3].closest('li')!;
    // (No layout here: the pointer reads as the top of the song.)
    await fireEvent.dragOver(target, { dataTransfer, clientY: 0 });
    expect(target.querySelector('[data-testid="song-drop-line"]')).not.toBeNull();
    await fireEvent.drop(target, { dataTransfer });
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 0, count: 1, to: 3, sectionId: 'music' });
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
  });

  it('over its own place, no line, and a drop there moves nothing', async () => {
    const { onmove } = setup();
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    await fireEvent.dragStart(grips()[3], { dataTransfer });
    const own = screen.getAllByTestId('song-row')[3].closest('li')!;
    await fireEvent.dragOver(own, { dataTransfer, clientY: 0 });
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
    await fireEvent.drop(own, { dataTransfer });
    expect(onmove).not.toHaveBeenCalled();
  });

  it('off while searched, when the page says so, and on a station built from a repeating row: the note says why', async () => {
    const { rerender, onmove } = setup({ moveBlocked: 'Saving…' });
    // While the page waits the grips stay (one with focus keeps it),
    // but don't pick up: Space says why.
    expect(grips().every((g) => g.getAttribute('aria-disabled') === 'true' && !g.draggable)).toBe(true);
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('Saving…');
    await key(grips()[3], ' ');
    expect(status()).toBe('Saving…');
    await key(grips()[3], 'ArrowUp');
    await key(grips()[3], ' ');
    expect(onmove).not.toHaveBeenCalled();
    await rerender({ moveBlocked: null });
    expect(grips()[0]).toHaveAttribute('aria-disabled', 'false');
    await fireEvent.input(screen.getByTestId('song-search'), { target: { value: 'Song 1' } });
    expect(screen.queryByTestId('song-grip')).toBeNull();
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('Clear the search to move songs.');
    cleanup();
    const rows: StationTemplate = {
      ...template,
      layout: { unit: 'slot', unit_labels: [], row_start_offsets: [0], row_template: template.layout.flat_slots! },
    };
    render(SongList, { props: { trace: four(), template: rows, sidecar, onmove: vi.fn() } });
    expect(screen.getByTestId('song-move-note')).toHaveTextContent("can't be moved in a station built from a repeating row");
  });

  it('told to wait mid-carry: put back', async () => {
    const { rerender, onmove } = setup();
    await key(grips()[3], ' ');
    await key(grips()[3], 'ArrowUp');
    await rerender({ moveBlocked: 'Saving…' });
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
    await rerender({ moveBlocked: null });
    await key(grips()[3], ' ');
    expect(onmove).not.toHaveBeenCalled();
    expect(status()).toMatch(/^Picked up/);
  });

  it('after a move from the keyboard, focus goes with the song once the list shows the new order', async () => {
    const { rerender } = setup();
    const grip = grips()[3];
    grip.focus();
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    await key(grip, 'ArrowUp');
    await key(grip, ' ');
    // The page's preview catches up: rule 3 now plays second (to: 1).
    const t: StationTemplate = { ...template, layout: { ...template.layout, flat_slots: [0, 3, 1, 2].map((i) => template.layout.flat_slots![i]) } };
    const moved: Sidecar = {
      ...sidecar,
      sections: [
        { ...sidecar.sections[0], blocks: [{ kind: 'atomic', slot_index: 0 }, { kind: 'atomic', slot_index: 1 }] },
        { ...sidecar.sections[1], blocks: [{ kind: 'macro', ref: 'pops' }] },
      ],
    };
    await rerender({ trace: week([slot(0), { ...slot(3), block: 2 }, { ...slot(1), block: 3 }, { ...slot(2), block: 4 }]), template: t, sidecar: moved });
    await waitFor(() => expect(document.activeElement).toBe(grips()[1]));
    expect(grips()[1]).toHaveAccessibleName('Move Song 3');
  });

  it('a song moved with its rule says moved; ones only shifted along say nothing', () => {
    const saved = four();
    // The pick (rule 3) moved to the front of Music: 0 3 1 2.
    const opLog: Edit[] = [{ kind: 'moveRules', from: 3, count: 1, to: 1, sectionId: 'music' }];
    const moved: Sidecar = {
      ...sidecar,
      sections: [sidecar.sections[0], { ...sidecar.sections[1], blocks: [{ kind: 'atomic', slot_index: 1 }, { kind: 'macro', ref: 'pops' }] }],
    };
    const t: StationTemplate = { ...template, layout: { ...template.layout, flat_slots: [0, 3, 1, 2].map((i) => template.layout.flat_slots![i]) } };
    const edited = week([slot(0), { ...slot(3), block: 2 }, { ...slot(1), block: 3 }, { ...slot(2), block: 4 }]);
    render(SongList, { props: { trace: edited, template: t, sidecar: moved, saved, opLog } });
    expect(screen.getAllByTestId('song-change').map((c) => c.textContent)).toEqual(['moved']);
    expect(screen.getAllByTestId('song-row')[1]).toContainElement(screen.getByTestId('song-change'));
    expect(screen.queryByTestId('song-removed')).toBeNull();
  });
});
