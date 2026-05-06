import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

// --- mocks -------------------------------------------------------------
// $app/navigation and $lib/api need stubbing because the wizard's submit
// handler calls both. We replace them at module-mock level so the wizard
// renders as a plain component under jsdom.

// vi.mock factories are hoisted above imports — any values they reference
// must be declared via vi.hoisted so they're available at hoist time.
const { gotoMock, createStationMock, FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }
  return {
    gotoMock: vi.fn(),
    createStationMock: vi.fn(),
    FakeApiError,
  };
});

vi.mock('$app/navigation', () => ({
  goto: (...args: unknown[]) => gotoMock(...args),
}));
vi.mock('$lib/api', () => ({
  createStation: (...args: unknown[]) => createStationMock(...args),
  ApiError: FakeApiError,
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
    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));
    expect(await screen.findByText(/slug is required/i)).toBeInTheDocument();
    expect(createStationMock).not.toHaveBeenCalled();
  });

  it('rejects slugs with uppercase / spaces before hitting the API', async () => {
    render(Page);
    const slug = screen.getByPlaceholderText('rebirth-classics');
    await fireEvent.input(slug, { target: { value: 'Bad Slug' } });
    await fireEvent.click(screen.getByRole('button', { name: /create station/i }));
    expect(
      await screen.findByText(/slug must be lowercase, digits, or dashes/i),
    ).toBeInTheDocument();
    expect(createStationMock).not.toHaveBeenCalled();
  });

  it('requires at least one content source with a name', async () => {
    render(Page);
    await fireEvent.input(screen.getByPlaceholderText('rebirth-classics'), {
      target: { value: 'good-slug' },
    });
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
    await fireEvent.input(screen.getByPlaceholderText('rebirth-classics'), {
      target: { value: 'ok' },
    });
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
    await fireEvent.input(screen.getByPlaceholderText('rebirth-classics'), {
      target: { value: 'ok' },
    });
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

  it('renders the JSON preview disclosure in create mode (N4 anti-regression)', () => {
    // The JSON preview ships in both create and edit modes (DESIGN.md §16.7
    // D6) but only edit-mode is tested in StationForm.test.ts. Without this
    // case, a future change that accidentally gated the preview on
    // `mode === 'edit'` would silently break the wizard preview without
    // breaking either test layer. One assertion is enough — content
    // verification happens in StationForm.test.ts.
    render(Page);
    expect(screen.getByTestId('json-preview')).toBeInTheDocument();
  });
});
