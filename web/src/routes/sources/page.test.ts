import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

/**
 * §16.25 c2 — /sources page tests.
 *
 * Mocks the four api.ts source helpers (listSources / uploadSource /
 * importSourceByPath / deleteSource) via vi.hoisted so the page's
 * onMount + form submit drive them without a real backend.
 */
const {
  listSourcesMock,
  uploadSourceMock,
  importSourceByPathMock,
  FakeApiError,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  return {
    listSourcesMock: vi.fn(),
    uploadSourceMock: vi.fn(),
    importSourceByPathMock: vi.fn(),
    FakeApiError,
  };
});

vi.mock('$lib/api', () => ({
  ApiError: FakeApiError,
  listSources: (...args: unknown[]) => listSourcesMock(...args),
  uploadSource: (...args: unknown[]) => uploadSourceMock(...args),
  importSourceByPath: (...args: unknown[]) => importSourceByPathMock(...args),
}));

// Load AFTER vi.mock so the mocked module is what the page sees.
const Page = (await import('./+page.svelte')).default;

const tinyFile = () =>
  new File(['<plist></plist>'], 'tiny.xml', { type: 'application/xml' });

beforeEach(() => {
  listSourcesMock.mockReset();
  uploadSourceMock.mockReset();
  importSourceByPathMock.mockReset();
  // Default: empty list (page renders empty state).
  listSourcesMock.mockResolvedValue([]);
});


describe('/sources — initial render', () => {
  it('shows the empty state when no pools are imported', async () => {
    render(Page);
    await waitFor(() => expect(listSourcesMock).toHaveBeenCalled());
    expect(await screen.findByTestId('empty-state')).toBeInTheDocument();
    expect(screen.queryByTestId('sources-list')).not.toBeInTheDocument();
  });

  it('renders one row per pool with name, role tag, track count', async () => {
    listSourcesMock.mockResolvedValue([
      { id: 1, name: 'DAYTIME', role: 'content', track_count: 247 },
      { id: 2, name: 'MASTER', role: 'reference', track_count: 8421 },
    ]);
    render(Page);
    const rows = await screen.findAllByTestId('pool-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute('data-pool-name', 'DAYTIME');
    expect(rows[1]).toHaveAttribute('data-pool-name', 'MASTER');
    // The track count is formatted with locale separators (8,421).
    expect(rows[1]).toHaveTextContent('8,421');
    // Role tags render with the role text.
    expect(rows[0]).toHaveTextContent('content');
    expect(rows[1]).toHaveTextContent('reference');
  });

  it('surfaces a list error if listSources rejects', async () => {
    listSourcesMock.mockRejectedValueOnce(new FakeApiError(500, 'boom'));
    render(Page);
    expect(await screen.findByTestId('list-error')).toHaveTextContent('boom');
  });
});


describe('/sources — add-pool form (upload mode, the D1 primary path)', () => {
  it('uploads the file with trimmed name + role, then refreshes the list', async () => {
    // First listSources: empty. Second (after submit): one pool.
    listSourcesMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { id: 1, name: 'TINY', role: 'content', track_count: 3 },
      ]);
    uploadSourceMock.mockResolvedValue({
      name: 'TINY',
      role: 'content',
      tracks_imported: 3,
      entries_imported: 3,
    });

    render(Page);
    await screen.findByTestId('add-pool-form');

    // Fill the form — note the surrounding whitespace gets stripped
    // client-side before the helper is called (mirrors the server-side
    // _validate_name guard from c1).
    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: '  TINY  ' },
    });
    await fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [tinyFile()] },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    await waitFor(() => expect(uploadSourceMock).toHaveBeenCalledTimes(1));
    const [name, role, file] = uploadSourceMock.mock.calls[0];
    expect(name).toBe('TINY');
    expect(role).toBe('content');
    expect((file as File).name).toBe('tiny.xml');

    // Success banner reports the import summary.
    const banner = await screen.findByTestId('success-banner');
    expect(banner).toHaveTextContent(/Imported "TINY".*3 tracks/);

    // List refreshed — the new row is visible.
    const row = await screen.findByTestId('pool-row');
    expect(row).toHaveAttribute('data-pool-name', 'TINY');
  });

  it('rejects empty name client-side without calling uploadSource', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [tinyFile()] },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/name is required/i);
    expect(uploadSourceMock).not.toHaveBeenCalled();
  });

  it('rejects missing file client-side without calling uploadSource', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'NO_FILE' },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/pick.*xml file/i);
    expect(uploadSourceMock).not.toHaveBeenCalled();
  });

  it('clears the file input after a successful upload (c2-review 🟡-1)', async () => {
    // Locks the post-submit DOM clear: `bind:files` is one-way, so
    // setting `fileList = null` alone leaves the underlying
    // <input type=file> still showing the chosen filename. The page
    // clears it via a `bind:this`-bound element ref. Before the fix,
    // a global `document.querySelector` did the same thing but was
    // brittle to test-id renames + the conditional-render collapse.
    listSourcesMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        { id: 1, name: 'TINY', role: 'content', track_count: 3 },
      ]);
    uploadSourceMock.mockResolvedValue({
      name: 'TINY',
      role: 'content',
      tracks_imported: 3,
      entries_imported: 3,
    });

    render(Page);
    const fileInput = (await screen.findByTestId('file-input')) as HTMLInputElement;

    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'TINY' },
    });
    await fireEvent.change(fileInput, { target: { files: [tinyFile()] } });
    // Sanity — the input now holds a file (we just picked one).
    expect(fileInput.files?.length).toBe(1);

    await fireEvent.submit(screen.getByTestId('add-pool-form'));
    await screen.findByTestId('success-banner');

    // Post-submit: the DOM input's visible value is cleared (real
    // browsers ALSO clear `.files` when `.value=''` is set, but JSDOM
    // doesn't fully — checking `.value` catches what the user sees).
    expect(fileInput.value).toBe('');

    // The deeper behavioural proof: a second submit without picking
    // a new file now correctly fails validation, because the page's
    // `fileList` state was also cleared. Before the fix, the DOM input
    // would have *visually* still shown the prior file (broken
    // querySelector lookup), creating a confusing UX where Submit
    // re-imported the same XML silently.
    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'AGAIN' },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));
    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/pick.*xml file/i);
  });

  it('surfaces server errors (e.g. 500 malformed XML) inline', async () => {
    uploadSourceMock.mockRejectedValueOnce(
      new FakeApiError(500, 'import failed: malformed plist'),
    );

    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'BROKEN' },
    });
    await fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [tinyFile()] },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/malformed plist/);
    expect(screen.queryByTestId('success-banner')).not.toBeInTheDocument();
  });
});


describe('/sources — advanced path mode (the D1 secondary path)', () => {
  it('toggling to path mode hides the file input and shows path input', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    expect(screen.getByTestId('file-input')).toBeInTheDocument();
    expect(screen.queryByTestId('path-input')).not.toBeInTheDocument();

    await fireEvent.click(screen.getByTestId('mode-toggle-path'));

    expect(screen.queryByTestId('file-input')).not.toBeInTheDocument();
    expect(screen.getByTestId('path-input')).toBeInTheDocument();
  });

  it('submits via importSourceByPath when in path mode', async () => {
    importSourceByPathMock.mockResolvedValue({
      name: 'P',
      role: 'reference',
      tracks_imported: 12,
      entries_imported: 12,
    });

    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.click(screen.getByTestId('mode-toggle-path'));
    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'P' },
    });
    // Role select to reference — verifies role flows through.
    await fireEvent.change(screen.getByTestId('role-select'), {
      target: { value: 'reference' },
    });
    await fireEvent.input(screen.getByTestId('path-input'), {
      target: { value: '/abs/path/to/lib.xml' },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    await waitFor(() => expect(importSourceByPathMock).toHaveBeenCalledTimes(1));
    expect(importSourceByPathMock).toHaveBeenCalledWith(
      'P',
      'reference',
      '/abs/path/to/lib.xml',
    );
    // Upload helper must NOT have been called.
    expect(uploadSourceMock).not.toHaveBeenCalled();
  });

  it('rejects empty path client-side in path mode', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.click(screen.getByTestId('mode-toggle-path'));
    await fireEvent.input(screen.getByTestId('name-input'), {
      target: { value: 'X' },
    });
    await fireEvent.submit(screen.getByTestId('add-pool-form'));

    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/type the server-side path/i);
    expect(importSourceByPathMock).not.toHaveBeenCalled();
  });
});


// ---- §16.25 c3 — batch (multi-pool) mode ---------------------------


describe('/sources — batch mode (c3 D2 multi-pool)', () => {
  it('toggling into batch mode seeds one empty row + hides single form', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    // Single form visible by default.
    expect(screen.queryByTestId('batch-form')).not.toBeInTheDocument();

    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));

    expect(screen.queryByTestId('add-pool-form')).not.toBeInTheDocument();
    expect(screen.getByTestId('batch-form')).toBeInTheDocument();
    // One empty row, status 'pending'.
    const rows = screen.getAllByTestId('batch-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute('data-row-status', 'pending');
  });

  it('Back to single mode restores the single form and drops rows', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');

    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));
    await fireEvent.click(screen.getByTestId('batch-add-row'));
    expect(screen.getAllByTestId('batch-row')).toHaveLength(2);

    await fireEvent.click(screen.getByTestId('single-mode-toggle'));

    expect(screen.queryByTestId('batch-form')).not.toBeInTheDocument();
    expect(screen.getByTestId('add-pool-form')).toBeInTheDocument();
  });

  it('+ Add another row appends; remove drops it; never goes below 1', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));

    await fireEvent.click(screen.getByTestId('batch-add-row'));
    await fireEvent.click(screen.getByTestId('batch-add-row'));
    expect(screen.getAllByTestId('batch-row')).toHaveLength(3);

    // Remove the first row.
    const removeButtons = screen.getAllByTestId('batch-row-remove');
    await fireEvent.click(removeButtons[0]);
    expect(screen.getAllByTestId('batch-row')).toHaveLength(2);

    // Removing both remaining rows must NOT leave us row-less —
    // the page re-seeds with one empty row.
    await fireEvent.click(screen.getAllByTestId('batch-row-remove')[0]);
    await fireEvent.click(screen.getAllByTestId('batch-row-remove')[0]);
    expect(screen.getAllByTestId('batch-row')).toHaveLength(1);
  });

  it('submits all rows sequentially (G3) on the happy path, then resets', async () => {
    listSourcesMock
      .mockResolvedValueOnce([]) // initial
      .mockResolvedValueOnce([
        { id: 1, name: 'A', role: 'content', track_count: 1 },
        { id: 2, name: 'B', role: 'content', track_count: 1 },
      ]);

    // Track call order to prove sequential submission.
    const callOrder: string[] = [];
    uploadSourceMock.mockImplementation(async (name: string) => {
      callOrder.push(`start:${name}`);
      // Tiny delay so parallel uploads (if they happened) would
      // interleave the start/end markers — sequential ensures
      // start:A → end:A → start:B → end:B.
      await new Promise((r) => setTimeout(r, 0));
      callOrder.push(`end:${name}`);
      return {
        name,
        role: 'content',
        tracks_imported: 1,
        entries_imported: 1,
      };
    });

    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));
    await fireEvent.click(screen.getByTestId('batch-add-row'));

    // Two rows: A and B.
    const nameInputs = screen.getAllByTestId('batch-row-name');
    const fileInputs = screen.getAllByTestId('batch-row-file');
    await fireEvent.input(nameInputs[0], { target: { value: 'A' } });
    await fireEvent.change(fileInputs[0], { target: { files: [tinyFile()] } });
    await fireEvent.input(nameInputs[1], { target: { value: 'B' } });
    await fireEvent.change(fileInputs[1], { target: { files: [tinyFile()] } });

    await fireEvent.submit(screen.getByTestId('batch-form'));

    const banner = await screen.findByTestId('success-banner');
    expect(banner).toHaveTextContent(/Imported all 2 pools/);

    // G3 — submissions did not interleave.
    expect(callOrder).toEqual(['start:A', 'end:A', 'start:B', 'end:B']);

    // Form reset to one empty row on full success.
    expect(screen.getAllByTestId('batch-row')).toHaveLength(1);
    expect(screen.getAllByTestId('batch-row-name')[0]).toHaveValue('');

    // Pool list refreshed with the two new rows.
    const listRows = await screen.findAllByTestId('pool-row');
    expect(listRows).toHaveLength(2);
  });

  it('partial failure: keeps failed rows visible with per-row error, drops succeeded', async () => {
    listSourcesMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([{ id: 1, name: 'GOOD', role: 'content', track_count: 3 }]);

    // GOOD succeeds, BAD fails with a 500.
    uploadSourceMock
      .mockResolvedValueOnce({
        name: 'GOOD',
        role: 'content',
        tracks_imported: 3,
        entries_imported: 3,
      })
      .mockRejectedValueOnce(new FakeApiError(500, 'import failed: malformed plist'));

    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));
    await fireEvent.click(screen.getByTestId('batch-add-row'));

    const nameInputs = screen.getAllByTestId('batch-row-name');
    const fileInputs = screen.getAllByTestId('batch-row-file');
    await fireEvent.input(nameInputs[0], { target: { value: 'GOOD' } });
    await fireEvent.change(fileInputs[0], { target: { files: [tinyFile()] } });
    await fireEvent.input(nameInputs[1], { target: { value: 'BAD' } });
    await fireEvent.change(fileInputs[1], { target: { files: [tinyFile()] } });

    await fireEvent.submit(screen.getByTestId('batch-form'));

    // Form-level error summary mentions the partial split.
    const err = await screen.findByTestId('form-error');
    expect(err).toHaveTextContent(/Imported 1 of 2.*1 failed/);

    // Succeeded row dropped; failed row stays visible with its error.
    // (Identify "this is BAD's row" via the input's .value rather than
    // textContent — input values don't surface in textContent.)
    const remaining = screen.getAllByTestId('batch-row');
    expect(remaining).toHaveLength(1);
    expect(remaining[0]).toHaveAttribute('data-row-status', 'failed');
    expect(screen.getByTestId('batch-row-name')).toHaveValue('BAD');
    expect(screen.getByTestId('batch-row-error')).toHaveTextContent(
      /malformed plist/,
    );

    // Pool list refreshed — the succeeded pool is there.
    const listRows = await screen.findAllByTestId('pool-row');
    expect(listRows).toHaveLength(1);
    expect(listRows[0]).toHaveAttribute('data-pool-name', 'GOOD');
  });

  it('pre-flight validation marks empty-name rows failed without API call', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));
    await fireEvent.click(screen.getByTestId('batch-add-row'));

    // Row 0: valid; row 1: empty name.
    const nameInputs = screen.getAllByTestId('batch-row-name');
    const fileInputs = screen.getAllByTestId('batch-row-file');
    await fireEvent.input(nameInputs[0], { target: { value: 'OK' } });
    await fireEvent.change(fileInputs[0], { target: { files: [tinyFile()] } });
    // Leave nameInputs[1] empty.
    await fireEvent.change(fileInputs[1], { target: { files: [tinyFile()] } });

    uploadSourceMock.mockResolvedValue({
      name: 'OK',
      role: 'content',
      tracks_imported: 1,
      entries_imported: 1,
    });
    listSourcesMock.mockResolvedValueOnce([
      { id: 1, name: 'OK', role: 'content', track_count: 1 },
    ]);

    await fireEvent.submit(screen.getByTestId('batch-form'));

    await screen.findByTestId('form-error'); // partial-failure banner
    // The invalid row was NOT submitted to the API.
    expect(uploadSourceMock).toHaveBeenCalledTimes(1);
    expect(uploadSourceMock.mock.calls[0][0]).toBe('OK');

    // The failed row stays + carries the inline error.
    const failedRow = screen.getByTestId('batch-row');
    expect(failedRow).toHaveAttribute('data-row-status', 'failed');
    expect(screen.getByTestId('batch-row-error')).toHaveTextContent(
      /name is required/i,
    );
  });

  it('batch path mode submits via importSourceByPath (not uploadSource)', async () => {
    importSourceByPathMock.mockResolvedValue({
      name: 'P',
      role: 'reference',
      tracks_imported: 2,
      entries_imported: 2,
    });
    listSourcesMock.mockResolvedValueOnce([
      { id: 1, name: 'P', role: 'reference', track_count: 2 },
    ]);

    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('mode-toggle-path'));
    await fireEvent.click(screen.getByTestId('batch-mode-toggle'));

    const nameInput = screen.getByTestId('batch-row-name');
    const pathInput = screen.getByTestId('batch-row-path');
    const roleSelect = screen.getByTestId('batch-row-role');
    await fireEvent.input(nameInput, { target: { value: 'P' } });
    await fireEvent.change(roleSelect, { target: { value: 'reference' } });
    await fireEvent.input(pathInput, { target: { value: '/abs/p.xml' } });

    await fireEvent.submit(screen.getByTestId('batch-form'));

    await screen.findByTestId('success-banner');
    expect(importSourceByPathMock).toHaveBeenCalledWith('P', 'reference', '/abs/p.xml');
    expect(uploadSourceMock).not.toHaveBeenCalled();
  });
});


describe('/sources — iTunes export hint (G5)', () => {
  it('shows the iTunes export hint in upload mode (the new-user help)', async () => {
    render(Page);
    const hint = await screen.findByTestId('itunes-hint');
    expect(hint).toHaveTextContent(/Music \(macOS\)/);
    expect(hint).toHaveTextContent(/Export Playlist/);
  });

  it('hides the iTunes hint in path mode (irrelevant when typing a path)', async () => {
    render(Page);
    await screen.findByTestId('add-pool-form');
    await fireEvent.click(screen.getByTestId('mode-toggle-path'));
    expect(screen.queryByTestId('itunes-hint')).not.toBeInTheDocument();
  });
});
