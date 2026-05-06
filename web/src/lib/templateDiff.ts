// Template diff comparator for the edit-mode form (DESIGN.md §16.7 D7).
//
// Pure function over the canonical template shape. Tailored output beats
// a generic `diff` library: "Sources unchanged" is a one-line summary,
// "row_start_offsets [0, 10, ...] → [0, 12, ...]" reads as one change,
// not seven whitespace lines. Output is grouped by the form's four
// fieldsets (Identity / Sources / Cursor / Layout) so the rendering
// matches the user's mental model of the form they just edited.
//
// Match strategy for the Sources array: by `name` (not array index) so
// reordering doesn't read as N changes. Added / removed sources show as
// + / - lines; modified sources surface only the changed sub-fields.

/**
 * Anything the diff comparator accepts. Mirrors `StationCreatePayload` from
 * `$lib/api` plus the slug field that lives outside `template` in the
 * server's response shape. Loose typing so the function can be called with
 * either the API-loaded station or a freshly-built form payload.
 */
export interface DiffableStation {
  slug: string;
  name: string;
  template: Record<string, unknown>;
}

export interface Change {
  /** Field path / human label (e.g. "rng", "source RG_RADIO"). */
  label: string;
  /** Pre-edit value, formatted for display. */
  before: string;
  /** Post-edit value, formatted for display. */
  after: string;
}

export interface Section {
  /** "Identity", "Sources", "Cursor", "Layout". */
  name: string;
  /** Empty array means the section is unchanged — render "X unchanged". */
  changes: Change[];
}

/**
 * Produce a 4-section diff. Sections always appear in the output array
 * even when unchanged so the UI can render a stable list with "unchanged"
 * placeholders where appropriate.
 */
export function templateDiff(
  before: DiffableStation,
  after: DiffableStation,
): Section[] {
  return [
    { name: 'Identity', changes: identityChanges(before, after) },
    { name: 'Sources', changes: sourcesChanges(before.template, after.template) },
    { name: 'Cursor', changes: cursorChanges(before.template, after.template) },
    { name: 'Layout', changes: layoutChanges(before.template, after.template) },
  ];
}

/** True iff every section's `changes` is empty — i.e. nothing changed. */
export function isClean(diff: Section[]): boolean {
  return diff.every((s) => s.changes.length === 0);
}

// ---- helpers --------------------------------------------------------------

function identityChanges(b: DiffableStation, a: DiffableStation): Change[] {
  const out: Change[] = [];
  // Slug never changes (immutable per §16.7 D3) but include it defensively
  // — if it ever does change, the diff makes that visible rather than
  // silently dropping the most important field.
  if (b.slug !== a.slug) {
    out.push({ label: 'slug', before: fmt(b.slug), after: fmt(a.slug) });
  }
  if (b.name !== a.name) {
    out.push({ label: 'name', before: fmt(b.name), after: fmt(a.name) });
  }
  for (const key of ['cadence', 'rng', 'seed'] as const) {
    if (b.template[key] !== a.template[key]) {
      out.push({
        label: key,
        before: fmt(b.template[key]),
        after: fmt(a.template[key]),
      });
    }
  }
  return out;
}

interface Source {
  name: string;
  colour: string;
  role: string;
}

function sourcesChanges(
  b: Record<string, unknown>,
  a: Record<string, unknown>,
): Change[] {
  const before = (b.sources as Source[] | undefined) ?? [];
  const after = (a.sources as Source[] | undefined) ?? [];
  const beforeByName = new Map(before.map((s) => [s.name, s]));
  const afterByName = new Map(after.map((s) => [s.name, s]));
  const out: Change[] = [];

  // Removed sources (in before, not in after).
  for (const [name, src] of beforeByName) {
    if (!afterByName.has(name)) {
      out.push({
        label: `source ${name}`,
        before: `${src.colour} / ${src.role}`,
        after: '(removed)',
      });
    }
  }
  // Added sources (in after, not in before).
  for (const [name, src] of afterByName) {
    if (!beforeByName.has(name)) {
      out.push({
        label: `source ${name}`,
        before: '(added)',
        after: `${src.colour} / ${src.role}`,
      });
    }
  }
  // Modified sources (in both, but different).
  for (const [name, bSrc] of beforeByName) {
    const aSrc = afterByName.get(name);
    if (!aSrc) continue;
    if (bSrc.colour !== aSrc.colour || bSrc.role !== aSrc.role) {
      out.push({
        label: `source ${name}`,
        before: `${bSrc.colour} / ${bSrc.role}`,
        after: `${aSrc.colour} / ${aSrc.role}`,
      });
    }
  }
  return out;
}

function cursorChanges(
  b: Record<string, unknown>,
  a: Record<string, unknown>,
): Change[] {
  const before = (b.cursor as Record<string, unknown> | undefined) ?? {};
  const after = (a.cursor as Record<string, unknown> | undefined) ?? {};
  const out: Change[] = [];
  for (const key of ['base_advance_per_week', 'cycle_weeks'] as const) {
    if (before[key] !== after[key]) {
      out.push({
        label: `cursor.${key}`,
        before: fmt(before[key]),
        after: fmt(after[key]),
      });
    }
  }
  return out;
}

interface RowBlock {
  source: string;
  strategy: string;
  size: number;
}

function layoutChanges(
  b: Record<string, unknown>,
  a: Record<string, unknown>,
): Change[] {
  const before = (b.layout as Record<string, unknown> | undefined) ?? {};
  const after = (a.layout as Record<string, unknown> | undefined) ?? {};
  const out: Change[] = [];

  // Scalar layout fields.
  if (before.unit !== after.unit) {
    out.push({
      label: 'layout.unit',
      before: fmt(before.unit),
      after: fmt(after.unit),
    });
  }

  // Array fields — compare as JSON to dodge reference/element drift.
  for (const key of ['unit_labels', 'row_start_offsets'] as const) {
    const bs = JSON.stringify(before[key] ?? []);
    const as_ = JSON.stringify(after[key] ?? []);
    if (bs !== as_) {
      out.push({ label: `layout.${key}`, before: bs, after: as_ });
    }
  }

  // row_template — array of blocks. Compare as JSON for the whole list:
  // edits to a row's source/strategy/size manifest as array changes,
  // and reordering matters here (unlike sources, where name keys identity).
  const bRows = (before.row_template as RowBlock[] | undefined) ?? [];
  const aRows = (after.row_template as RowBlock[] | undefined) ?? [];
  if (JSON.stringify(bRows) !== JSON.stringify(aRows)) {
    out.push({
      label: 'layout.row_template',
      before: blocksFmt(bRows),
      after: blocksFmt(aRows),
    });
  }

  return out;
}

function fmt(v: unknown): string {
  if (v === undefined || v === null) return '(unset)';
  if (typeof v === 'string') return JSON.stringify(v);
  return String(v);
}

function blocksFmt(blocks: RowBlock[]): string {
  if (blocks.length === 0) return '[]';
  return blocks
    .map((b) => `${b.source}/${b.strategy}/${b.size}`)
    .join(', ');
}
