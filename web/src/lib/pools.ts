// Wording for the imported-pools table and the import banner
// (PoolImportPanel.svelte).
import type { ImportSummary } from '$lib/api';

/**
 * The banner after an import. The count is the pool's entries, the same
 * number the table's Tracks column shows; when a playlist lists some
 * songs more than once, how many different songs it holds is added.
 */
export function importedMessage(summary: ImportSummary): string {
  const entries = summary.entries_imported;
  const distinct = summary.tracks_imported;
  const count = `${entries.toLocaleString()} track${entries === 1 ? '' : 's'}`;
  const repeats = distinct < entries ? `, ${distinct.toLocaleString()} different` : '';
  return `Imported "${summary.name}" — ${count}${repeats} (${summary.role}).`;
}

/** "Sun, 25 Oct 2026, 15:30" in the viewer's own time zone and language. */
export function formatImportedAt(iso: string, locale?: string, timeZone?: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone,
  });
}
