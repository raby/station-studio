import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import type { Trace, TraceSlot } from '$lib/api';
import { applyEdit, type Edit } from '$lib/edit-ops';
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
    // No further: into Top of the hour would leave Music only its macro,
    // with no rule of its own to stand on, which the station refuses.
    await key(grip, 'ArrowUp');
    expect(status()).toBe('before Song 1 in Music');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 3, count: 1, to: 1, sectionId: 'music' });
    expect(status()).toBe('Moved Song 3 before Song 1 in Music.');
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
  });

  it('Home and End skip the places the station would refuse', async () => {
    const { onmove } = setup();
    const grip = grips()[3]; // the pick: Top of the hour's places are refused
    await key(grip, ' ');
    await key(grip, 'Home');
    expect(status()).toBe('before Song 1 in Music');
    await key(grip, 'End');
    expect(status()).toBe('back where it was');
    await key(grip, ' ');
    expect(onmove).not.toHaveBeenCalled();
  });

  it("a place the station would refuse isn't offered, and a drop there does nothing", async () => {
    const { onmove } = setup();
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    // The pick (Music's only single rule) onto Top of the hour's song:
    // Music would be left with just its macro.
    await fireEvent.dragStart(grips()[3], { dataTransfer });
    const top = screen.getAllByTestId('song-row')[0].closest('li')!;
    await fireEvent.dragOver(top, { dataTransfer, clientY: 0 });
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
    expect(dataTransfer.dropEffect).toBe('none');
    await fireEvent.drop(top, { dataTransfer });
    expect(onmove).not.toHaveBeenCalled();
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

  it('over the line between two sections it plays where it did, and only changes section', async () => {
    const { onmove } = setup();
    // The macro (Music's first) up one stop: the end of Top of the hour,
    // which is where it plays already.
    const grip = grips()[1];
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    expect(status()).toBe('at the end of Top of the hour');
    await key(grip, ' ');
    // `to` just after the run: the edit for "plays where it did".
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 1, count: 2, to: 3, sectionId: 'top' });
  });

  it('a selection in two sections: from where it is, it can join either, each offered once', async () => {
    const { onmove } = setup();
    const rows = screen.getAllByTestId('song-row');
    await fireEvent.click(rows[0]);                     // the jingle, Top of the hour
    await fireEvent.click(rows[1], { shiftKey: true }); // and the macro, Music
    const grip = grips()[0];
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    expect(status()).toBe('back where it was');
    const seen: string[] = [];
    for (let i = 0; i < 4; i++) {
      await key(grip, 'ArrowDown');
      seen.push(status());
    }
    expect(seen).toEqual([
      'before Song 0 in Top of the hour', // all of it into Top of the hour
      'before Song 1 in Music',           // all of it into Music
      'at the end of Music',
      'at the end of Music',
    ]);
    await key(grip, 'ArrowUp');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 0, count: 3, to: 3, sectionId: 'music' });
  });

  it("with a pointer, over a section's heading: the start of that section", async () => {
    const { onmove } = setup();
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    // The jingle (Top of the hour's only song) onto Music's heading:
    // first in Music, playing where it did.
    await fireEvent.dragStart(grips()[0], { dataTransfer });
    const heading = screen.getAllByTestId('song-section')[1];
    await fireEvent.dragOver(heading, { dataTransfer });
    expect(screen.getAllByTestId('song-drop-line')).toHaveLength(1);
    expect(dataTransfer.dropEffect).toBe('move');
    await fireEvent.drop(heading, { dataTransfer });
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 0, count: 1, to: 1, sectionId: 'music' });
    // Its own section's heading: where it is already, so nothing.
    onmove.mockClear();
    await fireEvent.dragStart(grips()[0], { dataTransfer });
    await fireEvent.dragOver(screen.getAllByTestId('song-section')[0], { dataTransfer });
    expect(screen.queryByTestId('song-drop-line')).toBeNull();
    expect(dataTransfer.dropEffect).toBe('none');
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

  it('off while searched, and when the page says so: the note says why', async () => {
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

describe('<SongList> moving songs, a station built from a repeating row', () => {
  // Two days (Mon, Tue) of three blocks: Morning has blocks 0-1, Evening
  // block 2, on both days.
  const rows: StationTemplate = {
    ...template,
    layout: {
      unit: 'day', unit_labels: ['Mon', 'Tue'], row_start_offsets: [0, 3],
      row_template: [
        { source: 'POP', strategy: 'pick', size: 1 },
        { source: 'POP', strategy: 'pick', size: 1 },
        { source: 'JINGLES', strategy: 'pick', size: 1 },
      ],
    },
  };
  const at = (r: number, b: number) => ({ kind: 'atomic' as const, row_idx: r, block_idx: b });
  const days: Sidecar = {
    schema_version: 1,
    sections: [
      { id: 'morning', name: 'Morning', blocks: [at(0, 0), at(0, 1), at(1, 0), at(1, 1)] },
      { id: 'evening', name: 'Evening', blocks: [at(0, 2), at(1, 2)] },
    ],
    macros: [],
    layout_order: ['morning', 'evening'],
  };
  const song = (row: number, block: number): TraceSlot => {
    const b = rows.layout.row_template![block];
    return {
      slot: (row - 1) * 3 + block + 1, unit: row === 1 ? 'Mon' : 'Tue', row, block: block + 1, source: b.source,
      strategy: b.strategy, pool_index: block, track_id: row * 10 + block, track_name: `${row === 1 ? 'Mon' : 'Tue'} ${block}`,
      track_artist: null, reason: 'picked',
    };
  };
  const twoDays = () => week([1, 2].flatMap((r) => [0, 1, 2].map((b) => song(r, b))));
  function setup() {
    const onmove = vi.fn();
    const r = render(SongList, { props: { trace: twoDays(), template: rows, sidecar: days, onmove } });
    return { onmove, ...r };
  }
  const grips = () => screen.getAllByTestId('song-grip');
  const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k });
  const status = () => screen.getByTestId('song-move-status').textContent;
  const carried = () => screen.getAllByTestId('song-row').map((r) => r.closest('li')!.classList.contains('carried'));

  it('grips on every song, and the note says a move is made in every day', () => {
    setup();
    expect(grips()).toHaveLength(6);
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('between. A move is made in every day');
    // Each day's first song says which day.
    expect(screen.getAllByTestId('song-unit').map((u) => u.textContent)).toEqual(['Mon', 'Tue']);
  });

  it("searched, the first song shown of each day says which", async () => {
    setup();
    await fireEvent.input(screen.getByTestId('song-search'), { target: { value: 'Tue 2' } });
    expect(screen.getAllByTestId('song-unit').map((u) => u.textContent)).toEqual(['Tue']);
  });

  it("from the keyboard, the places are the song's own day's; what moves is marked on every day", async () => {
    const { onmove } = setup();
    const grip = grips()[5]; // Tue's evening song: block 2
    await key(grip, ' ');
    expect(status()).toMatch(/^Picked up Tue 2\./);
    expect(carried()).toEqual([false, false, true, false, false, true]);
    // Up: the end of Morning that day (where it plays already), then
    // before Tue's second song.
    await key(grip, 'ArrowUp');
    expect(status()).toBe('at the end of Morning (Tue)');
    await key(grip, 'ArrowUp');
    expect(status()).toBe('before Tue 1 in Morning');
    await key(grip, 'Home');
    expect(status()).toBe('before Tue 0 in Morning');
    await key(grip, 'ArrowDown');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 2, count: 1, to: 1, sectionId: 'morning' });
  });

  it('after a move from the keyboard, focus goes to the song in the day it was moved from', async () => {
    const { rerender } = setup();
    const grip = grips()[5];
    grip.focus();
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    await key(grip, 'ArrowUp');
    await key(grip, ' ');
    await rerender({ trace: twoDays() });
    await waitFor(() => expect(document.activeElement).toBe(
      document.querySelector('[data-testid="song-grip"][data-rule="1"][data-row="2"]'),
    ));
  });

  it('a section moves in every day, from any day\'s heading', async () => {
    const { onmove, rerender } = setup();
    const evenings = screen.getAllByTestId('section-grip').filter((g) => g.getAttribute('data-section') === 'evening');
    expect(evenings).toHaveLength(2);
    evenings[1].focus();
    await key(evenings[1], ' ');
    // Tue's places only: one "before Morning", then back.
    await key(evenings[1], 'ArrowUp');
    await key(evenings[1], 'ArrowUp');
    expect(status()).toBe('before Morning');
    await key(evenings[1], 'ArrowDown');
    expect(status()).toBe('back where it was');
    await key(evenings[1], 'ArrowUp');
    await key(evenings[1], ' ');
    expect(onmove).toHaveBeenCalledWith({
      kind: 'moveSection', sectionId: 'evening', beforeSectionId: 'morning', from: 2, count: 1, to: 0,
    });
    // Focus goes to its heading in the day it was moved from.
    await rerender({ trace: twoDays() });
    await waitFor(() => expect(document.activeElement).toBe(
      document.querySelector('[data-testid="section-grip"][data-section="evening"][data-row="2"]'),
    ));
  });

  it("a section per day: the song's own day's sections, in order, and its own place among them", async () => {
    const perDay: Sidecar = {
      ...days,
      sections: [
        { id: 'mon-am', name: 'Mon AM', blocks: [at(0, 0), at(0, 1)] },
        { id: 'mon-pm', name: 'Mon PM', blocks: [at(0, 2)] },
        { id: 'tue-am', name: 'Tue AM', blocks: [at(1, 0), at(1, 1)] },
        { id: 'tue-pm', name: 'Tue PM', blocks: [at(1, 2)] },
      ],
      layout_order: ['mon-am', 'mon-pm', 'tue-am', 'tue-pm'],
    };
    const onmove = vi.fn();
    render(SongList, { props: { trace: twoDays(), template: rows, sidecar: perDay, onmove } });
    expect(screen.getAllByTestId('song-section').map((h) => h.textContent?.replace(/\s+/g, ' ').trim()))
      .toEqual(['⋮⋮ Mon AM 2 songs', '⋮⋮ Mon PM 1 song', '⋮⋮ Tue AM 2 songs', '⋮⋮ Tue PM 1 song']);
    const grip = grips()[5]; // Tue PM's song
    await key(grip, ' ');
    await key(grip, 'ArrowUp');
    expect(status()).toBe('at the end of Tue AM (Tue)');
    await key(grip, 'ArrowDown');
    expect(status()).toBe('back where it was');
    await key(grip, 'ArrowUp');
    await key(grip, ' ');
    // Into Tue AM, playing where it did (Mon's block stays in Mon PM).
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 2, count: 1, to: 3, sectionId: 'tue-am' });
  });

  it('a selection is of one day: shift-clicking into another says so', async () => {
    setup();
    const songs = screen.getAllByTestId('song-row');
    await fireEvent.click(songs[0]);
    await fireEvent.click(songs[4], { shiftKey: true });
    expect(document.querySelector('.in-selection')).toBeNull();
    expect(status()).toMatch(/^Select songs of one day/);
    await fireEvent.click(songs[0]);
    await fireEvent.click(songs[1], { shiftKey: true });
    // Blocks 0-1, marked on both days; counted in the one.
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('2 songs selected');
  });

  it("with a pointer, a line in the day it's over; the move is the same", async () => {
    const { onmove } = setup();
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    await fireEvent.dragStart(grips()[2], { dataTransfer }); // Mon's evening song
    const tueFirst = screen.getAllByTestId('song-row')[3].closest('li')!;
    await fireEvent.dragOver(tueFirst, { dataTransfer, clientY: 0 });
    expect(tueFirst.querySelector('[data-testid="song-drop-line"]')).not.toBeNull();
    await fireEvent.drop(tueFirst, { dataTransfer });
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 2, count: 1, to: 0, sectionId: 'morning' });
  });

  it('one section over every day: each day has its own end to move to', async () => {
    const all: Sidecar = {
      ...days,
      sections: [{ id: 'all', name: 'All', blocks: [0, 1].flatMap((r) => [0, 1, 2].map((b) => at(r, b))) }],
      layout_order: ['all'],
    };
    const onmove = vi.fn();
    render(SongList, { props: { trace: twoDays(), template: rows, sidecar: all, onmove } });
    const grip = grips()[0]; // Mon's first
    await key(grip, ' ');
    await key(grip, 'End');
    expect(status()).toBe('at the end of All (Mon)');
    // The line is at the bottom of Mon's last song (the list runs on
    // into Tue in the same section).
    const monLast = screen.getAllByTestId('song-row')[2].closest('li')!;
    expect(monLast.querySelector('.drop-line--after')).not.toBeNull();
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 0, count: 1, to: 3, sectionId: 'all' });
    // With a pointer, over the top of Tue's first song: before it (the
    // same rule on Mon, in the same list, doesn't count).
    const dataTransfer = { setData: vi.fn(), effectAllowed: '', dropEffect: '' };
    await fireEvent.dragStart(grips()[2], { dataTransfer });
    const tueFirst = screen.getAllByTestId('song-row')[3].closest('li')!;
    await fireEvent.dragOver(tueFirst, { dataTransfer, clientY: 0 });
    expect(tueFirst.querySelector('[data-testid="song-drop-line"]:not(.drop-line--after)')).not.toBeNull();
  });

  it('a section repeated for every day (as Rebirth Classics\' is): a block moves in every day, the section kept', async () => {
    const cycle = {
      ...days,
      sections: [{ id: 'cycle', name: 'Weekly cycle', repeat_with_offsets: [0, 3], blocks: [at(0, 0), at(0, 1), at(0, 2)] }],
      layout_order: ['cycle'],
    } as unknown as Sidecar;
    const onmove = vi.fn();
    render(SongList, { props: { trace: twoDays(), template: rows, sidecar: cycle, onmove } });
    expect(grips()).toHaveLength(6);
    expect(screen.getByTestId('song-move-note')).toHaveTextContent('A move is made in every day');
    // Every day's songs are the cycle's.
    expect(screen.getAllByTestId('song-section').map((h) => h.textContent)).toEqual([
      expect.stringContaining('Weekly cycle'),
    ]);
    const grip = grips()[5]; // Tue's last
    await key(grip, ' ');
    expect(carried()).toEqual([false, false, true, false, false, true]);
    await key(grip, 'Home');
    expect(status()).toBe('before Tue 0 in Weekly cycle');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 2, count: 1, to: 0, sectionId: 'cycle' });
  });

  it("a section per day with a macro in each: each day's songs under its own day; the macro moves whole", async () => {
    const perDay = {
      ...days,
      sections: [
        { id: 'mon', name: 'Monday', blocks: [at(0, 0), { kind: 'macro', ref: 'two' }] },
        { id: 'tue', name: 'Tuesday', blocks: [at(1, 0), { kind: 'macro', ref: 'two' }] },
      ],
      macros: [{ id: 'two', name: 'Two', repeat: 2, spec: { source: 'POP', strategy: 'pick', size: 1 } }],
      layout_order: ['mon', 'tue'],
    } as unknown as Sidecar;
    const onmove = vi.fn();
    render(SongList, { props: { trace: twoDays(), template: rows, sidecar: perDay, onmove } });
    expect(screen.getAllByTestId('song-section').map((h) => h.textContent))
      .toEqual([expect.stringContaining('Monday 3 songs'), expect.stringContaining('Tuesday 3 songs')]);
    const grip = grips()[4]; // Tue's first of the macro
    await key(grip, ' ');
    expect(status()).toMatch(/^Picked up 2 songs\./);
    expect(carried()).toEqual([false, true, true, false, true, true]);
    await key(grip, 'Home');
    await key(grip, ' ');
    expect(onmove).toHaveBeenCalledWith({ kind: 'moveRules', from: 1, count: 2, to: 0, sectionId: 'tue' });
  });
});

describe('<SongList> every place offered is a move the station takes', () => {
  // Random stations (as tests/test_rule_moves.py makes them): flat, some
  // sections with a macro; or 1-3 days, with sections per day or over
  // every day. From every grip, every place the keyboard offers, dropped:
  // the preview (the server's mirror) must take the edit.
  let seed = 5;
  const rand = (n: number): number => {
    seed = (seed + 0x6d2b79f5) | 0;
    let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return (((x ^ (x >>> 14)) >>> 0) / 4294967296 * n) | 0;
  };
  function station(rowsToo: boolean): { template: StationTemplate; sidecar: Sidecar; trace: Trace } {
    const n = 2 + rand(5);
    const rules = Array.from({ length: n }, (_, i) => ({ source: `r${i}`, strategy: 'pick' as const, size: 1 }));
    const cuts = [...new Set(Array.from({ length: rand(3) }, () => 1 + rand(n - 1)))].filter((c) => c < n).sort((a, b) => a - b);
    const spans = [0, ...cuts].map((a, k) => [a, [...cuts, n][k]] as const);
    const sections: Sidecar['sections'] = [];
    const macros: Sidecar['macros'] = [];
    const slots: TraceSlot[] = [];
    const songAt = (row: number, block: number, unit: string): TraceSlot => ({
      slot: slots.length + 1, unit, row, block: block + 1, source: `r${block}`, strategy: 'pick', pool_index: block,
      track_id: row * 100 + block, track_name: `S${row}-${block}`, track_artist: null, reason: 'r',
    });
    if (!rowsToo) {
      spans.forEach(([a, b], k) => {
        let blocks: Sidecar['sections'][number]['blocks'] = Array.from({ length: b - a }, (_, i) => ({ kind: 'atomic' as const, slot_index: a + i }));
        if (b - a >= 3 && rand(2)) {
          const width = 2 + rand(b - a - 2);
          macros.push({ id: `m${k}`, name: `M${k}`, repeat: width, spec: { source: 'r0', strategy: 'pick', size: 1 } });
          blocks = [blocks[0], { kind: 'macro', ref: `m${k}` }, ...blocks.slice(1 + width)];
        }
        sections.push({ id: `s${k}`, name: `S${k}`, blocks });
      });
      for (let b = 0; b < n; b++) slots.push(songAt(1, b, 'slot'));
      return {
        template: { ...template, layout: { unit: 'slot', unit_labels: [], row_start_offsets: [], flat_slots: rules } },
        sidecar: { schema_version: 1, sections, macros, layout_order: sections.map((s) => s.id) },
        trace: week(slots),
      };
    }
    const days = 1 + rand(3);
    const perDay = rand(2) === 1;
    const at = (r: number, b: number) => ({ kind: 'atomic' as const, row_idx: r, block_idx: b });
    if (perDay) {
      for (let r = 0; r < days; r++) spans.forEach(([a, b], k) => sections.push({ id: `d${r}s${k}`, name: `D${r}S${k}`, blocks: Array.from({ length: b - a }, (_, i) => at(r, a + i)) }));
    } else {
      spans.forEach(([a, b], k) => sections.push({ id: `s${k}`, name: `S${k}`, blocks: Array.from({ length: days }, (_, r) => Array.from({ length: b - a }, (_, i) => at(r, a + i))).flat() }));
    }
    for (let r = 1; r <= days; r++) for (let b = 0; b < n; b++) slots.push(songAt(r, b, `D${r}`));
    return {
      template: { ...template, layout: { unit: 'day', unit_labels: Array.from({ length: days }, (_, r) => `D${r + 1}`), row_start_offsets: Array(days).fill(0), row_template: rules } },
      sidecar: { schema_version: 1, sections, macros: [], layout_order: sections.map((s) => s.id) },
      trace: week(slots),
    };
  }

  const key = (el: HTMLElement, k: string) => fireEvent.keyDown(el, { key: k });
  const status = () => screen.getByTestId('song-move-status').textContent;

  it('flat and row stations, songs and sections, from the keyboard', async () => {
    let tried = 0;
    for (let i = 0; i < 40; i++) {
      const st = station(i % 2 === 1);
      const onmove = vi.fn();
      render(SongList, { props: { trace: st.trace, template: st.template, sidecar: st.sidecar, onmove } });
      const all = [...screen.queryAllByTestId('song-grip'), ...screen.queryAllByTestId('section-grip')];
      for (const grip of all) {
        // Count the places: down from its own, then up from its own.
        for (const dir of ['ArrowDown', 'ArrowUp']) {
          let before = '';
          for (let k = 1; k < 30; k++) {
            await key(grip, ' ');
            for (let j = 0; j < k; j++) await key(grip, dir);
            const said = status() ?? '';
            if (said === before || said === 'back where it was') {
              await key(grip, 'Escape'); // past the last place
              break;
            }
            before = said;
            await key(grip, ' ');
            const edit = onmove.mock.calls.at(-1)?.[0] as Edit | undefined;
            onmove.mockClear();
            expect(edit, said).toBeDefined();
            if (!edit) break;
            tried++;
            const out = applyEdit(st.template, st.sidecar, edit);
            expect(out.template === st.template && out.sidecar === st.sidecar, `${said}: ${JSON.stringify(edit)} on ${JSON.stringify(st.sidecar)}`).toBe(false);
          }
        }
      }
      cleanup();
    }
    expect(tried).toBeGreaterThan(200);
  }, 60000);
});
