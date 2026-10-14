/* Interface-only messages. Catalogue names and saved content are never translated. */
(() => {
  'use strict';
  let language = 'zh';
  try { language = localStorage.getItem('shiros-locale') === 'en' ? 'en' : 'zh'; } catch (_) {}
  const messages = new Map(), bindings = new Set();
  const marker = Symbol('shiros-ui-message'), attributeSlots = new WeakMap();
  const render = value => value?.[marker] ? format(value) : String(value ?? '');
  function format(value) {
    if (value.formatter) return value.formatter(language);
    const source = value.source;
    const trimmed = source.trim();
    const translated = language === 'en' ? messages.get(source) ?? messages.get(trimmed) : null;
    const template = translated == null ? source : source.replace(trimmed, translated);
    return template.replace(/\{(\d+)\}/g, (_, i) => render(value.values[Number(i)]));
  }
  function message(source, ...values) {
    return { [marker]: true, source, values, toString() { return format(this); } };
  }
  function bind(node, update) { bindings.add({ node: new WeakRef(node), update }); }
  function text(node, value) {
    if (node.nodeType === 3) node.data = render(value);
    else node.textContent = render(value);
    if (value?.[marker]) {
      const target = node.nodeType === 3 ? node : node.firstChild;
      // Bind the text node so changing language cannot remove a label's input or icon.
      if (target) bind(target, n => { n.data = render(value); });
    }
  }
  function attribute(node, name, value) {
    node.setAttribute(name, render(value));
    const slots = attributeSlots.get(node) || new Map();
    attributeSlots.set(node, slots); slots.set(name, value);
    if (value?.[marker]) bind(node, n => {
      if (attributeSlots.get(n)?.get(name) === value) n.setAttribute(name, render(value));
    });
  }
  function refresh() {
    for (const entry of bindings) {
      const node = entry.node.deref();
      // Cached library pages retain their nodes while detached. Translate them too.
      if (!node) bindings.delete(entry);
      else entry.update(node);
    }
    document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
  }
  function setLocale(value) {
    const next = value === 'en' || value === 'en-US' ? 'en' : 'zh';
    if (language === next) return;
    language = next; refresh();
    document.dispatchEvent(new CustomEvent('shiros-locale-changed', { detail: { language } }));
  }
  function scanStatic(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      if (!node.parentElement?.closest('script,style') && /[\u3400-\u9fff]/.test(node.data)) text(node, message(node.data));
    }
    for (const node of root.querySelectorAll('[placeholder],[aria-label],[title]')) {
      for (const name of ['placeholder', 'aria-label', 'title']) {
        const value = node.getAttribute(name);
        if (value && /[\u3400-\u9fff]/.test(value)) attribute(node, name, message(value));
      }
    }
  }
  window.ShirosI18n = {
    message, text, attribute, render, setLocale, refresh, scanStatic,
    join: (values, separator = ' · ') => message(values.map((_, i) => `{${i}}`).join(separator), ...values),
    date: (value, options = {}) => ({ [marker]: true, formatter: lang => new Date(value).toLocaleString(lang === 'en' ? 'en-US' : 'zh-CN', options), toString() { return format(this); } }),
    label: value => messages.has(String(value)) ? message(String(value)) : value,
    register(entries) { for (const [source, english] of Object.entries(entries)) messages.set(source, english); },
    get locale() { return language === 'en' ? 'en-US' : 'zh-CN'; },
    get language() { return language; },
  };
})();
