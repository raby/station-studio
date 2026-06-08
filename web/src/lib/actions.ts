/**
 * Shared Svelte actions (use:directive helpers).
 *
 * §16.2.5 c2 review #7 — `focusOnMount` replaces the unreliable
 * `autofocus` HTML attribute for inputs inserted via reactive
 * `{#if}` blocks. Firefox + Safari ignore `autofocus` on
 * dynamically-mounted inputs; the action calls `node.focus()`
 * imperatively on mount, which works uniformly across browsers
 * + closes the svelte-check a11y-no-autofocus warning the
 * attribute triggers.
 *
 * Usage:
 *   <input use:focusOnMount />
 *
 * Or pass `false` to disable conditionally:
 *   <input use:focusOnMount={shouldFocus} />
 */
export function focusOnMount(node: HTMLElement, enabled: boolean = true) {
  if (enabled) {
    // Defer one microtask so the element is actually in the DOM +
    // can receive focus. Calling node.focus() synchronously during
    // the mount tick is a no-op in some browsers (the focus call
    // happens before the element is "current" in the focus queue).
    queueMicrotask(() => node.focus());
  }
  return {
    update(newEnabled: boolean) {
      if (newEnabled && document.activeElement !== node) {
        queueMicrotask(() => node.focus());
      }
    },
  };
}


/**
 * §16.2.5.t6 — `commitOnEnterEscapeBlur`: shared inline-edit
 * keyboard + blur handler. Enter commits, Escape cancels, blur
 * commits (with a `committed` latch so the blur fired during
 * unmount post-Enter doesn't double-dispatch).
 *
 * The latch is the load-bearing detail. Real browsers fire blur
 * when the input unmounts (Enter handler set `renaming = false`
 * → input removed from DOM → blur fires on the now-detached
 * element). jsdom doesn't, so unit tests passed but real-browser
 * usage double-dispatched the commit. Both `commitRename` (§16.2.5
 * c2 SectionHeader rename) + the in-line section-create input on
 * EditorCanvas (which today doesn't handle blur AT ALL — silent
 * half-state when operator clicks away) want this.
 *
 * Parameters:
 *   - `onCommit(value)`: called with the input's current value
 *     when the operator commits (Enter or blur). Implementations
 *     should validate the value + dispatch the upstream event.
 *   - `onCancel()`: called when Escape is pressed (or, optionally,
 *     when blur fires with no edit — but we leave that policy to
 *     the caller; blur defaults to commit).
 *
 * Usage:
 *   <input use:commitOnEnterEscapeBlur={{onCommit, onCancel}} />
 */
export interface CommitOnEnterEscapeBlurOpts {
  onCommit: (value: string) => void;
  onCancel: () => void;
}

export function commitOnEnterEscapeBlur(
  node: HTMLInputElement,
  opts: CommitOnEnterEscapeBlurOpts,
) {
  let committed = false;
  let currentOpts = opts;

  function handleKey(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (committed) return;
      committed = true;
      currentOpts.onCommit(node.value);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      committed = true;  // suppress the blur firing during unmount
      currentOpts.onCancel();
    }
  }

  function handleBlur() {
    if (committed) return;
    committed = true;
    currentOpts.onCommit(node.value);
  }

  node.addEventListener('keydown', handleKey);
  node.addEventListener('blur', handleBlur);
  return {
    update(newOpts: CommitOnEnterEscapeBlurOpts) {
      currentOpts = newOpts;
    },
    destroy() {
      node.removeEventListener('keydown', handleKey);
      node.removeEventListener('blur', handleBlur);
    },
  };
}


/**
 * §16.2.5.t6 — `armToConfirm`: two-click destructive-confirm action.
 * First trigger arms the button for `timeoutMs`; second trigger
 * within the window commits via `onConfirm`. Auto-disarms after
 * the window.
 *
 * Uses `performance.now()` (monotonic) so wall-clock drift (NTP,
 * DST, tab-pause) can't extend or shrink the window. A generation
 * counter invalidates stale `setTimeout`s — re-arm + commit cycles
 * within the window don't leak callbacks that disarm fresh state.
 *
 * The action exposes an imperative `trigger()` method via the
 * `update` callback's input. Components read `armed` from their
 * own reactive state (the action calls a supplied `onArmedChange`
 * to sync).
 *
 * Why a Svelte action rather than a callback store: the timer
 * lifecycle is tied to the button's DOM lifetime — when the button
 * unmounts (component destroyed) the timer should clear. Actions
 * get `destroy()` for free.
 *
 * Usage example (in DetailPanel):
 *   let armed = false;
 *   <button use:armToConfirm={{
 *     timeoutMs: 3000,
 *     onConfirm: () => dispatch('structuralEdit', {...}),
 *     onArmedChange: (a) => armed = a,
 *   }} on:click={(e) => triggerArm(e)} >
 *     {armed ? 'Confirm?' : 'Delete'}
 *   </button>
 *
 * The action manages state; the consumer reads it via the
 * onArmedChange callback. The trigger comes through the action's
 * exposed `trigger` method on the action's returned object.
 *
 * Simpler API trade-off: we expose `trigger` via a callback-set
 * pattern. The component calls `setTrigger((fn) => triggerRef = fn)`
 * inside the action's options, and the action calls it back during
 * mount. See DetailPanel for the wiring pattern.
 */
export interface ArmToConfirmOpts {
  timeoutMs?: number;
  onConfirm: () => void;
  onArmedChange?: (armed: boolean) => void;
  /** Optional callback that the action invokes with its `trigger()`
   *  function. The component stores the reference + calls it from
   *  the button's click handler. */
  bindTrigger?: (trigger: () => void) => void;
}

export function armToConfirm(node: HTMLElement, opts: ArmToConfirmOpts) {
  let armedAt: number | null = null;
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let currentOpts = opts;
  const DEFAULT_MS = 3000;

  function _disarm() {
    if (armedAt !== null) {
      armedAt = null;
      currentOpts.onArmedChange?.(false);
    }
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function trigger() {
    const timeoutMs = currentOpts.timeoutMs ?? DEFAULT_MS;
    const now = performance.now();
    if (armedAt === null || now - armedAt > timeoutMs) {
      // First click (or expired): arm.
      _disarm();
      armedAt = now;
      generation += 1;
      const myGen = generation;
      currentOpts.onArmedChange?.(true);
      timer = setTimeout(() => {
        if (generation === myGen) _disarm();
      }, timeoutMs + 50);
      return;
    }
    // Second click within window: commit.
    _disarm();
    currentOpts.onConfirm();
  }

  // Bind the trigger so the component can call it.
  currentOpts.bindTrigger?.(trigger);

  return {
    update(newOpts: ArmToConfirmOpts) {
      currentOpts = newOpts;
      newOpts.bindTrigger?.(trigger);
    },
    destroy() {
      _disarm();
    },
  };
}
