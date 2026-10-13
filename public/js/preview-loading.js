import { normalizeFeaturedCovers } from "./render.js";

export function previewBatchSize(connection) {
  return connection?.saveData || ["slow-2g", "2g"].includes(connection?.effectiveType)
    || (Number.isFinite(connection?.downlink) && connection.downlink < 3) ? 1 : 3;
}

export function afterFirstPreview({ image, source, signal, onReady, timeoutMs = 6000,
  setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout }) {
  let finished = false;
  let timer;
  const cleanup = () => {
    clearTimer(timer);
    image.removeEventListener("load", loaded);
    image.removeEventListener("error", failed);
    signal?.removeEventListener("abort", cancel);
  };
  const settle = reason => { if (finished) return; finished = true; cleanup(); onReady(reason); };
  const loaded = () => settle("load");
  const failed = () => settle("error");
  const cancel = () => { finished = true; cleanup(); };
  if (signal?.aborted) return cancel;
  image.addEventListener("load", loaded);
  image.addEventListener("error", failed);
  signal?.addEventListener("abort", cancel, { once: true });
  timer = setTimer(() => settle("timeout"), timeoutMs);
  image.src = source;
  return cancel;
}

export function startFollowingPreviews({ images, signal, onReady, ...timing }) {
  if (signal?.aborted) return () => {};
  const [next, ...remaining] = images;
  if (!next) { onReady(); return () => {}; }
  return afterFirstPreview({ ...timing, image: next, source: next.dataset.previewSource, signal,
    onReady: reason => {
      for (const image of remaining) image.src = image.dataset.previewSource;
      onReady(reason);
    },
  });
}

export function createViewerModuleLoader({ importer,
  setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout } = {}) {
  let pending = null;
  let attempt = 0;
  function load() {
    pending ??= Promise.resolve().then(() => importer(attempt++)).catch(error => { pending = null; throw error; });
    return pending;
  }
  function warm({ signal } = {}) {
    if (signal?.aborted || pending) return () => {};
    let timer;
    const cancel = () => { clearTimer(timer); signal?.removeEventListener("abort", cancel); };
    timer = setTimer(() => { cancel(); if (!signal?.aborted) void load().catch(() => {}); }, 180);
    signal?.addEventListener("abort", cancel, { once: true });
    return cancel;
  }
  return { load, warm };
}

function canonicalLocalCover(value, code) {
  const match = typeof value === "string" && value.match(/(\/media\/featured-covers\/g\/([0-9a-f]{64})\/[a-z][a-z0-9]*-\d+\.webp)$/u);
  return match ? normalizeFeaturedCovers({ generation: match[2], covers: [{ code, path: match[1] }] }).get(code) ?? null : null;
}

export function selectDetailCover(video, covers, loadedImage) {
  if (loadedImage?.original === video.cover) {
    if (loadedImage.source === video.cover) return video.cover;
    const local = canonicalLocalCover(loadedImage.source, video.code);
    if (local) return local;
  }
  return canonicalLocalCover(covers?.get(video.code), video.code) ?? video.cover;
}
