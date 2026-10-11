import test from "node:test";
import assert from "node:assert/strict";
import { fitImage, clampTransform, zoomAtPoint, swipeDirection, createImageViewerNavigation, shouldHandleMouseDoubleClick } from "../../public/js/image-viewer-state.js";

test("a synthesized double-click cannot undo touch double-tap zoom", () => {
  assert.equal(shouldHandleMouseDoubleClick({ sourceCapabilities: { firesTouchEvents: true } }, 1000), false);
  assert.equal(shouldHandleMouseDoubleClick({}, 20), false);
  assert.equal(shouldHandleMouseDoubleClick({ pointerType: "mouse" }, 1000), true);
});
import { collectPreviewUrls, normalizeFeaturedCovers, renderSearchResults } from "../../public/js/render.js";

test("the viewer can reach later batches and explicit previews", () => {
  assert.equal(collectPreviewUrls({ previewBase: "https://images.example/abc/", previewCount: 18 })[17], "https://images.example/abc/018_l.jpg");
  assert.deepEqual(collectPreviewUrls({ previewImages: ["javascript:alert(1)", "https://images.example/one.jpg"] }), ["https://images.example/one.jpg"]);
});
test("all 24 recent covers can use validated local files while only two are high priority", () => {
  const generation = "a".repeat(64);
  const covers = Array.from({ length: 24 }, (_, i) => ({ code: `ABC-${i + 1}`, path: `/media/featured-covers/g/${generation}/abc-${i + 1}.webp` }));
  const map = normalizeFeaturedCovers({ generation, covers });
  assert.equal(map.size, 24);
  const container = { innerHTML: "" };
  renderSearchResults(container, covers.map((item) => ({ code: item.code, title: item.code })), { context: "recent", featuredCovers: map });
  assert.equal((container.innerHTML.match(/fetchpriority="high"/g) || []).length, 2);
  assert.equal((container.innerHTML.match(/abc-24\.webp/g) || []).length, 1);
});
test("loading more preserves the existing cards and appends only the next window", () => {
  let added = "";
  const grid = { dataset: { view: "search", rendered: "24" }, insertAdjacentHTML: (_, html) => { added = html; } };
  const container = { innerHTML: "existing cards", querySelector: () => grid };
  const videos = Array.from({ length: 60 }, (_, i) => ({ code: `ABC-${i + 1}`, title: "Title" }));
  const metadata = renderSearchResults(container, videos, { append: true, visibleCount: 48 });
  assert.equal(container.innerHTML, "existing cards");
  assert.match(added, /data-code="ABC-25"/);
  assert.doesNotMatch(added, /data-code="ABC-1"/);
  assert.equal((added.match(/<article/g) || []).length, 24);
  assert.doesNotMatch(added, /fetchpriority="high"/);
  assert.equal(metadata.rendered, 48);
});

test("contain fitting preserves portrait and landscape images without cropping", () => {
  assert.deepEqual(fitImage(1200, 800, 390, 700), { width: 390, height: 260 });
  assert.deepEqual(fitImage(800, 1200, 390, 600), { width: 390, height: 585 });
});
test("zoom stays anchored to the finger and clamps pan at the image edges", () => {
  const bounds = { imageWidth: 400, imageHeight: 300, width: 400, height: 300 };
  assert.deepEqual(zoomAtPoint({ scale: 1, x: 0, y: 0 }, 2, { x: 80, y: 40 }, bounds), { scale: 2, x: -80, y: -40 });
  assert.deepEqual(clampTransform({ scale: 3, x: 999, y: -999 }, bounds), { scale: 3, x: 400, y: -300 });
  assert.deepEqual(clampTransform({ scale: 1, x: 50, y: 50 }, bounds), { scale: 1, x: 0, y: 0 });
});
test("swipe switches only on a deliberate horizontal gesture at original size", () => {
  assert.equal(swipeDirection(-100, 12, 1), 1);
  assert.equal(swipeDirection(100, 12, 1), -1);
  assert.equal(swipeDirection(20, 0, 1), 0);
  assert.equal(swipeDirection(100, 110, 1), 0);
  assert.equal(swipeDirection(-100, 0, 2), 0);
});

function harness({ isParentOpen = () => true, restoreParent } = {}) {
  const parent = { gigaCatalog: { videoDetail: { code: "ABC-1", sessionId: "parent", entryId: "detail" } }, filter: "recent" };
  const entries = [null, parent];
  let index = 1;
  let pending = false;
  let shown = null;
  let closes = 0;
  const listeners = new Set();
  const dispatch = () => { const state = entries[index]; history.beforePop?.(); for (const fn of listeners) fn({ state }); };
  const history = {
    get state() { return entries[index]; },
    backCalls: 0,
    pushState(value) { entries.splice(++index, entries.length, value); },
    replaceState(value) { entries[index] = value; },
    back() { this.backCalls++; pending = true; },
    flush() { if (pending) { pending = false; index--; dispatch(); } },
    forward() { if (index < entries.length - 1) { index++; dispatch(); } },
  };
  const nav = createImageViewerNavigation({
    history, eventTarget: { addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) },
    show: (context) => { shown = context; }, hide: () => { shown = null; closes++; },
    isParentOpen, restoreParent, sessionId: "gallery",
  });
  return { nav, history, entries, parent, get shown() { return shown; }, get closes() { return closes; } };
}
test("gallery owns one entry, preserves detail state and restores its image on Forward", () => {
  const h = harness();
  h.nav.open({ code: "ABC-1", urls: ["one", "two"], index: 0 });
  assert.deepEqual(h.history.state.gigaCatalog, h.parent.gigaCatalog);
  assert.equal(h.history.state.filter, "recent");
  h.nav.updateIndex(1);
  assert.equal(h.entries.length, 3);
  h.nav.requestClose(); h.nav.requestClose();
  assert.equal(h.history.backCalls, 1);
  assert.equal(h.nav.open({ code: "ABC-1", urls: ["three"], index: 0 }), false);
  h.history.flush();
  assert.equal(h.shown, null);
  assert.deepEqual(h.history.state, h.parent);
  h.history.forward();
  assert.equal(h.shown.index, 1);
  assert.deepEqual(h.shown.urls, ["one", "two"]);
});
test("direct parent dismissal strips only the viewer marker without extra Back", () => {
  const h = harness();
  h.nav.open({ code: "ABC-1", urls: ["one"], index: 0 });
  h.nav.dismiss();
  assert.deepEqual(h.history.state, h.parent);
  assert.equal(h.history.backCalls, 0);
  assert.equal(h.shown, null);
});

test("Forward to a gallery waits for async parent restoration and preserves its marker", async () => {
  let parentOpen = true;
  let complete;
  const restored = new Promise(resolve => { complete = resolve; });
  const h = harness({ isParentOpen: () => parentOpen, restoreParent: () => restored });
  h.nav.open({ code: "ABC-1", urls: ["one", "two"], index: 1 });
  h.nav.requestClose(); h.history.flush();
  parentOpen = false;
  h.history.forward();
  assert.ok(h.history.state.gigaImageViewer);
  h.nav.dismiss({ preserveRestoration: true });
  assert.ok(h.history.state.gigaImageViewer);
  parentOpen = true; complete(true);
  await restored; await Promise.resolve();
  assert.equal(h.shown?.index, 1);
  assert.ok(h.history.state.gigaImageViewer);
});

test("Back during async gallery restoration prevents a late reopen", async () => {
  let parentOpen = true;
  let complete;
  const restored = new Promise(resolve => { complete = resolve; });
  const h = harness({ isParentOpen: () => parentOpen, restoreParent: () => restored });
  h.nav.open({ code: "ABC-1", urls: ["one"], index: 0 });
  h.nav.requestClose(); h.history.flush();
  parentOpen = false; h.history.forward();
  h.history.back(); h.history.flush();
  parentOpen = true; complete(true);
  await restored; await Promise.resolve();
  assert.equal(h.shown, null);
  assert.deepEqual(h.history.state, h.parent);
});

test("a parent restored before the gallery popstate listener preserves the owned entry", () => {
  const h = harness();
  h.nav.open({ code: "ABC-1", urls: ["one"], index: 0 });
  h.nav.requestClose(); h.history.flush();
  h.history.beforePop = () => h.nav.dismiss({ preserveRestoration: true });
  h.history.forward();
  assert.ok(h.history.state.gigaImageViewer);
  assert.equal(h.shown?.index, 0);
});
