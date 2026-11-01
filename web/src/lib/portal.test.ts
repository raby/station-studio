import { describe, it, expect } from 'vitest';
import { portal } from './portal';

describe('portal', () => {
  function setup() {
    document.body.innerHTML = '<div id="home"><p id="before"></p><div id="el"></div><p id="after"></p></div><div id="there"></div>';
    const el = document.getElementById('el')!;
    const there = document.getElementById('there')!;
    return { el, there, home: document.getElementById('home')! };
  }

  it('shows the element inside the target', () => {
    const { el, there } = setup();
    portal(el, there);
    expect(el.parentElement).toBe(there);
  });

  it('with no target, leaves it where it was written', () => {
    const { el, home } = setup();
    portal(el, null);
    expect(el.parentElement).toBe(home);
    expect(el.previousElementSibling?.id).toBe('before');
  });

  it('moves there and back in place as the target comes and goes', () => {
    const { el, there, home } = setup();
    const action = portal(el, null);
    action.update(there);
    expect(el.parentElement).toBe(there);
    action.update(null);
    expect(el.parentElement).toBe(home);
    // Back between its old neighbours, not at the end.
    expect(el.previousElementSibling?.id).toBe('before');
    expect(el.nextElementSibling?.id).toBe('after');
  });

  it('leaves nothing behind when it goes', () => {
    const { el, there, home } = setup();
    const action = portal(el, there);
    action.destroy();
    el.remove(); // as Svelte does when the element goes
    expect(there.childNodes).toHaveLength(0);
    expect([...home.childNodes].some((n) => n.nodeType === Node.COMMENT_NODE)).toBe(false);
  });
});
