/* Shared lightweight transitions and delayed, layout-neutral request feedback. */
(() => {
  'use strict';
  const pending = new WeakMap(), animations = new WeakMap();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  function enter(node) {
    if (!node || reduced.matches || !node.animate) return;
    animations.get(node)?.cancel();
    animations.set(node, node.animate([
      {opacity: .94, transform: 'translateY(4px)'},
      {opacity: 1, transform: 'translateY(0)'}
    ], {duration: 160, easing: 'cubic-bezier(.2,.7,.3,1)'}));
  }
  async function request(url, options) {
    const host = document.querySelector('.view:not(.hidden)');
    let state;
    if (host) {
      state = pending.get(host);
      if (!state) {
        state = {count: 0, timer: null}; pending.set(host, state);
      }
      state.count++;
      if (state.count === 1) state.timer = setTimeout(() => {
        host.classList.add('shiros-ui-busy'); host.setAttribute('aria-busy', 'true');
      }, 120);
    }
    try { return await fetch(url, options); }
    finally {
      if (state && --state.count === 0) {
        clearTimeout(state.timer); host.classList.remove('shiros-ui-busy');
        host.removeAttribute('aria-busy');
      }
    }
  }
  window.ShirosMotion = {enter, request};
})();
