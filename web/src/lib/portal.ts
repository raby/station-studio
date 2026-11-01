/**
 * `use:portal={target}`: show an element inside `target` instead of where
 * it's written, and back in its own place when `target` is null.
 *
 * Only the element moves: the component that renders it keeps its state
 * and event handlers, and still removes it when it goes. The station
 * editor uses it to show a rule's panel in the preview column, beside
 * the songs, while the canvas keeps owning the panel.
 */
export function portal(node: HTMLElement, target: HTMLElement | null) {
  // A marker where the element was written, to put it back.
  const home = document.createComment('portal');
  node.parentNode?.insertBefore(home, node);

  function place(to: HTMLElement | null) {
    if (to) {
      if (node.parentNode !== to) to.appendChild(node);
    } else if (home.parentNode && node.previousSibling !== home) {
      home.parentNode.insertBefore(node, home.nextSibling);
    }
  }

  place(target);
  return {
    update: place,
    destroy() {
      home.remove();
    },
  };
}
