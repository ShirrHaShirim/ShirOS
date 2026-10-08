// Dependency-free state regression checks; browser layout is verified separately.
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const path = require("node:path");

class Element {
  constructor() {
    this.children = [];
    this.attrs = {};
    this.handlers = {};
    this.value = "";
    this.scrollTop = 0;
    const classes = new Set();
    this.classList = {
      toggle(name, force) {
        const enabled = force === undefined ? !classes.has(name) : force;
        if (enabled) classes.add(name); else classes.delete(name);
        return enabled;
      },
      contains: (name) => classes.has(name),
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener(key, value) { this.handlers[key] = value; }
  get childElementCount() { return this.children.length; }
}

const nodes = Object.fromEntries(
  ["#tag-tree", "#tag-filter", "#tag-search", "#memory-untagged"].map((id) => [id, new Element()]),
);
const context = {
  document: { createElement: () => new Element() },
  $: (selector) => nodes[selector],
  text: (element, value) => { element.textContent = value; },
  tr: (key) => key,
  emptyList: (element, value) => element.append(value),
  openTagDialog() {}, loadLibrary: async () => {}, notice() {}, errorMessage() {},
  tags: [
    { id: "a", name: "A", parent_id: null },
    { id: "b", name: "B", parent_id: null },
    { id: "aa", name: "Child A", parent_id: "a" },
    { id: "bb", name: "Child B", parent_id: "b" },
  ],
  expandedTags: new Set(), pickerExpansion: new Map(), tagDrafts: new Map(),
  libraryMode: "tags", libraryTag: null, libraryOffset: 0, Set, Boolean,
};
vm.createContext(context);
const source = fs.readFileSync(path.join(__dirname, "../src/shiros/apps/web/app.js"), "utf8");
function loadFunctions(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end);
  assert(from >= 0 && to > from, `Missing function boundaries: ${start}`);
  vm.runInContext(source.slice(from, to), context);
}
loadFunctions("  function tagPath(", "  async function loadTags(");
loadFunctions("  function renderTagTree(", "  function renderLibraryList(");
const render = () => vm.runInContext("renderTagTree()", context);
const tree = nodes["#tag-tree"];
render();
assert.equal(tree.children[0].children[0].attrs["aria-expanded"], "false");
tree.children[0].children[0].handlers.click();
render();
assert.equal(tree.children[0].children[0].attrs["aria-expanded"], "true");
assert.equal(tree.children[2].children[0].attrs["aria-expanded"], "false");
tree.children[0].children[1].handlers.click();
render();
assert.equal(tree.children[2].children[0].attrs["aria-expanded"], "false");
nodes["#tag-search"].value = "Child B";
render();
assert.equal(tree.children.length, 2);
nodes["#tag-search"].value = "";
render();
assert.equal(tree.children[2].children[0].attrs["aria-expanded"], "false");

const picker = vm.runInContext("tagPicker('memory', [{id:'aa'}], 'memory')", context);
const search = picker.children[1], choices = picker.children[4];
assert.equal(choices.children[0].children[0].attrs["aria-expanded"], "true");
search.value = "B";
search.handlers.input();
assert.equal(choices.children.length, 2);
assert.equal(picker.children[3].children.length, 1);
search.value = "";
search.handlers.input();
const checkbox = choices.children[2].children[1].children[0];
checkbox.checked = true;
checkbox.handlers.change();
assert.equal(context.tagDrafts.get("memory").size, 2);
const reloaded = vm.runInContext("tagPicker('memory', [{id:'aa'}], 'memory')", context);
assert.equal(reloaded.children[3].children.length, 2);
console.log("PASS: independent expansion, tag selection, search restoration, nested picker, hidden selections, draft reload");
