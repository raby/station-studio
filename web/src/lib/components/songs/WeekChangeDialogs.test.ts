import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

const { searchMock } = vi.hoisted(() => ({ searchMock: vi.fn() }));
vi.mock('$lib/api', async () => ({
  ...(await vi.importActual<typeof import('$lib/api')>('$lib/api')),
  searchStationSongs: (...args: unknown[]) => searchMock(...args),
}));

import ChangeScope from './ChangeScope.svelte';
import SwapSong from './SwapSong.svelte';
import WeekOnlyChanges from './WeekOnlyChanges.svelte';
import type { WeekChange } from '$lib/api';
import type { StationTemplate } from '$lib/editor';

beforeEach(() => {
  searchMock.mockReset();
  try { window.localStorage.clear(); } catch { /* */ }
});

describe('ChangeScope', () => {
  const props = () => ({
    song: 'song 4 (Tue): Song by Band', week: 12, weekOnlyBlocked: null as string | null,
    onchoose: vi.fn(), oncancel: vi.fn(),
  });

  it('offers what was chosen last time first, and remembers the choice', async () => {
    window.localStorage.setItem('changeScope', 'week');
    const p = props();
    render(ChangeScope, p);
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('change-scope-week')));
    expect(screen.getByTestId('change-scope-week')).toHaveTextContent('Just this song, week 12');
    await fireEvent.click(screen.getByTestId('change-scope-every'));
    expect(p.onchoose).toHaveBeenCalledWith('every');
    expect(window.localStorage.getItem('changeScope')).toBe('every');
  });

  it("a blocked choice says why, can't be taken, and isn't offered first", async () => {
    window.localStorage.setItem('changeScope', 'week');
    const p = { ...props(), weekOnlyBlocked: 'Regenerate it first.' };
    render(ChangeScope, p);
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('change-scope-every')));
    const week = screen.getByTestId('change-scope-week');
    expect(week).toHaveAttribute('aria-disabled', 'true');
    expect(week).toHaveAttribute('aria-describedby', 'change-scope-why');
    expect(screen.getByTestId('change-scope-why')).toHaveTextContent('Regenerate it first.');
    await fireEvent.click(week);
    expect(p.onchoose).not.toHaveBeenCalled();
    expect(window.localStorage.getItem('changeScope')).toBe('week'); // not changed by a refused click
  });

  it('Escape, Cancel or the backdrop cancel; Tab stays in the dialog', async () => {
    const p = props();
    render(ChangeScope, p);
    const dialog = screen.getByTestId('change-scope');
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('change-scope-every')));
    await fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(screen.getByTestId('change-scope-cancel'));
    await fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(screen.getByTestId('change-scope-every'));
    await fireEvent.keyDown(dialog, { key: 'Escape' });
    await fireEvent.click(screen.getByTestId('change-scope-cancel'));
    await fireEvent.click(screen.getByTestId('change-scope-backdrop'));
    expect(p.oncancel).toHaveBeenCalledTimes(3);
    await fireEvent.click(dialog); // inside: not a cancel
    expect(p.oncancel).toHaveBeenCalledTimes(3);
  });
});

describe('SwapSong', () => {
  const songs = [
    { id: 1, name: 'Rule Song', artist: 'R', album: null, pools: ['A'] },
    { id: 2, name: 'Playing Now', artist: 'P', album: null, pools: ['A'] },
    { id: 3, name: 'Another', artist: null, album: null, pools: ['A', 'B'] },
  ];
  const props = () => ({
    slug: 'cls', song: 'song 4: Playing Now', pool: 'A', currentTrackId: 2, ruleTrackId: 1,
    inWeek: new Map([[3, [5, 9, 11, 20]]]), onpick: vi.fn(), oncancel: vi.fn(), debounceMs: 0,
  });

  it("lists the rule's pool first, without the rule's own song; marks repeats and the song playing now", async () => {
    searchMock.mockResolvedValue({ songs, more: false });
    const p = props();
    render(SwapSong, p);
    expect(searchMock).toHaveBeenCalledWith('cls', '', 'A');
    const results = await screen.findAllByTestId('swap-song-result');
    expect(results).toHaveLength(2);
    expect(results[0]).toHaveTextContent('Playing Now');
    expect(results[0]).toHaveTextContent('playing here now');
    expect(results[0]).toBeDisabled();
    expect(results[1]).toHaveTextContent('A, B');
    expect(results[1]).toHaveTextContent('In this week already: song 5, 9, 11 and 1 more');
    expect(screen.getByTestId('swap-song-count')).toHaveTextContent('2 songs');
    await fireEvent.click(results[1]);
    expect(p.onpick).toHaveBeenCalledWith(songs[2]);
    expect(document.activeElement).toBe(screen.getByTestId('swap-song-search'));
  });

  it('searches as you type; only the latest answer counts; says when there are more', async () => {
    let answer!: (v: unknown) => void;
    searchMock.mockResolvedValueOnce({ songs: [], more: false });
    searchMock.mockImplementationOnce(() => new Promise((r) => (answer = r)));
    searchMock.mockResolvedValueOnce({ songs: [songs[2]], more: true });
    render(SwapSong, props());
    await vi.waitFor(() => expect(screen.getByTestId('swap-song-count')).toHaveTextContent('No songs in A.'));
    const input = screen.getByTestId('swap-song-search');
    await fireEvent.input(input, { target: { value: 'an' } });
    await vi.waitFor(() => expect(searchMock).toHaveBeenCalledTimes(2));
    await fireEvent.input(input, { target: { value: 'ano' } });
    await vi.waitFor(() => expect(searchMock).toHaveBeenCalledTimes(3));
    expect(searchMock).toHaveBeenLastCalledWith('cls', 'ano', 'A');
    await vi.waitFor(() => expect(screen.getByTestId('swap-song-count')).toHaveTextContent('1+ song: type more to narrow it'));
    answer({ songs: [songs[0], songs[1]], more: false }); // the older search, late
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getAllByTestId('swap-song-result')).toHaveLength(1);
  });

  it('a failed search says so; Escape cancels', async () => {
    searchMock.mockRejectedValue(new Error('offline'));
    const p = props();
    render(SwapSong, p);
    expect(await screen.findByTestId('swap-song-search-error')).toHaveTextContent('Search failed: offline');
    await fireEvent.keyDown(screen.getByTestId('swap-song'), { key: 'Escape' });
    expect(p.oncancel).toHaveBeenCalled();
  });

  it('while saving nothing can be picked; a failed save is shown', async () => {
    searchMock.mockResolvedValue({ songs: [songs[2]], more: false });
    render(SwapSong, { ...props(), busy: true, error: "that song isn't in any of the station's pools" });
    const [only] = await screen.findAllByTestId('swap-song-result');
    expect(only).toBeDisabled();
    expect(screen.getByTestId('swap-song-error')).toHaveTextContent("isn't in any of the station's pools");
  });
});

describe('WeekOnlyChanges', () => {
  const template: StationTemplate = {
    station: 's', name: 'S', sources: [],
    layout: { unit: 'day', unit_labels: ['Mon'], row_start_offsets: [0],
      row_template: [{ source: 'A', strategy: 'window', size: 2 }] },
  };
  const change = (over: Partial<WeekChange>): WeekChange => ({
    id: 1, row: 1, block: 1, k: 0, kind: 'swap', track_id: 5, rule: null, rule_strategy: 'window',
    rule_source: 'A', track: { id: 5, name: 'Mine', artist: 'Me', album: null }, position: 1,
    also_plays_at: [], created_at: null, ...over,
  });

  it("each change in words; one the version doesn't play says to regenerate", () => {
    render(WeekOnlyChanges, {
      changes: [
        change({}),
        change({ id: 2, kind: 'rule', k: 1, position: null, track: null, track_id: null,
          rule: { source: 'B', strategy: 'window', size: 2 } }),
      ],
      template, units: new Map([[1, 'Mon']]), onundo: vi.fn(),
    });
    const items = screen.getAllByTestId('week-only-item');
    expect(items[0]).toHaveTextContent('Song 1 (Mon)');
    expect(items[0]).toHaveTextContent('Swapped for Mine by Me');
    expect(items[1]).toHaveTextContent('Not in this version of the week');
    expect(items[1]).toHaveTextContent('Picked by its rule changed: source B');
    expect(screen.getByTestId('week-only-pending')).toHaveTextContent('regenerate it');
    expect(screen.getAllByTestId('week-only-undo')[0]).toHaveAccessibleName('Undo Song 1 (Mon): Swapped for Mine by Me');
  });

  it('after an Undo, focus goes to the next one, or where the page says once none are left', async () => {
    const target = document.createElement('button');
    document.body.append(target);
    let list = [change({}), change({ id: 2, position: 2 })];
    const onundo = vi.fn(async (c: WeekChange) => {
      list = list.filter((x) => x.id !== c.id);
      await rerender({ changes: list });
      return true;
    });
    const { rerender } = render(WeekOnlyChanges, {
      changes: list, template, units: new Map(), onundo, emptyFocus: () => target,
    });
    await fireEvent.click(screen.getAllByTestId('week-only-undo')[0]);
    await vi.waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('week-only-undo')));
    expect(screen.getByRole('status')).toHaveTextContent('Undone: Song 1: Swapped for Mine by Me.');
    await fireEvent.click(screen.getByTestId('week-only-undo'));
    await vi.waitFor(() => expect(document.activeElement).toBe(target));
    target.remove();
  });
});
