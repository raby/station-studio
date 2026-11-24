import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Page from './+page.svelte';

describe('Help page', () => {
  afterEach(() => cleanup());

  it('lists each topic with a link to it', () => {
    const { getByTestId, getByRole } = render(Page);
    for (const id of ['start', 'pools', 'stations', 'generate', 'app', 'apps']) {
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
});
