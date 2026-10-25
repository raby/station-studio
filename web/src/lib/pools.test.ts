import { describe, it, expect } from 'vitest';
import { formatImportedAt, importedMessage } from './pools';

describe('importedMessage', () => {
  it('counts the pool the way the table does', () => {
    expect(importedMessage({ name: 'TINY', role: 'content', tracks_imported: 3, entries_imported: 3 })).toBe(
      'Imported "TINY" — 3 tracks (content).',
    );
    expect(importedMessage({ name: 'ONE', role: 'reference', tracks_imported: 1, entries_imported: 1 })).toBe(
      'Imported "ONE" — 1 track (reference).',
    );
  });

  it('says how many are different when songs repeat', () => {
    expect(importedMessage({ name: 'NIGHT', role: 'content', tracks_imported: 1285, entries_imported: 1884 })).toBe(
      'Imported "NIGHT" — 1,884 tracks, 1,285 different (content).',
    );
  });
});

describe('formatImportedAt', () => {
  it('shows the date and time in the given zone', () => {
    expect(formatImportedAt('2026-10-25T14:30:00Z', 'en-GB', 'Europe/London')).toBe('Sun, 25 Oct 2026, 14:30');
    expect(formatImportedAt('2026-07-05T14:30:00Z', 'en-GB', 'Europe/London')).toBe('Sun, 5 Jul 2026, 15:30');
  });

  it('is empty for a bad date', () => {
    expect(formatImportedAt('not a date')).toBe('');
  });
});
