// Words for Station Server's answers (api/routers/stationserver.py), shared
// by the station's page and the week's page.
import type { StationServerSent } from '$lib/api';

/** `013.xml` -> 13; anything else -> null. */
export function weekNo(file: string | null | undefined): number | null {
  const m = file ? /^(\d+)\.xml$/.exec(file) : null;
  return m ? Number(m[1]) : null;
}

/** A local time like "Mon 2 Nov, 00:00". */
export function shortTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

/** Station Server's answer to a week sent, as a sentence: the tracks it
 *  found on the station's computer, and when the week plays. */
export function sentText(r: StationServerSent): string {
  const found = `${r.tracks.toLocaleString()} track${r.tracks === 1 ? '' : 's'} found on the station's computer`;
  const missing = r.missing_count
    ? `, ${r.missing_count.toLocaleString()} not found (${r.missing
        .slice(0, 3)
        .map((m) => [m.artist, m.title].filter(Boolean).join(' – '))
        .join('; ')}${r.missing_count > 3 ? '…' : ''})`
    : '';
  const on =
    r.when === 'now'
      ? r.replaced ? 'It replaced the week on air.' : 'It plays now.'
      : r.when === 'next'
        ? `It goes on air ${r.goes_on ? shortTime(r.goes_on) : 'next week'}.`
        : r.when === 'later'
          ? 'It waits for its week.'
          : "It's older than the week on air there, so it won't play.";
  return `${r.station}, week ${weekNo(r.week) ?? r.week}: ${found}${missing}. ${on}`;
}
