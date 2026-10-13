import assert from "node:assert/strict";
import test from "node:test";
import { afterFirstPreview, startFollowingPreviews, previewBatchSize, createViewerModuleLoader, selectDetailCover } from "../../public/js/preview-loading.js";

function timers() {
  let sequence = 0; const callbacks = new Map();
  return { set: fn => { callbacks.set(++sequence, fn); return sequence; }, clear: id => callbacks.delete(id), flush: () => { for (const fn of [...callbacks.values()]) fn(); callbacks.clear(); }, get size() { return callbacks.size; } };
}
class Picture extends EventTarget { set src(value) { this.source = value; if (this.cached) this.dispatchEvent(new Event("load")); } }

test("first-image callbacks are installed before assigning a cached image source", () => {
  const clock = timers(); const image = new Picture(); image.cached = true;
  const settled = [];
  afterFirstPreview({ image, source: "https://images.example/one.jpg", onReady: reason => settled.push(reason), setTimer: clock.set, clearTimer: clock.clear });
  assert.deepEqual(settled, ["load"]);
  assert.equal(clock.size, 0);
});
test("first image error releases subsequent work once and abort cancels stale work", () => {
  const clock = timers(); const image = new Picture(); const controller = new AbortController(); const settled = [];
  afterFirstPreview({ image, source: "one", signal: controller.signal, onReady: r => settled.push(r), setTimer: clock.set, clearTimer: clock.clear });
  image.dispatchEvent(new Event("error")); image.dispatchEvent(new Event("load")); clock.flush();
  assert.deepEqual(settled, ["error"]);
  const next = new Picture();
  afterFirstPreview({ image: next, source: "two", signal: controller.signal, onReady: r => settled.push(r), setTimer: clock.set, clearTimer: clock.clear });
  controller.abort(); next.dispatchEvent(new Event("load")); clock.flush();
  assert.deepEqual(settled, ["error"]);
});
test("timeout releases optional work without discarding the pending image", () => {
  const clock = timers(); const image = new Picture(); const settled = [];
  afterFirstPreview({ image, source: "slow", onReady: r => settled.push(r), setTimer: clock.set, clearTimer: clock.clear });
  clock.flush(); image.dispatchEvent(new Event("load"));
  assert.deepEqual(settled, ["timeout"]);
  assert.equal(image.source, "slow");
});
test("an already aborted detail does not start an image or viewer warming", () => {
  const controller = new AbortController(); controller.abort(); const clock = timers(); const image = new Picture();
  afterFirstPreview({ image, source: "one", signal: controller.signal, onReady: () => assert.fail(), setTimer: clock.set, clearTimer: clock.clear });
  assert.equal(image.source, undefined);
  const loader = createViewerModuleLoader({ importer: () => assert.fail(), setTimer: clock.set, clearTimer: clock.clear });
  loader.warm({ signal: controller.signal }); clock.flush();
  assert.equal(clock.size, 0);
});
test("next preview settles before remaining previews and optional work start; close cancels them", () => {
  const clock = timers(); const controller = new AbortController();
  const images = ["two", "three", "four"].map(source => Object.assign(new Picture(), { dataset: { previewSource: source } }));
  let ready = 0;
  startFollowingPreviews({ images, signal: controller.signal, onReady: () => ready++, setTimer: clock.set, clearTimer: clock.clear });
  assert.equal(images[0].source, "two");
  assert.equal(images[1].source, undefined);
  assert.equal(ready, 0);
  images[0].dispatchEvent(new Event("load"));
  assert.deepEqual(images.map(image => image.source), ["two", "three", "four"]);
  assert.equal(ready, 1);
  const stale = ["stale-two", "stale-three"].map(source => Object.assign(new Picture(), { dataset: { previewSource: source } }));
  startFollowingPreviews({ images: stale, signal: controller.signal, onReady: () => ready++, setTimer: clock.set, clearTimer: clock.clear });
  controller.abort(); stale[0].dispatchEvent(new Event("load")); clock.flush();
  assert.equal(stale[1].source, undefined);
  assert.equal(ready, 1);
});
test("viewer warming and explicit open share one import; failed warming permits retry", async () => {
  const clock = timers(); let calls = 0;
  const module = { createImageViewer() {} };
  const loader = createViewerModuleLoader({ importer: async () => { calls++; if (calls === 1) throw new Error("offline"); return module; }, setTimer: clock.set, clearTimer: clock.clear });
  loader.warm(); clock.flush();
  await assert.rejects(loader.load(), /offline/);
  loader.warm(); clock.flush();
  assert.equal(await loader.load(), module);
  assert.equal(calls, 2);
  assert.equal(await loader.load(), module);
  assert.equal(calls, 2);
});
test("closing a detail cancels scheduled warming and slow connections use one later preview", () => {
  const clock = timers(); let calls = 0; const controller = new AbortController();
  const loader = createViewerModuleLoader({ importer: async () => { calls++; }, setTimer: clock.set, clearTimer: clock.clear });
  loader.warm({ signal: controller.signal }); controller.abort(); clock.flush();
  assert.equal(calls, 0);
  for (const connection of [{ saveData: true }, { effectiveType: "2g" }, { downlink: 2 }]) assert.equal(previewBatchSize(connection), 1);
  assert.equal(previewBatchSize({ downlink: 10 }), 3);
});
test("failed module imports retry with a fresh attempt to bypass browser failure caching", async () => {
  const attempts = [];
  const loader = createViewerModuleLoader({ importer: async attempt => {
    attempts.push(attempt);
    if (attempt === 0) throw new Error("failed module fetch");
    return "loaded";
  } });
  await assert.rejects(loader.load(), /failed module fetch/);
  assert.equal(await loader.load(), "loaded");
  assert.deepEqual(attempts, [0, 1]);
});
test("detail cover reuse accepts the same source or canonical local cache, with original fallback", () => {
  const video = { code: "ABC-1", cover: "https://images.example/one.jpg" };
  const path = `/media/featured-covers/g/${"a".repeat(64)}/abc-1.webp`;
  assert.equal(selectDetailCover(video, new Map([[video.code, path]]), null), path);
  assert.equal(selectDetailCover(video, new Map(), { original: video.cover, source: path }), path);
  assert.equal(selectDetailCover(video, new Map(), { original: "https://images.example/other.jpg", source: path }), video.cover);
  assert.equal(selectDetailCover(video, new Map([[video.code, "/media/../../escape"]]), null), video.cover);
});
