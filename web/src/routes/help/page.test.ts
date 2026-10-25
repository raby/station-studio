import { describe, it, expect, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Page from './+page.svelte';

describe('Help page', () => {
  afterEach(() => cleanup());

  it('lists each topic with a link to it', () => {
    const { getByTestId, getByRole } = render(Page);
    for (const id of ['start', 'pools', 'stations', 'generate', 'app']) {
      const card = getByTestId(`help-${id}`);
      expect(card).toHaveAttribute('id', `help-${id}`);
      expect(document.querySelector(`a[href="#help-${id}"]`)).not.toBeNull();
    }
    expect(getByRole('heading', { level: 1 })).toHaveTextContent('Help');
  });
});
