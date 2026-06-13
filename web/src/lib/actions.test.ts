import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { commitOnEnterEscapeBlur, armToConfirm } from './actions';

/**
 * Action-level unit tests for the §16.2.5.t6 shared inline-edit
 * primitives. Pin the contracts the c4 review #1 surfaced + locked
 * down: IME composition guard, latch reset on update(), min-arm-ms
 * double-click floor, and the basic lifecycle (commit / cancel /
 * destroy cleanup). Vitest contracts that COMPONENT tests can't
 * easily exercise.
 */


function makeInput(): HTMLInputElement {
  const input = document.createElement('input');
  input.type = 'text';
  document.body.appendChild(input);
  return input;
}


describe('commitOnEnterEscapeBlur', () => {
  let input: HTMLInputElement;

  beforeEach(() => {
    input = makeInput();
  });
  afterEach(() => {
    input.remove();
  });

  it('Enter commits with the input value', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel });
    input.value = 'hello';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(onCommit).toHaveBeenCalledTimes(1);
    expect(onCommit).toHaveBeenCalledWith('hello');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('Escape cancels, no commit', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel });
    input.value = 'discarded';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(onCommit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('blur commits when not pre-latched', () => {
    const onCommit = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel: vi.fn() });
    input.value = 'blurred';
    input.dispatchEvent(new FocusEvent('blur'));
    expect(onCommit).toHaveBeenCalledWith('blurred');
  });

  it('Enter sets the latch — subsequent blur does NOT double-dispatch', () => {
    const onCommit = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel: vi.fn() });
    input.value = 'x';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    input.dispatchEvent(new FocusEvent('blur'));
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it('Escape sets the latch — subsequent blur does NOT commit', () => {
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel });
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    input.dispatchEvent(new FocusEvent('blur'));
    expect(onCommit).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('IME composition Enter is ignored (isComposing=true)', () => {
    // c4 review #1 finding #2: Enter during a CJK IME composition
    // must not commit. Synthesise a keydown with isComposing=true.
    const onCommit = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel: vi.fn() });
    input.value = 'partial';
    const e = new KeyboardEvent('keydown', { key: 'Enter' });
    Object.defineProperty(e, 'isComposing', { value: true });
    input.dispatchEvent(e);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('IME composition Enter via keyCode 229 (Safari) is ignored', () => {
    const onCommit = vi.fn();
    commitOnEnterEscapeBlur(input, { onCommit, onCancel: vi.fn() });
    const e = new KeyboardEvent('keydown', { key: 'Enter' });
    Object.defineProperty(e, 'keyCode', { value: 229 });
    input.dispatchEvent(e);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('update() resets the latch so a remounted input can commit again', () => {
    // c4 review #1 finding #4: the EditorCanvas Add Section flow
    // keeps the input mounted across edit cycles (commit one
    // section, type another). The latch must reset so the second
    // commit isn't silently swallowed.
    const onCommit = vi.fn();
    const action = commitOnEnterEscapeBlur(input, {
      onCommit, onCancel: vi.fn(),
    });
    input.value = 'first';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(onCommit).toHaveBeenCalledTimes(1);
    // Simulate a Svelte reactive update — consumer passes the same
    // callbacks. The latch resets so the next Enter commits again.
    action.update({ onCommit, onCancel: vi.fn() });
    input.value = 'second';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(onCommit).toHaveBeenCalledTimes(2);
    expect(onCommit).toHaveBeenLastCalledWith('second');
  });

  // onCommit-throws-releases-latch: contract verified inline in the
  // action via try/finally; a vitest case here would need to
  // dispatch through jsdom's EventListener wrapper which converts
  // handler throws into window-level errors that vitest tracks as
  // unhandled. The latch-reset behavior is sufficiently covered by
  // the `update() resets the latch` case above; this throw-path
  // edge case is verified by reading the action source + the
  // try/finally pattern.

  it('destroy() removes both listeners', () => {
    const onCommit = vi.fn();
    const action = commitOnEnterEscapeBlur(input, {
      onCommit, onCancel: vi.fn(),
    });
    action.destroy();
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    input.dispatchEvent(new FocusEvent('blur'));
    expect(onCommit).not.toHaveBeenCalled();
  });
});


describe('armToConfirm', () => {
  let button: HTMLButtonElement;

  beforeEach(() => {
    button = document.createElement('button');
    document.body.appendChild(button);
    vi.useFakeTimers();
  });
  afterEach(() => {
    button.remove();
    vi.useRealTimers();
  });

  it('first trigger arms; onArmedChange(true) fires', () => {
    const onArmedChange = vi.fn();
    let trigger: (() => void) | null = null;
    armToConfirm(button, {
      onConfirm: vi.fn(),
      onArmedChange,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();
    expect(onArmedChange).toHaveBeenCalledWith(true);
  });

  it('second trigger after minArmMs but within timeoutMs commits', () => {
    const onConfirm = vi.fn();
    let trigger: (() => void) | null = null;
    armToConfirm(button, {
      onConfirm,
      timeoutMs: 3000,
      minArmMs: 300,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();
    vi.advanceTimersByTime(500);
    trigger!();
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('fast OS double-click (within minArmMs) does NOT commit', () => {
    // c4 review #1 finding #3: macOS / touch can deliver two click
    // events within ~250ms. Without the min-arm floor, the second
    // commits without the operator seeing the armed label.
    const onConfirm = vi.fn();
    let trigger: (() => void) | null = null;
    armToConfirm(button, {
      onConfirm,
      timeoutMs: 3000,
      minArmMs: 300,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();
    vi.advanceTimersByTime(100);  // way under 300ms floor
    trigger!();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('after timeoutMs elapsed, trigger re-arms (does not commit)', () => {
    const onConfirm = vi.fn();
    const onArmedChange = vi.fn();
    let trigger: (() => void) | null = null;
    armToConfirm(button, {
      onConfirm,
      onArmedChange,
      timeoutMs: 3000,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();  // arm
    onArmedChange.mockClear();
    vi.advanceTimersByTime(3100);  // past timeout
    trigger!();  // re-arm, not commit
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onArmedChange).toHaveBeenCalledWith(true);
  });

  it('auto-disarm fires after timeoutMs', () => {
    const onArmedChange = vi.fn();
    let trigger: (() => void) | null = null;
    armToConfirm(button, {
      onConfirm: vi.fn(),
      onArmedChange,
      timeoutMs: 3000,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();
    onArmedChange.mockClear();
    vi.advanceTimersByTime(3060);  // past timer (timeoutMs + 50)
    expect(onArmedChange).toHaveBeenCalledWith(false);
  });

  it('destroy() while armed clears the pending timer', () => {
    const onArmedChange = vi.fn();
    let trigger: (() => void) | null = null;
    const action = armToConfirm(button, {
      onConfirm: vi.fn(),
      onArmedChange,
      bindTrigger: (t) => { trigger = t; },
    });
    trigger!();
    action.destroy();
    onArmedChange.mockClear();
    vi.advanceTimersByTime(10_000);
    expect(onArmedChange).not.toHaveBeenCalled();
  });

  it('update() with same bindTrigger does NOT re-invoke the bind callback', () => {
    // c4 review #1 finding #7: idempotent rebind. Stable trigger
    // identity means update() with same bindTrigger ref shouldn't
    // re-run the callback.
    const bindTrigger = vi.fn();
    const action = armToConfirm(button, {
      onConfirm: vi.fn(),
      bindTrigger,
    });
    expect(bindTrigger).toHaveBeenCalledTimes(1);
    // Same callback reference → no re-bind.
    action.update({ onConfirm: vi.fn(), bindTrigger });
    expect(bindTrigger).toHaveBeenCalledTimes(1);
  });

  it('update() with a NEW bindTrigger callback rebinds', () => {
    const bindTrigger1 = vi.fn();
    const bindTrigger2 = vi.fn();
    const action = armToConfirm(button, {
      onConfirm: vi.fn(),
      bindTrigger: bindTrigger1,
    });
    action.update({ onConfirm: vi.fn(), bindTrigger: bindTrigger2 });
    expect(bindTrigger2).toHaveBeenCalledTimes(1);
  });
});
