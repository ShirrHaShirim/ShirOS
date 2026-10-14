/* Show the scrollbar away from the origin; preserve the pane's layout. */
(() => {
  'use strict';
  function update(node) {
    if (node instanceof Element) node.classList.toggle('shiros-scroll-offset', node.scrollTop > 0 || Math.abs(node.scrollLeft) > 0);
  }
  document.addEventListener('scroll', event => {
    const node = event.target === document ? document.documentElement : event.target;
    update(node);
  }, {capture: true, passive: true});
  const initialize = () => document.querySelectorAll('*').forEach(update);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true});
  else initialize();
})();
