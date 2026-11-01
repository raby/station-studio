/**
 * Per-station UI preferences kept in localStorage: whether live preview
 * is on, which list the preview shows (songs or slots), whether the
 * macro palette is open, the preview week, the compare-against-saved
 * toggle and how many extra weeks to compare.
 *
 * Moved out of the station editor page (stations/[slug]/visualise) so
 * the week page's song-list editor reads and writes the same values.
 * Keys and fallbacks are unchanged, so existing preferences carry over.
 *
 * Every read and write is wrapped: localStorage can be missing (SSR),
 * blocked (privacy mode) or full. A failed read falls back to the
 * default; a failed write just doesn't persist.
 */

export const prefKeys = {
  livePreview: (slug: string) => `livePreview:${slug}`,
  previewTab: (slug: string) => `previewTab:${slug}`,
  macroPalette: (slug: string) => `macroPalette:${slug}`,
  previewWeek: (slug: string) => `previewWeek:${slug}`,
  showDiff: (slug: string) => `showDiff:${slug}`,
  compareNextN: (slug: string) => `compareNextN:${slug}`,
};

/** Upper bound for the extra weeks the multi-week diff compares. */
export const COMPARE_NEXT_N_MAX = 5;

function read(key: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Quota or privacy-mode block: the preference just doesn't persist.
  }
}

// On/off flags are stored as "on" / "off"; anything else reads as off.
const readFlag = (key: string): boolean => read(key) === 'on';
const writeFlag = (key: string, on: boolean): void => write(key, on ? 'on' : 'off');

export const readLivePreview = (slug: string) => readFlag(prefKeys.livePreview(slug));
export const writeLivePreview = (slug: string, on: boolean) => writeFlag(prefKeys.livePreview(slug), on);

/** The preview's list: the week's songs unless slots were chosen. */
export type PreviewTab = 'songs' | 'slots';
export const readPreviewTab = (slug: string): PreviewTab =>
  read(prefKeys.previewTab(slug)) === 'slots' ? 'slots' : 'songs';
export const writePreviewTab = (slug: string, tab: PreviewTab) => write(prefKeys.previewTab(slug), tab);

export const readMacroPalette = (slug: string) => readFlag(prefKeys.macroPalette(slug));
export const writeMacroPalette = (slug: string, on: boolean) => writeFlag(prefKeys.macroPalette(slug), on);

export const readShowDiff = (slug: string) => readFlag(prefKeys.showDiff(slug));
export const writeShowDiff = (slug: string, on: boolean) => writeFlag(prefKeys.showDiff(slug), on);

/** The preview week: 1 when unset, unparseable, below 1 or not finite
 *  (the server's strict PreviewRequest.week is the real bound). */
export function readPreviewWeek(slug: string): number {
  const raw = read(prefKeys.previewWeek(slug));
  if (raw === null) return 1;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.floor(n);
}

export const writePreviewWeek = (slug: string, week: number) =>
  write(prefKeys.previewWeek(slug), String(week));

/** Parse a stored compare-next-N value: 0 when null, unparseable or
 *  negative; at most COMPARE_NEXT_N_MAX. Shared with the cross-tab
 *  `storage` handler, which gets the raw string from the event. */
export function parseCompareNextN(raw: string | null): number {
  if (raw === null) return 0;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.min(COMPARE_NEXT_N_MAX, Math.floor(n));
}

export const readCompareNextN = (slug: string) => parseCompareNextN(read(prefKeys.compareNextN(slug)));
export const writeCompareNextN = (slug: string, n: number) =>
  write(prefKeys.compareNextN(slug), String(n));
