import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/svelte';
import '@testing-library/jest-dom/vitest';
import Section from './Section.svelte';
import {
  DEFAULT_VIEW_MODE,
  type Sidecar,
  type SidecarSection,
  type StationTemplate,
  type ViewModeState,
} from '$lib/editor';

const VM: ViewModeState = DEFAULT_VIEW_MODE;


function makeTemplate(): StationTemplate {
  return {
    station: 'ts', name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot', unit_labels: [], row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'pick', size: 1 },
      ],
    },
  };
}


function makeSidecar(): Sidecar {
  return {
    schema_version: 1, sections: [], macros: [], layout_order: [],
  };
}


function makeSection(over: Partial<SidecarSection> = {}): SidecarSection {
  return {
    id: 's1', name: 'Section 1',
    blocks: [
      { kind: 'atomic', slot_index: 0 },
      { kind: 'atomic', slot_index: 1 },
    ],
    ...over,
  };
}


describe('<Section>', () => {
  beforeEach(() => cleanup());

  it('renders header with name + chip count', () => {
    render(Section, {
      viewMode: VM,
      section: makeSection(), template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('section-name')).toHaveTextContent('Section 1');
    expect(screen.getByTestId('section-count')).toHaveTextContent('2 chips');
  });

  it('falls back to section.id when name is omitted (🟡-2)', () => {
    render(Section, {
      viewMode: VM,
      section: makeSection({ name: undefined, id: 'abc-123' }),
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('section-name')).toHaveTextContent('abc-123');
  });

  it('renders body when initialExpanded=true', () => {
    render(Section, {
      viewMode: VM,
      section: makeSection(), template: makeTemplate(),
      sidecar: makeSidecar(), initialExpanded: true,
    });
    expect(screen.getByTestId('section-body')).toBeInTheDocument();
    expect(screen.getAllByTestId('chip')).toHaveLength(2);
  });

  it('hides body when initialExpanded=false', () => {
    render(Section, {
      viewMode: VM,
      section: makeSection(), template: makeTemplate(),
      sidecar: makeSidecar(), initialExpanded: false,
    });
    expect(screen.queryByTestId('section-body')).toBeNull();
    expect(screen.queryAllByTestId('chip')).toHaveLength(0);
  });

  it('clicking the toggle flips expanded state', async () => {
    render(Section, {
      viewMode: VM,
      section: makeSection(), template: makeTemplate(),
      sidecar: makeSidecar(), initialExpanded: false,
    });
    expect(screen.queryByTestId('section-body')).toBeNull();
    await fireEvent.click(screen.getByTestId('section-toggle'));
    expect(screen.getByTestId('section-body')).toBeInTheDocument();
    await fireEvent.click(screen.getByTestId('section-toggle'));
    expect(screen.queryByTestId('section-body')).toBeNull();
  });

  it('header singular/plural matches chip count (1 chip vs N chips)', () => {
    render(Section, {
      viewMode: VM,
      section: makeSection({
        blocks: [{ kind: 'atomic', slot_index: 0 }],
      }),
      template: makeTemplate(), sidecar: makeSidecar(),
    });
    expect(screen.getByTestId('section-count')).toHaveTextContent('1 chip');
    // No trailing 's' on singular.
    expect(screen.getByTestId('section-count').textContent).toMatch(/1 chip(?!s)/);
  });
});
