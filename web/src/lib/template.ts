/**
 * Pure-function helpers for building and unpacking station template JSON.
 *
 * Extracted from <StationForm> (DESIGN.md §17 Phase A #4) so that both
 * the wizard, the edit route, and the future visual block editor (§16.2)
 * can consume them without importing a Svelte component.
 *
 * `buildTemplate` and `unpack` are an inverse pair — any field added to
 * one MUST be mirrored in the other. The round-trip test in
 * `template.test.ts` (and the older component-level S3 test in
 * `StationForm.test.ts`) catches drift.
 */

// ---- types --------------------------------------------------------------

export type Rng = 'off' | 'on';
export type Role = 'content' | 'reference';
export type Strategy = 'pick' | 'window';

export type Source = {
  name: string;
  colour: string;
  role: Role;
};

export type RowBlock = {
  source: string;
  strategy: Strategy;
  size: number;
};

export type FormState = {
  slug: string;
  name: string;
  cadence: string;
  rng: Rng;
  seedStr: string;
  sources: Source[];
  baseAdvancePerWeek: number;
  cycleWeeks: number;
  unit: string;
  /** Array of labels, one per row (e.g. ['Mon', 'Tue', ...]). */
  unitLabels: string[];
  /** Array of per-row start offsets (e.g. [0, 10, 20, ...]). */
  rowStartOffsets: number[];
  rowTemplate: RowBlock[];
};

/**
 * The slug + name + template-blob shape used both for the prop input
 * (loaded via getStation) and the submit-handler output (built from
 * the form). Matches `StationCreatePayload` from $lib/api.
 */
export type StationFormPayload = {
  slug: string;
  name: string;
  template: Record<string, unknown>;
};

// ---- parsing helpers ----------------------------------------------------

export function parseIntList(s: string): number[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
}

export function parseStringList(s: string): string[] {
  return s
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
}

// ---- defaults -----------------------------------------------------------

export function defaults(): FormState {
  return {
    slug: '',
    name: '',
    cadence: 'weekly',
    rng: 'off',
    seedStr: '',
    sources: [{ name: '', colour: '#c0392b', role: 'content' }],
    baseAdvancePerWeek: 0,
    cycleWeeks: 1,
    unit: 'day',
    unitLabels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
    rowStartOffsets: [0, 0, 0, 0, 0, 0, 0],
    rowTemplate: [{ source: '', strategy: 'pick', size: 1 }],
  };
}

// ---- unpack -------------------------------------------------------------

/**
 * Convert a stored template (the `Record<string, unknown>` blob from the
 * API) back into the form-shaped FormState. The reverse of buildTemplate().
 * Defensive: any missing field falls back to its default so a malformed
 * stored template doesn't blank the whole form.
 */
export function unpack(initial: StationFormPayload): FormState {
  const t = initial.template as Record<string, unknown>;
  const cursor = (t.cursor as Record<string, unknown>) ?? {};
  const layout = (t.layout as Record<string, unknown>) ?? {};
  const tplSources = (t.sources as Source[] | undefined) ?? [];
  const tplRowTemplate = (layout.row_template as RowBlock[] | undefined) ?? [];
  const tplUnitLabels = (layout.unit_labels as string[] | undefined) ?? [];
  const tplOffsets = (layout.row_start_offsets as number[] | undefined) ?? [];

  const d = defaults();
  return {
    slug: initial.slug,
    name: initial.name,
    cadence: (t.cadence as FormState['cadence']) ?? d.cadence,
    rng: (t.rng as Rng) ?? d.rng,
    seedStr: t.seed != null ? String(t.seed) : '',
    sources: tplSources.length > 0
      ? tplSources.map((s) => ({ name: s.name, colour: s.colour, role: s.role }))
      : d.sources,
    baseAdvancePerWeek:
      Number(cursor.base_advance_per_week) || d.baseAdvancePerWeek,
    cycleWeeks: Number(cursor.cycle_weeks) || d.cycleWeeks,
    unit: (layout.unit as string) ?? d.unit,
    unitLabels:
      tplUnitLabels.length > 0 ? [...tplUnitLabels] : d.unitLabels,
    rowStartOffsets:
      tplOffsets.length > 0 ? [...tplOffsets] : d.rowStartOffsets,
    rowTemplate: tplRowTemplate.length > 0
      ? tplRowTemplate.map((b) => ({ source: b.source, strategy: b.strategy, size: b.size }))
      : d.rowTemplate,
  };
}

// ---- buildTemplate ------------------------------------------------------

/**
 * Build the canonical template JSON blob from form state. The reverse of
 * unpack(). The output shape matches what `engine/template.parse_template`
 * expects.
 */
export function buildTemplate(state: FormState): Record<string, unknown> {
  const t: Record<string, unknown> = {
    station: state.slug.trim(),
    name: state.name.trim(),
    cadence: state.cadence,
    rng: state.rng,
    sources: state.sources.map((s) => ({
      name: s.name.trim(),
      colour: s.colour,
      role: s.role,
    })),
    cursor: {
      base_advance_per_week: state.baseAdvancePerWeek,
      cycle_weeks: state.cycleWeeks,
    },
    layout: {
      unit: state.unit,
      unit_labels: [...state.unitLabels],
      row_start_offsets: [...state.rowStartOffsets],
      row_template: state.rowTemplate.map((b) => ({
        source: b.source,
        strategy: b.strategy,
        size: b.size,
      })),
    },
  };
  // Omit seed when RNG is off (cleaner JSON; engine tolerates either).
  if (state.rng === 'on' && state.seedStr.trim() !== '') {
    t['seed'] = Number(state.seedStr);
  }
  return t;
}

// ---- client-side sanity check -------------------------------------------

/**
 * Pre-submit sanity check. Returns a human-readable error string, or null
 * if everything looks valid. Server-side validation via
 * engine.template.parse_template is the source of truth; these catch the
 * obviously-doomed round trips.
 *
 * NOTE (review N3, §14.20): The `contentSourceNames` filter below
 * duplicates the `$: contentSourceNames` reactive in <StationForm>.
 * Intentional — the reactive feeds the <LayoutFieldset> dropdown (render
 * concern), this function validates before submit (validation concern).
 * Merging them would couple this pure function to component state.
 * 2-line duplication; not worth extracting a shared helper.
 */
export function preSubmitProblem(state: FormState): string | null {
  if (!state.slug.trim()) return 'slug is required';
  if (!/^[a-z0-9][a-z0-9-]*$/.test(state.slug.trim()))
    return 'slug must be lowercase, digits, or dashes';
  if (!state.name.trim()) return 'display name is required';
  if (state.sources.length === 0) return 'at least one source is required';
  if (state.sources.some((s) => !s.name.trim())) return 'all sources need a name';
  const contentSourceNames = state.sources
    .filter((s) => s.role === 'content' && s.name.trim() !== '')
    .map((s) => s.name.trim());
  if (contentSourceNames.length === 0)
    return 'at least one content source is required';
  if (state.rowTemplate.length === 0)
    return 'at least one row-template block is required';
  if (state.rowTemplate.some((b) => !b.source))
    return 'each block must reference a source';
  return null;
}
