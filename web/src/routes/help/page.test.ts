import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, within } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';

const getReportMock = vi.fn();
vi.mock('$lib/api', () => ({ getReport: (...a: unknown[]) => getReportMock(...a) }));

import Page from './+page.svelte';

const REPORT = {
  app: 'Station Studio',
  version: '1.24.0',
  installed: 'downloaded Mac app',
  system: 'macOS 15.1 (Apple Silicon)',
  python: '3.12.7',
  log_file: '/Users/rob/Library/Application Support/Station Studio/station-studio.log',
  issue_url: 'https://github.com/raby/station-studio/issues/new?title=%5BStation+Studio%5D+&body=x',
};

describe('Help page', () => {
  beforeEach(() => {
    getReportMock.mockReset();
    getReportMock.mockRejectedValue(new Error('not asked for in this test'));
  });
  afterEach(() => cleanup());

  it('lists each topic with a link to it', () => {
    const { getByTestId, getByRole } = render(Page);
    for (const id of ['start', 'pools', 'stations', 'generate', 'app', 'apps', 'report']) {
      const card = getByTestId(`help-${id}`);
      expect(card).toHaveAttribute('id', `help-${id}`);
      expect(document.querySelector(`a[href="#help-${id}"]`)).not.toBeNull();
    }
    expect(getByRole('heading', { level: 1 })).toHaveTextContent('Help');
  });

  it('opens the other two apps, and says which is which on the docs site', () => {
    const { getByRole } = render(Page);
    expect(getByRole('link', { name: 'Open Station Player' })).toHaveAttribute('href', 'station-player://open');
    expect(getByRole('link', { name: 'Open Station Server' })).toHaveAttribute('href', 'station-server://open');
    const docs = getByRole('link', { name: 'Which app do I need?' });
    expect(docs).toHaveAttribute('href', 'https://raby.github.io/station-studio/which-app/');
    expect(docs).toHaveAttribute('target', '_blank');
  });

  it('reports a problem with this copy filled in, and shows where the log is', async () => {
    getReportMock.mockResolvedValue(REPORT);
    const { findByTestId, getByTestId } = render(Page);
    expect(await findByTestId('log-file')).toHaveTextContent(REPORT.log_file);
    const link = within(getByTestId('help-report')).getByRole('link', { name: 'Report a problem' });
    expect(link).toHaveAttribute('href', REPORT.issue_url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(getByTestId('help-report')).toHaveTextContent(
      'The report says: Station Studio 1.24.0, downloaded Mac app, on macOS 15.1 (Apple Silicon), Python 3.12.7.',
    );
  });

  it('says the log is in the terminal when Studio was started from one', async () => {
    getReportMock.mockResolvedValue({ ...REPORT, log_file: null, installed: 'installed with pip' });
    const { findByText, queryByTestId } = render(Page);
    expect(await findByText(/writes its log in the terminal/)).toBeInTheDocument();
    expect(queryByTestId('log-file')).toBeNull();
  });

  it('still links to the issues when the details can’t be fetched', async () => {
    getReportMock.mockRejectedValue(new Error('offline'));
    const { getByTestId } = render(Page);
    await Promise.resolve();
    expect(within(getByTestId('help-report')).getByRole('link', { name: 'Report a problem' })).toHaveAttribute(
      'href',
      'https://github.com/raby/station-studio/issues/new',
    );
  });
});
