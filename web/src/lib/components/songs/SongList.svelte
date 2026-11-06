<script lang="ts">
  /**
   * A week's songs, each with the rule that put it there and why, in
   * playing order under the station's section names.
   *
   * Clicking a song calls `onselect` with its row (the rule's block and
   * editor ref included), and the page decides what that opens. With
   * `saved` (the same week as saved), songs are marked against what
   * their own rule played there (`compareWeeks`), rules followed through
   * the queue (`opLog`).
   *
   * With `onmove`, songs can be moved to play somewhere else: drag a
   * song's grip (its whole rule moves, a macro all of it), a selection
   * (shift-click a second song: every song between, and their rules), or
   * a section's heading. Or from the keyboard: Space on a grip picks it
   * up, the arrow keys choose where, Space drops it, Escape puts it back.
   * Each drop is a `moveRules` or `moveSection` edit for the page to
   * queue. A drop goes between rules, never inside one, and joins the
   * section it's drawn in. Off, with a line saying why, on stations built
   * from a repeating row, while the list is searched or filtered, and when
   * the page says so (`moveBlocked`: while its preview catches up, say).
   */
  import { tick } from 'svelte';
  import type { Trace } from '$lib/api';
  import { applyEdit, blockRuns, sectionSlots, whyRulesCantMove, type Edit, type MoveRulesOp, type MoveSectionOp } from '$lib/edit-ops';
  import { sourceColour, type Sidecar, type StationTemplate } from '$lib/editor';
  import { filterGroups, removedCount, rowRule, ruleKey, songGroups, unitWord, type RuleDefaults, type SongRow } from '$lib/songList';

  interface Props {
    trace: Trace;
    template: StationTemplate;
    sidecar: Sidecar;
    /** The same week as saved, to mark changes against. */
    saved?: Trace | null;
    /** The queued edits, to follow rules back to what they played. */
    opLog?: Edit[];
    /** The selected song's position (1-based), if any. */
    selected?: number | null;
    /** Mark every song made by the rule with this key (ruleKey), so the
     *  page can show which songs a change to that rule touches. */
    related?: string | null;
    onselect?: (row: SongRow) => void;
    /** Songs can be moved: each drop, as an edit to queue. */
    onmove?: (edit: MoveRulesOp | MoveSectionOp) => void;
    /** Why songs can't be moved just now (the page's say), or null. */
    moveBlocked?: string | null;
    /** The station's defaults for settings a rule leaves out. */
    ruleDefaults?: RuleDefaults;
  }

  let {
    trace, template, sidecar, saved = null, opLog = [], selected = null, related = null,
    onselect, onmove, moveBlocked = null, ruleDefaults = {},
  }: Props = $props();

  const allGroups = $derived(songGroups(trace, template, sidecar, saved, opLog));
  const removed = $derived(removedCount(trace, saved, template, opLog));

  // Search: song, artist, the rule in words, macro or section.
  let query = $state('');
  let onlyChanged = $state(false);
  const changedCount = $derived(allGroups.reduce((n, g) => n + g.rows.filter((r) => r.change !== null).length, 0));
  // "Only changed" applies while there's something to compare with.
  const filtering = $derived(!!query.trim() || (onlyChanged && saved !== null));
  const groups = $derived(filterGroups(allGroups, query, onlyChanged && saved !== null, ruleDefaults));
  const shown = $derived(groups.reduce((n, g) => n + g.rows.length, 0));

  function clear(): void {
    query = '';
    onlyChanged = false;
  }

  // ---- moving ------------------------------------------------------------

  type Run = { from: number; count: number };
  // A station built from a repeating row (a day, say) moves its rules
  // in every row: the row's rules, numbered by their place in it.
  const rows = $derived(!!template.layout.row_template?.length);
  const cantMove = $derived(whyRulesCantMove(template, sidecar));
  const movable = $derived(cantMove === null);
  const ruleCount = $derived((rows ? template.layout.row_template : template.layout.flat_slots)?.length ?? 0);
  const every = $derived(rows ? `every ${unitWord(template)}` : null);
  /** Why songs can't be moved now, when the page lets them be. */
  const moveNote = $derived(
    !onmove ? null
      : cantMove !== null ? cantMove
        : filtering ? 'Clear the search to move songs.'
          : moveBlocked,
  );
  const canMove = $derived(!!onmove && moveNote === null);
  // Grips stay while moving waits (the page catching up, say), so a
  // grip that has focus keeps it; they just don't pick anything up.
  const showGrips = $derived(!!onmove && movable && !filtering);
  // Told it can't now, mid-carry: put it back.
  $effect(() => {
    if (!canMove && moving) end();
  });

  // Each slot's block: what dragging one of its songs moves.
  const runs = $derived(movable ? blockRuns(sidecar) : new Map<number, Run>());
  const runOf = (row: SongRow): Run | null => runs.get(row.slot.block - 1) ?? null;

  /** A place a drop can go: before the rule a song starts (in its
   *  section), or after a run's last rule. `row`: the week's row (day)
   *  it's in, for a repeating row's (each row has the same places). */
  interface Spot { key: string; to: number; sectionId: string; row: number; label: string }
  const spots = $derived.by((): Spot[] => {
    const out: Spot[] = [];
    if (!movable) return out;
    const unit = unitWord(template);
    allGroups.forEach((g, gi) => {
      if (g.sectionId === null) return;
      let last: { run: Run; row: SongRow } | null = null;
      const end = (at: { run: Run; row: SongRow }, key: string) => out.push({
        key, to: at.run.from + at.run.count, sectionId: g.sectionId!, row: at.row.slot.row,
        label: rows ? `at the end of ${g.sectionName} (${at.row.slot.unit || `${unit} ${at.row.slot.row}`})` : `at the end of ${g.sectionName}`,
      });
      for (const r of g.rows) {
        const run = runOf(r);
        if (!run) continue;
        // A group can run on into the next row: its end in this row.
        if (last && r.slot.row !== last.row.slot.row) {
          end(last, `a${last.row.position}`);
          last = null;
        }
        if (!last || run.from !== last.run.from) {
          out.push({ key: `b${r.position}`, to: run.from, sectionId: g.sectionId, row: r.slot.row,
            label: `before ${r.slot.track_name} in ${g.sectionName}` });
        }
        last = { run, row: r };
      }
      if (last) end(last, `e${gi}`);
    });
    return out;
  });

  /** A section heading that can be dragged: the section's rules all play
   *  together, and all of them are this run. */
  function sectionRun(gi: number): Run | null {
    const g = allGroups[gi];
    if (!movable || !g?.sectionId) return null;
    const mine = sectionSlots(sidecar, g.sectionId);
    if (!mine?.length || mine.some((p, k) => p !== mine[0] + k)) return null;
    const first = runOf(g.rows[0]);
    const last = runOf(g.rows[g.rows.length - 1]);
    if (!first || !last || first.from !== mine[0] || last.from + last.count !== mine[0] + mine.length) return null;
    return { from: mine[0], count: mine.length };
  }
  /** Where a section can go: before a run that starts its section, or the end. */
  interface SectionSpot { key: string; to: number; beforeSectionId: string | null; group: number | null; row: number | null; label: string }
  const sectionSpots = $derived.by((): SectionSpot[] => {
    const out: SectionSpot[] = [];
    allGroups.forEach((g, gi) => {
      if (!g.sectionId) return;
      const first = sectionSlots(sidecar, g.sectionId)?.[0];
      const run = runOf(g.rows[0]);
      if (first !== undefined && run?.from === first) {
        out.push({ key: `s${gi}`, to: first, beforeSectionId: g.sectionId, group: gi, row: g.rows[0].slot.row, label: `before ${g.sectionName}` });
      }
    });
    out.push({ key: 's-end', to: ruleCount, beforeSectionId: null, group: null, row: null, label: 'at the end' });
    return out;
  });

  // Selected songs (shift-click): every song from the anchor to the
  // clicked one, and the whole of each rule they're in.
  let anchor: number | null = $state(null);
  let selection: Run | null = $state(null);
  /** The row a repeating row's selection was made in (its songs are
   *  counted and moved from there). */
  let selectionRow = $state(1);
  // Numbered by rules: a new list (an Undo, the preview catching up)
  // may have other songs there, so it starts again.
  $effect(() => {
    void trace;
    selection = null;
    anchor = null;
  });
  const selectedCount = $derived(selection
    ? allGroups.reduce((n, g) => n + g.rows.filter((r) => inSelection(r) && (!rows || r.slot.row === selectionRow)).length, 0) : 0);
  function inSelection(r: SongRow): boolean {
    const p = r.slot.block - 1;
    return selection !== null && p >= selection.from && p < selection.from + selection.count;
  }
  /** False (and says so) when the songs are in different rows of a
   *  repeating row's week: a selection is of rules in one row. */
  function selectTo(row: SongRow): boolean {
    if (anchor === null) return false;
    const all = allGroups.flatMap((g) => g.rows);
    const [a, b] = [Math.min(anchor, row.position), Math.max(anchor, row.position)];
    const between = all.filter((r) => r.position >= a && r.position <= b);
    if (rows && between.some((r) => r.slot.row !== row.slot.row)) {
      void announce(`Select songs of one ${unitWord(template)}: a move is made in every ${unitWord(template)}.`);
      return false;
    }
    const picked = between.map(runOf);
    if (picked.some((r) => r === null)) return false;
    const from = Math.min(...picked.map((r) => r!.from));
    const end = Math.max(...picked.map((r) => r!.from + r!.count));
    selection = { from, count: end - from };
    selectionRow = row.slot.row;
    return true;
  }
  function pick(e: MouseEvent, row: SongRow): void {
    if (e.shiftKey && canMove && anchor !== null) {
      if (selectTo(row)) void announce(`${selectedCount} songs selected. Use a grip to move them.`);
      return;
    }
    anchor = row.position;
    selection = null;
    onselect?.(row);
  }

  // What's being moved, and where to.
  // `row`: the week's row (day) it was picked up in; a repeating row's
  // places are offered from that row (each row has the same).
  type Moving =
    | { kind: 'rules'; run: Run; row: number; label: string }
    | { kind: 'section'; run: Run; sectionId: string; row: number; label: string };
  let moving: Moving | null = $state(null);
  let target: string | null = $state(null);
  let carrying = $state(false);

  /** The sections the run's songs are in now (a repeating row's: in
   *  row `row`, the one it was picked up in). */
  function runSections(run: Run, row: number): Set<string | null> {
    const out = new Set<string | null>();
    for (const g of allGroups) {
      for (const r of g.rows) {
        const p = r.slot.block - 1;
        if (p >= run.from && p < run.from + run.count && (!rows || r.slot.row === row)) out.add(g.sectionId);
      }
    }
    return out;
  }
  /** A place the run might go: somewhere else in play, or where it is
   *  in another section (over the line between two: only the section
   *  changes). `own`: its sections, in the row it was picked up in.
   *  (Whether the station takes it is `takes`.) */
  function canGo(m: Moving, s: Spot, own: Set<string | null>): boolean {
    const { run } = m;
    if (s.to < run.from || s.to > run.from + run.count) return true;
    return !(own.size === 1 && own.has(s.sectionId));
  }
  /** Whether a drop at place `key` is a move: somewhere it can go, that
   *  the station takes. */
  function canDrop(m: Moving, key: string): boolean {
    if (m.kind === 'rules') {
      const spot = spots.find((s) => s.key === key);
      return !!spot && canGo(m, spot, runSections(m.run, m.row)) && takes(m, key);
    }
    const spot = sectionSpots.find((s) => s.key === key);
    // (`takes` here is belt and braces: a section's places are only where
    // another section starts, which the station takes.)
    return !!spot && (spot.to < m.run.from || spot.to > m.run.from + m.run.count) && takes(m, key);
  }

  function songsLabel(run: Run, row: number): string {
    const songs = allGroups.flatMap((g) => g.rows).filter((r) => {
      const p = r.slot.block - 1;
      return p >= run.from && p < run.from + run.count && (!rows || r.slot.row === row);
    });
    return songs.length === 1 ? songs[0].slot.track_name : `${songs.length} songs`;
  }
  function movingFor(row: SongRow): Moving | null {
    const run = selection && inSelection(row) ? selection : runOf(row);
    return run ? { kind: 'rules', run, row: row.slot.row, label: songsLabel(run, row.slot.row) } : null;
  }
  function movingSection(gi: number): Moving | null {
    const run = sectionRun(gi);
    const g = allGroups[gi];
    return run && g.sectionId
      ? { kind: 'section', run, sectionId: g.sectionId, row: g.rows[0].slot.row, label: `section ${g.sectionName}` }
      : null;
  }

  /** The edit a drop at place `key` would be, or null. */
  function editFor(m: Moving, key: string): MoveRulesOp | MoveSectionOp | null {
    if (m.kind === 'rules') {
      const spot = spots.find((s) => s.key === key);
      if (!spot) return null;
      // Where it plays already (into another section): `to` just after
      // the run, as the edit has it.
      const stays = spot.to >= m.run.from && spot.to <= m.run.from + m.run.count;
      const to = stays ? m.run.from + m.run.count : spot.to;
      return { kind: 'moveRules', from: m.run.from, count: m.run.count, to, sectionId: spot.sectionId };
    }
    const spot = sectionSpots.find((s) => s.key === key);
    if (!spot) return null;
    return {
      kind: 'moveSection', sectionId: m.sectionId, beforeSectionId: spot.beforeSectionId,
      from: m.run.from, count: m.run.count, to: spot.to,
    };
  }
  /** Whether the station takes the drop at `key`: the preview's mirror
   *  of the server says (a move that would leave a macro with nothing
   *  to stand on, say, it refuses). Asked once per place a carry. */
  let takenFor: { m: Moving; at: Map<string, boolean> } | null = null;
  function takes(m: Moving, key: string): boolean {
    if (takenFor?.m !== m) takenFor = { m, at: new Map() };
    let ok = takenFor.at.get(key);
    if (ok === undefined) {
      const edit = editFor(m, key);
      const out = edit ? applyEdit(template, sidecar, edit) : null;
      ok = !!out && (out.template !== template || out.sidecar !== sidecar);
      takenFor.at.set(key, ok);
    }
    return ok;
  }

  function drop(): void {
    const m = moving;
    const key = target;
    const byKey = carrying;
    end();
    if (!m || key === null || !onmove) return;
    const edit = canDrop(m, key) ? editFor(m, key) : null;
    if (!edit) return;
    onmove(edit);
    const label = (m.kind === 'rules' ? spots : sectionSpots).find((s) => s.key === key)!.label;
    announce(`Moved ${m.label} ${label}.`);
    if (byKey && edit.kind === 'moveRules') {
      const { from, count, to } = edit;
      const now = to === from + count ? from : to > from ? to - count : to;
      follow(`[data-testid="song-grip"][data-rule="${now}"][data-row="${m.row}"]`);
    } else if (byKey && m.kind === 'section') {
      follow(`[data-testid="section-grip"][data-section="${CSS.escape(m.sectionId)}"][data-row="${m.row}"]`);
    }
    selection = null;
  }
  /** After a move from the keyboard, focus goes with what moved: to
   *  its grip (`selector`), once the list shows the new order. */
  let following: { selector: string; trace: Trace } | null = null;
  function follow(selector: string): void {
    following = { selector, trace };
  }
  $effect(() => {
    const now = trace;
    void groups;
    if (!following || following.trace === now) return;
    const { selector } = following;
    following = null;
    void tick().then(() => listEl?.querySelector<HTMLElement>(selector)?.focus());
  });
  function end(): void {
    moving = null;
    target = null;
    carrying = false;
  }

  // ---- with a pointer: drag and drop --------------------------------------

  function dragStart(e: DragEvent, m: Moving | null): void {
    if (!canMove || !m) {
      e.preventDefault();
      return;
    }
    moving = m;
    e.dataTransfer?.setData('text/plain', m.label);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }
  /** Over a song: before its rule or after it, by which half of the
   *  rule's songs the pointer is in. */
  function dragOverRow(e: DragEvent, row: SongRow, gi: number): void {
    if (!moving) return;
    e.preventDefault();
    if (moving.kind === 'section') return dragOverGroup(e, gi);
    const run = runOf(row);
    const g = allGroups[gi];
    if (!run || !g.sectionId) {
      target = null;
      return;
    }
    const mine = g.rows.filter((r) => runOf(r)?.from === run.from && r.slot.row === row.slot.row);
    const at = mine.indexOf(row);
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const within = box.height > 0 ? (e.clientY - box.top) / box.height : 0;
    const before = (at + within) / mine.length < 0.5;
    const i = spots.findIndex((s) => s.key === `b${mine[0].position}`);
    const spot = before ? spots[i] : spots[i + 1];
    target = spot && canDrop(moving, spot.key) ? spot.key : null;
    if (e.dataTransfer) e.dataTransfer.dropEffect = target ? 'move' : 'none';
  }
  /** Over a group, not a song. A section: before this run (when it
   *  starts a section) or before the next run that does. */
  function dragOverGroup(e: DragEvent, gi: number): void {
    if (!moving) return;
    if (moving.kind === 'rules') {
      // Over a song, the row said where. Over the heading (or the gap
      // under the list): the start of this section, so a drop there
      // does what the line shows.
      if ((e.target as Element | null)?.closest?.('li')) return;
      e.preventDefault();
      const first = allGroups[gi]?.rows[0];
      const key = first ? `b${first.position}` : null;
      target = key && canDrop(moving, key) ? key : null;
      if (e.dataTransfer) e.dataTransfer.dropEffect = target ? 'move' : 'none';
      return;
    }
    e.preventDefault();
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const upper = box.height > 0 && e.clientY - box.top < box.height / 2;
    const later = sectionSpots.filter((s) => s.group === null || s.group > gi || (upper && s.group === gi));
    const spot = later[0];
    target = spot && canDrop(moving, spot.key) ? spot.key : null;
    if (e.dataTransfer) e.dataTransfer.dropEffect = target ? 'move' : 'none';
  }
  function dropHere(e: DragEvent): void {
    if (!moving) return;
    e.preventDefault();
    drop();
  }

  // ---- with the keyboard --------------------------------------------------

  let status = $state('');
  async function announce(text: string): Promise<void> {
    // Cleared first, so the same words twice running are said twice.
    status = '';
    await tick();
    status = text;
  }
  /** Where the carried songs or section might go, in list order, with
   *  their own place ("here") among them. (Whether the station takes
   *  each is asked as the arrows reach it: `takes`.) */
  function carryStops(m: Moving): { key: string; label: string }[] {
    if (m.kind === 'rules') {
      const own = runSections(m.run, m.row);
      // A repeating row's: the places in the row it was picked up in
      // (every row has the same ones).
      const mine = rows ? spots.filter((s) => s.row === m.row) : spots;
      const near = (s: Spot) => s.to >= m.run.from && s.to <= m.run.from + m.run.count;
      // Its own place: where its own section's spot is, or (a run in
      // more than one section) the first place it already plays at.
      let here = mine.findIndex((s) => near(s) && !canGo(m, s, own));
      if (here === -1) here = mine.findIndex(near);
      const stops: { key: string; label: string }[] = [];
      const into = new Set<string>(); // sections it can join where it plays: once each
      mine.forEach((s, i) => {
        if (i === here) stops.push({ key: 'here', label: 'back where it was' });
        if (!canGo(m, s, own)) return;
        if (near(s)) {
          if (into.has(s.sectionId)) return;
          into.add(s.sectionId);
        }
        stops.push({ key: s.key, label: s.label });
      });
      return stops;
    }
    const stops = sectionSpots
      .filter((s) => (s.to < m.run.from || s.to > m.run.from + m.run.count) && (!rows || s.row === null || s.row === m.row))
      .map((s) => ({ key: s.key, label: s.label, to: s.to }));
    const at = stops.findIndex((s) => s.to > m.run.from);
    stops.splice(at === -1 ? stops.length : at, 0, { key: 'here', label: 'back where it was', to: m.run.from });
    return stops;
  }
  function gripKey(e: KeyboardEvent, m: Moving | null): void {
    if (!canMove) {
      if (moveNote && (e.key === ' ' || e.key === 'Enter')) {
        e.preventDefault();
        void announce(moveNote);
      }
      return;
    }
    if (!m) return;
    if (!carrying) {
      if (e.key !== ' ' && e.key !== 'Enter') return;
      e.preventDefault();
      moving = m;
      carrying = true;
      target = 'here';
      void announce(`Picked up ${m.label}. Up and down arrows to choose where, Space to drop, Escape to put it back.`);
      return;
    }
    if (!moving) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      end();
      void announce('Put back.');
      return;
    }
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      if (target === 'here') {
        end();
        void announce('Put back where it was.');
      } else {
        drop();
      }
      return;
    }
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown' || e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      const stops = carryStops(moving);
      const at = stops.findIndex((s) => s.key === target);
      // The next place the station takes, that way (each asked only when
      // reached: on a big station asking all at once would lag).
      const m = moving;
      const ok = (i: number) => stops[i].key === 'here' || takes(m, stops[i].key);
      const step = e.key === 'ArrowUp' ? -1 : 1;
      let next = e.key === 'Home' ? 0 : e.key === 'End' ? stops.length - 1 : at + step;
      if (e.key === 'Home' || e.key === 'End') {
        // Inwards from that end ("here" is always there to stop at).
        const inwards = e.key === 'Home' ? 1 : -1;
        while (!ok(next)) next += inwards;
      } else {
        while (next >= 0 && next < stops.length && !ok(next)) next += step;
        if (next < 0 || next >= stops.length) next = at;
      }
      target = stops[next].key;
      void announce(stops[next].label);
      void tick().then(() => listEl?.querySelector('.drop-line')?.scrollIntoView?.({ block: 'nearest' }));
    }
  }
  function gripBlur(): void {
    if (carrying) {
      end();
      void announce('Put back.');
    }
  }
  function listKey(e: KeyboardEvent): void {
    if (e.key === 'Escape' && selection && !carrying) {
      selection = null;
      void announce('Selection cleared.');
    }
  }

  /** The songs being moved: dimmed, so it's plain what's on the move. */
  function isCarried(r: SongRow): boolean {
    if (!moving) return false;
    const p = r.slot.block - 1;
    return p >= moving.run.from && p < moving.run.from + moving.run.count;
  }

  let listEl: HTMLElement | null = $state(null);
  const lineBefore = (r: SongRow): boolean => target === `b${r.position}`;
  /** A repeating row's: the first song shown of each row (day), which
   *  says which (a search can hide a day's first). */
  const dayStarts = $derived.by(() => {
    const out = new Set<number>();
    let last: number | null = null;
    for (const g of groups) {
      for (const r of g.rows) {
        if (r.slot.row !== last) out.add(r.position);
        last = r.slot.row;
      }
    }
    return out;
  });
  const startsDay = (r: SongRow): boolean => dayStarts.has(r.position);
  /** After a song: a row's end, in a group that runs on into the next. */
  const lineAfter = (r: SongRow): boolean => target === `a${r.position}`;
  const lineAtEnd = (gi: number): boolean => target === `e${gi}`;
  const lineBeforeGroup = (gi: number): boolean => target === `s${gi}`;
</script>

<div class="song-filters" data-testid="song-filters">
  <input
    type="search"
    placeholder="Search songs, artists or rules…"
    aria-label="Search songs, artists or rules"
    autocomplete="off"
    data-testid="song-search"
    bind:value={query}
  />
  {#if saved !== null}
    <label class="only-changed">
      <input type="checkbox" data-testid="song-only-changed" bind:checked={onlyChanged} />
      Only changed songs ({changedCount})
    </label>
  {/if}
  {#if filtering}
    <span class="shown" data-testid="song-shown" aria-live="polite">
      Showing {shown.toLocaleString()} of {trace.slots.length.toLocaleString()}
    </span>
    <button type="button" class="clear" data-testid="song-search-clear" onclick={clear}>Clear</button>
  {/if}
</div>
{#if onmove}
  <p class="move-note" data-testid="song-move-note">
    {#if moveNote}
      {moveNote}
    {:else if selection}
      {selectedCount} songs selected: drag one of their grips to move them all.
      <button type="button" class="clear" data-testid="song-selection-clear" onclick={() => (selection = null)}>Clear selection</button>
    {:else}
      Drag a song's grip, or a section's, to move it. Shift-click a second song to select the songs between.{#if every}{' '}A
        move is made in {every}: each plays the same rules.{/if}
    {/if}
  </p>
{/if}
<p class="visually-hidden" id="song-grip-help">
  Space to pick it up, then the up and down arrows to choose where, Space to drop it, Escape to put it back.{#if every}{' '}It
    moves in {every}.{/if}
</p>
<span class="visually-hidden" role="status" aria-live="assertive" data-testid="song-move-status">{status}</span>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<section
  class="song-list"
  class:moving={moving !== null}
  aria-label="Songs this week"
  data-testid="song-list"
  bind:this={listEl}
  onkeydown={listKey}
>
  {#each groups as group, gi (gi)}
    {@const headRun = showGrips ? sectionRun(gi) : null}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="group"
      ondragover={(e) => dragOverGroup(e, gi)}
      ondrop={dropHere}
    >
      {#if lineBeforeGroup(gi)}<div class="drop-line" data-testid="song-drop-line" aria-hidden="true"></div>{/if}
      <h3 class="section-name" data-testid="song-section">
        {#if headRun}
          <button
            type="button"
            class="grip"
            draggable={canMove}
            aria-disabled={!canMove}
            aria-label="Move section {group.sectionName}"
            data-section={group.sectionId}
            data-row={group.rows[0].slot.row}
            aria-describedby="song-grip-help"
            data-testid="section-grip"
            ondragstart={(e) => dragStart(e, movingSection(gi))}
            ondragend={end}
            onkeydown={(e) => gripKey(e, movingSection(gi))}
            onblur={gripBlur}
          ><span aria-hidden="true">⋮⋮</span></button>
        {/if}
        {group.sectionName}
        <span class="count">{group.rows.length} {group.rows.length === 1 ? 'song' : 'songs'}</span>
      </h3>
      <ol class="songs" start={group.rows[0].position}>
        {#each group.rows as row (row.position)}
          <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
          <li
            class:drop-before={lineBefore(row)}
            class:in-selection={inSelection(row)}
            class:carried={isCarried(row)}
            ondragover={(e) => dragOverRow(e, row, gi)}
            ondrop={dropHere}
          >
            {#if lineBefore(row)}<div class="drop-line" data-testid="song-drop-line" aria-hidden="true"></div>{/if}
            {#if lineAfter(row)}<div class="drop-line drop-line--after" data-testid="song-drop-line" aria-hidden="true"></div>{/if}
            {#if showGrips && runOf(row) && group.sectionId}
              <button
                type="button"
                class="grip"
                draggable={canMove}
                aria-disabled={!canMove}
                data-rule={row.slot.block - 1}
                data-row={row.slot.row}
                aria-label="Move {selection && inSelection(row) ? `${selectedCount} selected songs` : row.slot.track_name}"
                aria-describedby="song-grip-help"
                data-testid="song-grip"
                ondragstart={(e) => dragStart(e, movingFor(row))}
                ondragend={end}
                onkeydown={(e) => gripKey(e, movingFor(row))}
                onblur={gripBlur}
              ><span aria-hidden="true">⋮⋮</span></button>
            {/if}
            <button
              type="button"
              class="song"
              class:selected={selected === row.position}
              class:changed={row.change !== null}
              class:related={related !== null && row.ref !== null && ruleKey(row.ref) === related}
              aria-pressed={selected === row.position}
              data-testid="song-row"
              onclick={(e) => pick(e, row)}
            >
              <span class="position">{row.position}{#if rows && startsDay(row)}<span class="unit" data-testid="song-unit">{row.slot.unit || `${unitWord(template)} ${row.slot.row}`}</span>{/if}</span>
              <span class="track">
                <span class="title">{row.slot.track_name}</span>
                {#if row.slot.track_artist}<span class="artist">{row.slot.track_artist}</span>{/if}
              </span>
              <span class="rule" data-testid="song-rule">
                <span
                  class="dot"
                  style:background={sourceColour(template, row.slot.source)}
                  aria-hidden="true"
                ></span>
                {rowRule(row, ruleDefaults)}{#if row.macroName}<span class="macro">{' '}· {row.macroName}</span>{/if}{#if row.ruleChanged}<span
                    class="macro" data-testid="song-rule-changed">{' '}· rule changed since</span>{/if}
              </span>
              {#if row.change}
                <span
                  class="change change--{row.change}"
                  data-testid="song-change"
                  title={row.change === 'moved' ? 'Its rule plays here now, so the song it picks can differ' : undefined}
                >{row.change === 'added' ? 'new' : row.change}</span>
              {/if}
              <span class="why" data-testid="song-why"><span class="visually-hidden">Why:</span> {row.slot.reason}</span>
            </button>
          </li>
        {/each}
      </ol>
      {#if lineAtEnd(gi)}<div class="drop-line drop-line--end" data-testid="song-drop-line" aria-hidden="true"></div>{/if}
    </div>
  {/each}
  {#if target === 's-end'}<div class="drop-line" data-testid="song-drop-line" aria-hidden="true"></div>{/if}
  {#if filtering && shown === 0}
    <p class="none" role="status" data-testid="song-search-empty">
      No songs match.
      <button type="button" class="clear" onclick={clear}>Clear the search</button>
    </p>
  {/if}
  {#if removed > 0}
    <p class="removed" data-testid="song-removed">
      {removed} {removed === 1 ? 'song' : 'songs'} from the saved week {removed === 1 ? 'is' : 'are'} no longer in it.
    </p>
  {/if}
</section>

<style>
  .song-filters {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 1rem;
    margin: 0 0 0.75rem;
  }
  .song-filters input[type='search'] {
    flex: 1 1 16rem;
    max-width: 28rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel);
    color: inherit;
    font: inherit;
  }
  .only-changed {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
  }
  .shown {
    color: var(--muted);
  }
  .clear {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
    font: inherit;
  }
  .clear:hover {
    color: var(--accent-strong);
  }
  .none {
    margin: 0.5rem 0;
    color: var(--muted);
  }
  .move-note {
    margin: -0.25rem 0 0.75rem;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .song-list {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    /* Laid out by its own width, not the window's: it's a full page on
       a week and a narrow column beside the editor's blocks. */
    container-type: inline-size;
  }
  .group {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .section-name {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    margin: 1rem 0 0.25rem;
    font-size: 0.85rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  .group:first-child .section-name {
    margin-top: 0;
  }
  .count {
    margin-left: 0.5rem;
    font-weight: 400;
    text-transform: none;
    letter-spacing: 0;
    color: var(--faint);
  }
  .songs {
    list-style: none;
    margin: 0;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius, 6px);
    background: var(--panel);
    overflow: hidden;
  }
  .songs li + li {
    border-top: 1px solid var(--border);
  }
  /* A week can be 2,000+ songs: skip laying out rows that are off
     screen (they stay in the DOM, so find-in-page and focus still work). */
  .songs li {
    position: relative;
    display: flex;
    content-visibility: auto;
    contain-intrinsic-size: auto 4rem;
  }
  .songs li.in-selection {
    background: var(--accent-soft);
  }
  /* What's being moved: outlined (not faded, so it stays readable). */
  .songs li.carried {
    outline: 2px dashed var(--accent);
    outline-offset: -2px;
  }
  .grip {
    flex: 0 0 auto;
    align-self: stretch;
    width: 1.5rem;
    padding: 0;
    border: 0;
    background: none;
    color: var(--faint);
    font: inherit;
    font-size: 0.8rem;
    letter-spacing: -0.15em;
    cursor: grab;
  }
  .grip:hover,
  .grip:focus-visible {
    background: var(--row-hover);
    color: var(--text);
  }
  .grip:focus-visible {
    outline: 2px solid var(--focus, var(--accent));
    outline-offset: -2px;
  }
  .section-name .grip {
    align-self: auto;
    width: 1.25rem;
    height: 1.25rem;
    border-radius: 4px;
  }
  .moving .grip {
    cursor: grabbing;
  }
  .grip[aria-disabled='true'] {
    cursor: not-allowed;
    opacity: 0.5;
  }
  /* Where a drop goes. */
  .drop-line {
    position: relative;
    height: 0;
    border-top: 3px solid var(--accent);
    border-radius: 2px;
    pointer-events: none;
  }
  /* Inside the row's own box: a row skipped off screen
     (content-visibility) clips what it paints to that box. */
  .songs li .drop-line {
    position: absolute;
    top: -1px;
    left: 0;
    right: 0;
    z-index: 1;
  }
  /* A notch at its start, so the line reads as a place, not a border. */
  .drop-line::before {
    content: '';
    position: absolute;
    left: 0;
    top: -3px;
    width: 4px;
    height: 12px;
    border-radius: 0 2px 2px 0;
    background: var(--accent);
  }
  .songs li .drop-line::before {
    top: 0;
    height: 9px;
  }
  .songs li .drop-line--after {
    top: auto;
    bottom: 0;
  }
  .songs li .drop-line--after::before {
    top: -9px;
  }
  .song {
    flex: 1 1 auto;
    min-width: 0;
    display: grid;
    grid-template-columns: 2.5rem minmax(0, 1fr) minmax(0, 1fr) auto;
    grid-template-areas:
      'pos track rule change'
      'pos why   why  why';
    column-gap: 0.75rem;
    row-gap: 0.15rem;
    align-items: baseline;
    width: 100%;
    padding: 0.5rem 0.75rem;
    border: 0;
    background: transparent;
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .grip + .song {
    padding-left: 0.25rem;
  }
  .song:hover {
    background: var(--row-hover);
  }
  /* Narrow (the editor's preview column): the rule goes under the song
     instead of squeezing beside it. */
  @container (max-width: 30rem) {
    .song {
      grid-template-columns: 2rem minmax(0, 1fr) auto;
      grid-template-areas:
        'pos track change'
        'pos rule  rule'
        'pos why   why';
      column-gap: 0.5rem;
      padding: 0.45rem 0.5rem;
    }
  }
  .song.related {
    background: var(--accent-soft);
  }
  .song.selected {
    background: var(--row-active);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .song:focus-visible {
    outline: 2px solid var(--focus, var(--accent));
    outline-offset: -2px;
  }
  .position {
    grid-area: pos;
    color: var(--faint);
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  /* A repeating row's first song of each day: which day. */
  .position .unit {
    display: block;
    margin-top: 0.1rem;
    color: var(--muted);
    font-size: 0.75rem;
    font-weight: 600;
  }
  .track {
    grid-area: track;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .title {
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .artist {
    color: var(--muted);
    font-size: 0.9em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rule {
    grid-area: rule;
    min-width: 0;
    font-size: 0.9em;
  }
  .dot {
    display: inline-block;
    width: 0.6em;
    height: 0.6em;
    margin-right: 0.4em;
    border-radius: 50%;
    vertical-align: baseline;
  }
  .macro {
    color: var(--muted);
  }
  .change {
    grid-area: change;
    padding: 0.05em 0.5em;
    border-radius: 999px;
    background: var(--warn-soft);
    color: var(--warn);
    font-size: 0.75rem;
    font-weight: 600;
  }
  .change--moved {
    background: var(--accent-soft);
    color: var(--accent-strong);
  }
  .why {
    grid-area: why;
    color: var(--muted);
    font-size: 0.8em;
    font-family: var(--font-mono, ui-monospace, monospace);
    overflow-wrap: anywhere;
  }
  .removed {
    margin: 0.5rem 0 0;
    color: var(--muted);
    font-size: 0.9em;
  }
  /* Narrow screens: rule under the song, the why under that. */
  @media (max-width: 40rem) {
    .song {
      grid-template-columns: 2rem minmax(0, 1fr) auto;
      grid-template-areas:
        'pos track change'
        'pos rule  rule'
        'pos why   why';
    }
  }
</style>
