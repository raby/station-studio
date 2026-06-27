import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// --- mocks -------------------------------------------------------------
// $app/navigation, $lib/api and $lib/meta need stubbing because the wizard's
// submit handler calls them. We replace them at module-mock level so the
// wizard renders as a plain component under jsdom.

// vi.mock factories are hoisted above imports — any values they reference
// must be declared via vi.hoisted so they're available at hoist time.
const { gotoMock, createStationMock, getMetaMock, loadMetaMock, FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    body?: unknown;
    constructor(status: number, message: string, body?: unknown) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.body = body;
    }
  }
  // Standard meta fixture mirroring `GET /api/meta` (DESIGN.md §14.19).
  // loadMeta returns a Promise; tests that need failure paths can replace
  // via .mockRejectedValueOnce.
  const META = {
    strategies: ['pick', 'window'],
    rng: ['off', 'on'],
    roles: ['content', 'reference'],
    cadences: ['weekly'],
  };
  const loadMetaMock = vi.fn().mockResolvedValue(META);
  const getMetaMock = vi.fn().mockResolvedValue(META);
  return {
    gotoMock: vi.fn(),
    createStationMock: vi.fn(),
    getMetaMock,
    loadMetaMock,
    FakeApiError,
  };
});

vi.mock('$app/navigation', () => ({
  goto: (...args: unknown[]) => gotoMock(...args),
}));
vi.mock('$lib/api', () => ({
  createStation: (...args: unknown[]) => createStationMock(...args),
  getMeta: (...args: unknown[]) => getMetaMock(...args),
  ApiError: FakeApiError,
  // §16.5 c3 — type guard mirroring the real impl. Updated post-
  // c3-review R2 to also enforce per-item {field, message} shape
  // so the mocked behaviour matches what production sees.
  isTemplateErrorBody: (b: unknown): boolean => {
    if (typeof b !== 'object' || b === null) return false;
    const o = b as Record<string, unknown>;
    if (typeof o.detail !== 'string' || !Array.isArray(o.errors)) return false;
    return o.errors.every(
      (e: unknown) =>
        e !== null &&
        typeof e === 'object' &&
        (typeof (e as { field?: unknown }).field === 'string' ||
          (e as { field?: unknown }).field === null) &&
        typeof (e as { message?: unknown }).message === 'string',
    );
  },
}));
vi.mock('$lib/meta', () => ({
  loadMeta: (...args: unknown[]) => loadMetaMock(...args),
  _resetMetaCache: vi.fn(),
}));

// Import after the mocks are registered.
import Page from './+page.svelte';

/**
 * These tests cover the client-side validation branches of the wizard.
 * In Playwright these were flaky (the `error = ...` assignment in a handler
 * competes with Svelte's reactivity); here under jsdom + testing-library
 * they're deterministic.
 *
 * The happy-path (fill + submit → createStation + goto) is exercised here
 * as well, as a fast alternative to the Playwright equivalent.
 */
describe('station wizard (new +page.svelte)', () => {
  beforeEach(() => {
    gotoMock.mockReset();
    createStationMock.mockReset();
  });
  afterEach(() => cleanup());

  it('shows "slug is required" when submitting with an empty slug', async () => {
    render(Page);
    // Form renders only after loadMeta() resolves (§14.19); wait for the
    // submit button before interacting. Same pattern in the cases below
    // — first findBy waits, subsequent gets work synchronously.
    await fireEvent.click(
      await screen.findByRole('button', { name: /create station/i }),
    );
    expect(await screen.findByText(/slug is required/i)).toBeInTheDocument();
    expect(createStationMock).not.toHaveBeenCalled();
  });

  it('rejects slugs with uppercase / spaces before hitting the API', async () => {
    render(Page);
    const slug = await screen.findByPlaceholderText('rebirth-classics');
    await fireEvent.input(slug, { target: { value: 'Bad Slug' } });
    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));
    expect(
      await screen.findByText(/slug must be lowercase, digits, or dashes/i),
    ).toBeInTheDocument();
    expect(createStationMock).not.toHaveBeenCalled();
  });

  it('requires at least one content source with a name', async () => {
    render(Page);
    await fireEvent.input(
      await screen.findByPlaceholderText('rebirth-classics'),
      { target: { value: 'good-slug' } },
    );
    await fireEvent.input(screen.getByPlaceholderText('Rebirth Classics'), {
      target: { value: 'Good Name' },
    });
    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));
    // The single default source row has an empty Name input, so the
    // 'all sources need a name' branch fires before 'content source is required'.
    expect(await screen.findByText(/all sources need a name/i)).toBeInTheDocument();
    expect(createStationMock).not.toHaveBeenCalled();
  });

  it('on a valid form, calls createStation then navigates to the detail page', async () => {
    createStationMock.mockResolvedValueOnce({ id: 1, slug: 'ok', name: 'OK', template: {} });

    render(Page);
    await fireEvent.input(
      await screen.findByPlaceholderText('rebirth-classics'),
      { target: { value: 'ok' } },
    );
    await fireEvent.input(screen.getByPlaceholderText('Rebirth Classics'), {
      target: { value: 'OK' },
    });
    // Fill the first source's name — the reactive $contentSourceNames
    // picks it up, and the default row-template block points at it.
    const sourceNameInput = screen.getByPlaceholderText('RG_RADIO');
    await fireEvent.input(sourceNameInput, { target: { value: 'A' } });

    // Collapse layout to a single row so the row_start_offsets length
    // matches unit_labels (the defaults both have 7 slots, which is also
    // fine, but 1 makes the intent obvious).
    const unitLabels = screen.getByPlaceholderText('Mon, Tue, Wed, Thu, Fri, Sat, Sun');
    await fireEvent.input(unitLabels, { target: { value: 'Mon' } });
    const offsets = screen.getByPlaceholderText('0, 10, 20, 30, 40, 50, 50');
    await fireEvent.input(offsets, { target: { value: '0' } });

    // Select the 'A' option in the block source dropdown. The select
    // carries data-testid="block-source" specifically so tests don't have
    // to enumerate <select> elements and match by option contents.
    const blockSource = screen.getByTestId('block-source') as HTMLSelectElement;
    await fireEvent.change(blockSource, { target: { value: 'A' } });

    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));

    // createStation was called with the right shape, and goto fired.
    await vi.waitFor(() => expect(createStationMock).toHaveBeenCalledOnce());
    const payload = createStationMock.mock.calls[0][0];
    expect(payload.slug).toBe('ok');
    expect(payload.name).toBe('OK');
    expect(payload.template.sources[0].name).toBe('A');
    expect(payload.template.layout.row_template[0].source).toBe('A');

    await vi.waitFor(() => expect(gotoMock).toHaveBeenCalledWith('/stations/ok'));
  });

  it('surfaces an ApiError message from the server without navigating', async () => {
    createStationMock.mockRejectedValueOnce(
      new FakeApiError(400, '400 Bad Request: invalid template: bad'),
    );

    render(Page);
    await fireEvent.input(
      await screen.findByPlaceholderText('rebirth-classics'),
      { target: { value: 'ok' } },
    );
    await fireEvent.input(screen.getByPlaceholderText('Rebirth Classics'), {
      target: { value: 'OK' },
    });
    await fireEvent.input(screen.getByPlaceholderText('RG_RADIO'), {
      target: { value: 'A' },
    });
    await fireEvent.input(
      screen.getByPlaceholderText('Mon, Tue, Wed, Thu, Fri, Sat, Sun'),
      { target: { value: 'Mon' } },
    );
    await fireEvent.input(
      screen.getByPlaceholderText('0, 10, 20, 30, 40, 50, 50'),
      { target: { value: '0' } },
    );
    const blockSource = screen.getByTestId('block-source') as HTMLSelectElement;
    await fireEvent.change(blockSource, { target: { value: 'A' } });

    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));

    expect(await screen.findByText(/invalid template: bad/i)).toBeInTheDocument();
    expect(gotoMock).not.toHaveBeenCalled();
  });

  it('§16.5 c3 — c0 structured error tags rng field with aria-describedby', async () => {
    // The c0 backend envelope `{detail, errors: [{field, message}]}`
    // gets parsed by the wizard catch block; tagged fields render
    // an inline <p id="X-error"> and the input picks up
    // `aria-invalid="true"` + `aria-describedby="X-error"`. Pins
    // the backend→frontend contract for screen-reader error
    // association.
    createStationMock.mockRejectedValueOnce(
      new FakeApiError(
        400,
        '400 Bad Request: invalid template',
        {
          detail: 'invalid template: rng must be one of [off, on]',
          errors: [{ field: 'rng', message: 'rng must be one of [off, on]' }],
        },
      ),
    );

    render(Page);
    await fireEvent.input(
      await screen.findByPlaceholderText('rebirth-classics'),
      { target: { value: 'ok' } },
    );
    await fireEvent.input(screen.getByPlaceholderText('Rebirth Classics'), {
      target: { value: 'OK' },
    });
    await fireEvent.input(screen.getByPlaceholderText('RG_RADIO'), {
      target: { value: 'A' },
    });
    await fireEvent.input(
      screen.getByPlaceholderText('Mon, Tue, Wed, Thu, Fri, Sat, Sun'),
      { target: { value: 'Mon' } },
    );
    await fireEvent.input(
      screen.getByPlaceholderText('0, 10, 20, 30, 40, 50, 50'),
      { target: { value: '0' } },
    );
    const blockSource = screen.getByTestId('block-source') as HTMLSelectElement;
    await fireEvent.change(blockSource, { target: { value: 'A' } });

    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));

    // Inline per-field error renders with the tagged message + a
    // stable id matching the input's aria-describedby.
    const rngError = await screen.findByTestId('rng-error');
    expect(rngError).toHaveAttribute('id', 'rng-error');
    expect(rngError.textContent).toMatch(/rng must be one of/);

    // The rng <select> is now flagged invalid and points at the
    // inline error via aria-describedby. The hint id stays in the
    // describedby list so the screen reader still gets the
    // "'off' = deterministic" context.
    const rngSelect = screen
      .getByText('RNG')
      .parentElement!
      .querySelector('select')!;
    expect(rngSelect.getAttribute('aria-invalid')).toBe('true');
    expect(rngSelect.getAttribute('aria-describedby')).toBe('rng-hint rng-error');

    // Banner copy comes from the structured `detail` (cleaner
    // human-readable form than the raw error text). Pin to the
    // banner via its testId — the inline rng-error also carries
    // the same substring, so a bare findByText would be ambiguous.
    const banner = await screen.findByTestId('station-form-error');
    expect(banner.textContent).toMatch(/rng must be one of/i);
    expect(gotoMock).not.toHaveBeenCalled();
  });

  it('§16.5 c3-review R1 — aria-invalid clears when the user edits the tagged input', async () => {
    // The submit-tag path flipped rng to aria-invalid="true". On
    // the very next `change` event from the rng <select>,
    // clearFieldError('rng') drops the key from fieldErrors so
    // aria-invalid reverts to false and the inline error <p>
    // disappears WITHOUT requiring another submit. Without the
    // per-field clear, the input stayed flagged invalid forever;
    // SR would announce "invalid" on every refocus.
    createStationMock.mockRejectedValueOnce(
      new FakeApiError(
        400,
        '400 Bad Request: invalid template',
        {
          detail: 'invalid template: rng must be one of [off, on]',
          errors: [{ field: 'rng', message: 'rng must be one of [off, on]' }],
        },
      ),
    );

    render(Page);
    await fireEvent.input(
      await screen.findByPlaceholderText('rebirth-classics'),
      { target: { value: 'ok' } },
    );
    await fireEvent.input(screen.getByPlaceholderText('Rebirth Classics'), {
      target: { value: 'OK' },
    });
    await fireEvent.input(screen.getByPlaceholderText('RG_RADIO'), {
      target: { value: 'A' },
    });
    await fireEvent.input(
      screen.getByPlaceholderText('Mon, Tue, Wed, Thu, Fri, Sat, Sun'),
      { target: { value: 'Mon' } },
    );
    await fireEvent.input(
      screen.getByPlaceholderText('0, 10, 20, 30, 40, 50, 50'),
      { target: { value: '0' } },
    );
    const blockSource = screen.getByTestId('block-source') as HTMLSelectElement;
    await fireEvent.change(blockSource, { target: { value: 'A' } });

    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));

    // Confirm we hit the tagged state first.
    await screen.findByTestId('rng-error');
    const rngSelect = screen
      .getByText('RNG')
      .parentElement!
      .querySelector('select')!;
    expect(rngSelect.getAttribute('aria-invalid')).toBe('true');

    // Now simulate the user picking a valid rng. clearFieldError
    // fires on the `change` event and drops the rng key from
    // fieldErrors — the inline error <p> unmounts and aria-invalid
    // reverts.
    await fireEvent.change(rngSelect, { target: { value: 'on' } });
    expect(screen.queryByTestId('rng-error')).not.toBeInTheDocument();
    expect(rngSelect.getAttribute('aria-invalid')).toBe('false');
  });

  it('renders the JSON preview disclosure in create mode (N4 anti-regression)', async () => {
    // The JSON preview ships in both create and edit modes (DESIGN.md §16.7
    // D6) but only edit-mode is tested in StationForm.test.ts. Without this
    // case, a future change that accidentally gated the preview on
    // `mode === 'edit'` would silently break the wizard preview without
    // breaking either test layer. One assertion is enough — content
    // verification happens in StationForm.test.ts.
    render(Page);
    expect(await screen.findByTestId('json-preview')).toBeInTheDocument();
  });
});
