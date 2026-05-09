import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import StationForm from './StationForm.svelte';

/**
 * Edit-mode behaviour pinned at the component level.
 *
 * The wizard's create-mode is already covered by `wizard.test.ts` (the
 * route's existing 5 tests still pass post-extraction). This file covers
 * the edit-mode branches that those tests don't reach: slug readonly,
 * initial template unpacking, the reset button, and the diff disclosure
 * flipping clean/dirty as the user types.
 */
describe('<StationForm mode="edit" /> — §16.7 edit-mode', () => {
  beforeEach(() => {
    cleanup();
  });

  // Meta fixture — matches what GET /api/meta returns. Fixed values
  // (rather than fetching) so tests don't depend on the cache state.
  const meta = {
    strategies: ['pick', 'window'],
    rng: ['off', 'on'],
    roles: ['content', 'reference'],
    cadences: ['weekly'],
  };

  const initial = {
    slug: 'cls',
    name: 'Classics',
    template: {
      station: 'cls',
      cadence: 'weekly',
      rng: 'off',
      sources: [{ name: 'A', colour: '#c0392b', role: 'content' }],
      cursor: { base_advance_per_week: 60, cycle_weeks: 16 },
      layout: {
        unit: 'day',
        unit_labels: ['Mon', 'Tue'],
        row_start_offsets: [0, 10],
        row_template: [{ source: 'A', strategy: 'window', size: 215 }],
      },
    },
  };

  it('renders slug input as readonly with the immutability hint', () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });

    const slugInput = screen.getByPlaceholderText('rebirth-classics') as HTMLInputElement;
    expect(slugInput).toHaveAttribute('readonly');
    expect(slugInput.value).toBe('cls');
    expect(screen.getByText(/slug is immutable/i)).toBeInTheDocument();
  });

  it('populates display name + cursor + layout fields from initial', () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });

    expect(
      (screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement).value,
    ).toBe('Classics');
    // Cursor numbers come back as the unpacker put them.
    const cursorInputs = screen.getAllByRole('spinbutton') as HTMLInputElement[];
    const baseAdvance = cursorInputs.find((i) => i.value === '60');
    expect(baseAdvance).toBeTruthy();
    // Layout strings — the unpacker joined arrays with ", ".
    expect(
      (screen.getByPlaceholderText('Mon, Tue, Wed, Thu, Fri, Sat, Sun') as HTMLInputElement).value,
    ).toBe('Mon, Tue');
  });

  it('shows "no changes" pill until the user edits a field, then "unsaved"', async () => {
    const { container } = render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });

    // Target the pill specifically — the body has "no changes" prose too.
    const cleanPill = container.querySelector('.clean-pill');
    expect(cleanPill).toBeTruthy();
    expect(cleanPill?.textContent?.toLowerCase()).toContain('no changes');

    const nameInput = screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'Classics REISSUE' } });

    // After typing, the pill flips to dirty/unsaved.
    expect(container.querySelector('.clean-pill')).toBeNull();
    const dirtyPill = container.querySelector('.dirty-pill');
    expect(dirtyPill?.textContent?.toLowerCase()).toContain('unsaved');
  });

  it('"Reset to saved" button is disabled when clean and enabled when dirty', async () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });

    const resetBtn = screen.getByTestId('reset-to-saved') as HTMLButtonElement;
    expect(resetBtn).toBeDisabled();

    const nameInput = screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'tweaked' } });
    expect(resetBtn).not.toBeDisabled();
  });

  it('"Reset to saved" reverts the form when the user accepts the confirm', async () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    const nameInput = screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'tweaked' } });
    expect(nameInput.value).toBe('tweaked');

    await fireEvent.click(screen.getByTestId('reset-to-saved'));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringMatching(/discard changes/i));
    expect(nameInput.value).toBe('Classics');

    confirmSpy.mockRestore();
  });

  it('"Reset to saved" leaves the form untouched if the user cancels', async () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    const nameInput = screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'tweaked' } });
    await fireEvent.click(screen.getByTestId('reset-to-saved'));

    expect(nameInput.value).toBe('tweaked');
    confirmSpy.mockRestore();
  });

  it('submit calls onSubmit with the slug-from-initial + edited fields', async () => {
    const onSubmit = vi.fn().mockResolvedValueOnce(undefined);
    render(StationForm, { mode: 'edit', initial, onSubmit, meta });

    const nameInput = screen.getByPlaceholderText('Rebirth Classics') as HTMLInputElement;
    await fireEvent.input(nameInput, { target: { value: 'Classics REISSUE' } });
    await fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledOnce());
    const payload = onSubmit.mock.calls[0][0];
    expect(payload.slug).toBe('cls'); // immutable — comes from initial
    expect(payload.name).toBe('Classics REISSUE');
    expect(payload.template.cursor.base_advance_per_week).toBe(60);
  });

  it('shows the JSON preview disclosure with a parseable JSON body', async () => {
    render(StationForm, { mode: 'edit', initial, onSubmit: vi.fn(), meta });

    const preview = screen.getByTestId('json-preview');
    expect(preview).toBeInTheDocument();
    // The <pre> child renders the buildTemplate() output. Check that
    // it parses and contains the slug — proves the preview reactives
    // wired up rather than asserting on whitespace.
    const pre = preview.querySelector('pre');
    expect(pre).toBeTruthy();
    const parsed = JSON.parse(pre!.textContent ?? '');
    expect(parsed.station).toBe('cls');
    expect(parsed.cadence).toBe('weekly');
  });

  it('round-trip: unpack(buildTemplate(state)) ≈ identity (S3 anti-divergence)', async () => {
    // The §16.7 review S3 finding: unpack() and buildTemplate() are an
    // inverse pair; if a future contributor adds a field to one but
    // forgets the other, edit mode silently drops/duplicates fields on
    // every save. This test catches that whole class of regression by
    // round-tripping a representative form state through both functions.
    //
    // Strategy: render in edit mode with a complex initial template,
    // submit immediately (no edits) to capture buildTemplate's output,
    // re-render with that output as the new initial, submit again. The
    // two payloads must be deep-equal — any field unpack misses on the
    // second render won't survive the second buildTemplate.
    const complexInitial = {
      slug: 'roundtrip',
      name: 'Round Trip',
      template: {
        station: 'roundtrip',
        cadence: 'weekly',
        rng: 'on',
        seed: 12345,
        sources: [
          { name: 'A', colour: '#c0392b', role: 'content' },
          { name: 'B', colour: '#8e44ad', role: 'content' },
          { name: 'C', colour: '#7f8c8d', role: 'reference' },
        ],
        cursor: { base_advance_per_week: 60, cycle_weeks: 16 },
        layout: {
          unit: 'day',
          unit_labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
          row_start_offsets: [0, 10, 20, 30, 40, 50, 50],
          row_template: [
            { source: 'A', strategy: 'window', size: 215 },
            { source: 'B', strategy: 'window', size: 215 },
          ],
        },
      },
    };

    // First render: capture buildTemplate output.
    const onSubmit1 = vi.fn().mockResolvedValueOnce(undefined);
    const { unmount } = render(StationForm, {
      mode: 'edit',
      initial: complexInitial,
      onSubmit: onSubmit1,
      meta,
    });
    await fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await vi.waitFor(() => expect(onSubmit1).toHaveBeenCalledOnce());
    const firstPayload = onSubmit1.mock.calls[0][0];
    unmount();

    // Second render: feed that output back as `initial`, capture again.
    const onSubmit2 = vi.fn().mockResolvedValueOnce(undefined);
    render(StationForm, { mode: 'edit', initial: firstPayload, onSubmit: onSubmit2, meta });
    await fireEvent.click(screen.getByRole('button', { name: /save changes/i }));
    await vi.waitFor(() => expect(onSubmit2).toHaveBeenCalledOnce());
    const secondPayload = onSubmit2.mock.calls[0][0];

    // The two payloads must be deep-equal. If unpack() ever drops a
    // field, the second payload won't have it.
    expect(secondPayload).toEqual(firstPayload);
  });
});
