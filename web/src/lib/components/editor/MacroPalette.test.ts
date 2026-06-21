import { describe, expect, it, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';

import MacroPalette from './MacroPalette.svelte';
import type { Sidecar } from '$lib/editor';

function _sidecar(macros: Sidecar['macros']): Sidecar {
  return {
    schema_version: 1,
    sections: [{ id: 's', name: 'S', blocks: [] }],
    macros,
    layout_order: ['s'],
  };
}

describe('§16.2.4.t1 MacroPalette', () => {
  it('renders the title + macro count', () => {
    const { getByTestId } = render(MacroPalette, {
      props: {
        sidecar: _sidecar([
          {
            id: 'm1',
            name: 'Jingle x3',
            repeat: 3,
            spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
          },
        ]),
      },
    });
    expect(getByTestId('macro-palette-count').textContent).toBe('1');
  });

  it('renders each macro as a clickable row with name + repeat + spec', () => {
    const { getAllByTestId } = render(MacroPalette, {
      props: {
        sidecar: _sidecar([
          {
            id: 'm1',
            name: 'Jingles',
            repeat: 3,
            spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
          },
          {
            id: 'm2',
            name: 'Commercials',
            repeat: 5,
            spec: { source: 'B', strategy: 'weighted', size: 1 },
          },
        ]),
      },
    });
    const rows = getAllByTestId('macro-palette-row');
    expect(rows).toHaveLength(2);
    // Row text carries name + repeat + spec preview.
    expect(rows[0].textContent).toContain('Jingles');
    expect(rows[0].textContent).toContain('× 3');
    expect(rows[1].textContent).toContain('Commercials');
    expect(rows[1].textContent).toContain('× 5');
  });

  it("falls back to id when a macro has no `name`", () => {
    const { getByTestId } = render(MacroPalette, {
      props: {
        sidecar: _sidecar([
          {
            id: 'unnamed-id',
            repeat: 2,
            spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
          },
        ]),
      },
    });
    const row = getByTestId('macro-palette-row');
    expect(row.textContent).toContain('unnamed-id');
  });

  it('dispatches `select { macroId }` on row click', async () => {
    const onSelect = vi.fn();
    const { getByTestId, component } = render(MacroPalette, {
      props: {
        sidecar: _sidecar([
          {
            id: 'm-pick',
            name: 'Pick',
            repeat: 2,
            spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
          },
        ]),
      },
    });
    component.$on('select', (e) => onSelect(e.detail));
    await fireEvent.click(getByTestId('macro-palette-row'));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith({ macroId: 'm-pick' });
  });

  it('shows the empty-state nudge + hides the row list when no macros', () => {
    const { getByTestId, queryByTestId } = render(MacroPalette, {
      props: { sidecar: _sidecar([]) },
    });
    expect(getByTestId('macro-palette-empty').textContent).toMatch(/No macros yet/);
    expect(queryByTestId('macro-palette-row')).toBeNull();
    expect(getByTestId('macro-palette-count').textContent).toBe('0');
  });

  it('treats a null sidecar (pre-load) as zero macros', () => {
    const { getByTestId, queryByTestId } = render(MacroPalette, {
      props: { sidecar: null },
    });
    expect(getByTestId('macro-palette-empty')).toBeTruthy();
    expect(queryByTestId('macro-palette-row')).toBeNull();
  });

  it('tolerates a sidecar with `macros` field absent (per grammar spec)', () => {
    const sc: Sidecar = {
      schema_version: 1,
      sections: [{ id: 's', name: 'S', blocks: [] }],
      layout_order: ['s'],
    };
    const { getByTestId, queryByTestId } = render(MacroPalette, {
      props: { sidecar: sc },
    });
    expect(getByTestId('macro-palette-empty')).toBeTruthy();
    expect(queryByTestId('macro-palette-row')).toBeNull();
  });

  it('carries the macro id as a data attribute for downstream lookup', () => {
    const { getByTestId } = render(MacroPalette, {
      props: {
        sidecar: _sidecar([
          {
            id: 'addressable',
            name: 'A',
            repeat: 1,
            spec: { source: 'A', strategy: 'jingle', size: 1, genre: 'G' },
          },
        ]),
      },
    });
    expect(getByTestId('macro-palette-row').getAttribute('data-macro-id')).toBe(
      'addressable',
    );
  });
});
