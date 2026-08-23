import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("the public shell exposes an accessible official tag index tab", () => {
  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /data-view="tags"/u);
  assert.match(html, />\s*标签索引\s*</u);
});

test("startup uses the compact core and keeps tags on the lazy path", () => {
  const source = readFileSync(
    new URL("../../public/js/app.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /data\/catalog-core\.json/u);
  assert.match(source, /data\/catalog-tags\.json/u);
  assert.doesNotMatch(source, /data\/catalog\.json/u);
  assert.match(source, /createLazyTagLoader/u);
  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(html, /resolved-links\.json/u);
});

import {
  UI_STORAGE_KEY,
  applyRenderFocus,
  applyCoverFallback,
  attachPreviewProbe,
  bindDebouncedSearchInput,
  clearActiveSearch,
  collectLinkGroups,
  createPreviewIntersectionObserver,
  densityToggleLabel,
  loadFeaturedCovers,
  loadUiPreferences,
  normalizeSubtitleDirectoryResource,
  startPreviewProbeRequest,
  tabKeyTargetIndex,
  upgradeLinkGroups,
} from "../../public/js/app.js";

test("opening a detail tag clears global search before switching views", () => {
  const state = { query: "黑丝袜", searchStart: 100, searchTimer: 42 };
  const input = { value: "黑丝袜" };
  const clearButton = { hidden: false };
  const cleared = [];

  clearActiveSearch({
    state,
    input,
    clearButton,
    clearTimer: (timer) => cleared.push(timer),
  });

  assert.deepEqual(cleared, [42]);
  assert.equal(state.query, "");
  assert.equal(state.searchStart, 0);
  assert.equal(state.searchTimer, null);
  assert.equal(input.value, "");
  assert.equal(clearButton.hidden, true);
});

test("tab key navigation moves in both directions and wraps", () => {
  assert.equal(tabKeyTargetIndex("ArrowRight", 0, 3), 1);
  assert.equal(tabKeyTargetIndex("ArrowRight", 2, 3), 0);
  assert.equal(tabKeyTargetIndex("ArrowLeft", 2, 3), 1);
  assert.equal(tabKeyTargetIndex("ArrowLeft", 0, 3), 2);
});

test("tab key navigation handles boundaries and ignores unrelated keys", () => {
  assert.equal(tabKeyTargetIndex("Home", 2, 3), 0);
  assert.equal(tabKeyTargetIndex("End", 0, 3), 2);
  assert.equal(tabKeyTargetIndex("PageDown", 1, 3), null);
  assert.equal(tabKeyTargetIndex("Enter", 1, 3), null);
});

test("render focus transfer is opt-in", () => {
  const calls = [];
  const main = {
    focus(options) {
      calls.push(options);
    },
  };

  assert.equal(applyRenderFocus(main), false);
  assert.deepEqual(calls, []);
  assert.equal(applyRenderFocus(main, { focusMain: true }), true);
  assert.deepEqual(calls, [{ preventScroll: true }]);
});

test("paused search input commits each query without requesting main focus", () => {
  const listeners = new Map();
  const scheduled = new Map();
  let nextTimer = 1;
  const input = {
    value: "",
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
  };
  const clearButton = { hidden: true };
  const state = { query: "", searchStart: 99, searchTimer: null };
  const renderCalls = [];

  bindDebouncedSearchInput({
    input,
    clearButton,
    state,
    render: (...args) => renderCalls.push(args),
    clearTimer: (timer) => scheduled.delete(timer),
    setTimer(callback, delay) {
      assert.equal(delay, 180);
      const timer = nextTimer;
      nextTimer += 1;
      scheduled.set(timer, callback);
      return timer;
    },
  });

  input.value = "S";
  listeners.get("input")();
  scheduled.get(state.searchTimer)();
  assert.equal(state.query, "S");
  assert.equal(state.searchStart, 0);
  assert.equal(clearButton.hidden, false);

  input.value = "SP";
  listeners.get("input")();
  scheduled.get(state.searchTimer)();
  assert.equal(state.query, "SP");
  assert.deepEqual(renderCalls, [[], []]);
});

test("preview probe resolves once and fails closed before later loads", () => {
  const listeners = new Map();
  const image = {
    addEventListener(type, listener, options) {
      listeners.set(type, { listener, options });
    },
  };
  const calls = [];

  assert.equal(
    attachPreviewProbe(image, {
      onSuccess: () => calls.push("success"),
      onFailure: () => calls.push("failure"),
    }),
    true,
  );
  assert.equal(listeners.get("load").options.once, true);
  assert.equal(listeners.get("error").options.once, true);

  listeners.get("error").listener();
  listeners.get("load").listener();

  assert.deepEqual(calls, ["failure"]);
});

function previewImageFixture() {
  const listeners = new Map();
  const requests = [];
  return {
    image: {
      addEventListener(type, listener, options) {
        listeners.set(type, { listener, options });
      },
      set src(value) {
        requests.push(value);
      },
    },
    listeners,
    requests,
  };
}

test("preview loading requests only 001 until its probe succeeds", () => {
  const { image, listeners, requests } = previewImageFixture();

  const started = startPreviewProbeRequest(
    image,
    "https://www.giga-web.jp/db_titles/spsf/spsf0048/sample/001_l.jpg",
    {
      onSuccess() {
        requests.push(
          "https://www.giga-web.jp/db_titles/spsf/spsf0048/sample/002_l.jpg",
          "https://www.giga-web.jp/db_titles/spsf/spsf0048/sample/003_l.jpg",
          "https://www.giga-web.jp/db_titles/spsf/spsf0048/sample/004_l.jpg",
        );
      },
      onFailure() {
        requests.push("unexpected");
      },
    },
  );

  assert.equal(started, true);
  assert.deepEqual(requests.map((url) => new URL(url).pathname), [
    "/db_titles/spsf/spsf0048/sample/001_l.jpg",
  ]);

  listeners.get("load").listener();
  assert.deepEqual(requests.map((url) => new URL(url).pathname), [
    "/db_titles/spsf/spsf0048/sample/001_l.jpg",
    "/db_titles/spsf/spsf0048/sample/002_l.jpg",
    "/db_titles/spsf/spsf0048/sample/003_l.jpg",
    "/db_titles/spsf/spsf0048/sample/004_l.jpg",
  ]);
});

test("a failed 001 preview probe never schedules 002 or later", () => {
  const { image, listeners, requests } = previewImageFixture();

  startPreviewProbeRequest(
    image,
    "https://www.giga-web.jp/db_titles/tgs/tgs0004/sample/001_l.jpg",
    {
      onSuccess() {
        requests.push(
          "https://www.giga-web.jp/db_titles/tgs/tgs0004/sample/002_l.jpg",
        );
      },
      onFailure() {},
    },
  );
  listeners.get("error").listener();
  listeners.get("load").listener();

  assert.deepEqual(requests.map((url) => new URL(url).pathname), [
    "/db_titles/tgs/tgs0004/sample/001_l.jpg",
  ]);
});

test("default featured-cover deadline accepts a normal 400ms cold manifest", async () => {
  const generation = "a".repeat(64);
  const covers = await loadFeaturedCovers(
    () =>
      new Promise((resolve) => {
        setTimeout(() => {
          resolve({
            ok: true,
            json: async () => ({
              schemaVersion: 2,
              generation,
              covers: [
                {
                  code: "SPSF-1",
                  path: `/media/featured-covers/g/${generation}/spsf-1.webp`,
                },
              ],
            }),
          });
        }, 400);
      }),
  );

  assert.equal(
    covers.get("SPSF-1"),
    `/media/featured-covers/g/${generation}/spsf-1.webp`,
  );
});

test("slow or unavailable featured-cover manifests fall back without delaying catalog rendering", async () => {
  let deadlineSignal;
  const slow = await loadFeaturedCovers(
    (_url, options) => {
      deadlineSignal = options.signal;
      return new Promise(() => {});
    },
    { timeoutMs: 1 },
  );
  const missing = await loadFeaturedCovers(
    async () => ({ ok: false }),
    { timeoutMs: 1 },
  );

  assert.equal(slow.size, 0);
  assert.equal(missing.size, 0);
  assert.ok(deadlineSignal);
  assert.equal(deadlineSignal.aborted, true);
});

test("caller abort is relayed to a dedicated featured-manifest request signal", async () => {
  const caller = new AbortController();
  let requestSignal;
  const pending = loadFeaturedCovers(
    (_url, options) => {
      requestSignal = options.signal;
      return new Promise(() => {});
    },
    { signal: caller.signal, timeoutMs: 1 },
  );
  caller.abort();

  const result = await pending;
  assert.ok(requestSignal);
  assert.notEqual(requestSignal, caller.signal);
  assert.equal(requestSignal.aborted, true);
  assert.equal(result.size, 0);
});

class MemoryStorage {
  constructor(value = null) {
    this.value = value;
  }

  getItem(key) {
    return key === UI_STORAGE_KEY ? this.value : null;
  }
}

test("brand accessible name comes from all visible brand text", () => {
  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  const openingTag = html.match(/<a class="brand"[^>]*>/u)?.[0] ?? "";

  assert.ok(openingTag);
  assert.doesNotMatch(openingTag, /\saria-label=/u);
});

test("UI preferences preserve only supported theme, density, series, and slot values", () => {
  const storage = new MemoryStorage(
    JSON.stringify({
      theme: "light",
      density: "compact",
      selectedSeries: "spsf",
      slots: { SPSF: true, ABGD: false, INVALID: "yes" },
      injected: "<script>",
    }),
  );

  assert.deepEqual(loadUiPreferences(storage, true), {
    theme: "light",
    density: "compact",
    selectedSeries: "SPSF",
    slots: { SPSF: true, ABGD: false },
  });
});

test("invalid UI preferences fall back to the system theme", () => {
  assert.deepEqual(loadUiPreferences(new MemoryStorage("{broken"), true), {
    theme: "dark",
    density: "comfortable",
    selectedSeries: "",
    slots: {},
  });
});

test("density toggle labels always include the visible target layout", () => {
  assert.equal(densityToggleLabel("comfortable"), "切换到紧凑布局");
  assert.equal(densityToggleLabel("compact"), "切换到舒适布局");
});

test("cover failures retry the original source before using a local fallback", () => {
  const addedClasses = [];
  const image = {
    src: "/.netlify/images?url=optimized",
    alt: "SPSF-44 封面",
    dataset: {
      originalSrc: "https://www.giga-web.jp/db_titles/spsf/spsf-44.jpg",
    },
    classList: {
      add(value) {
        addedClasses.push(value);
      },
    },
  };

  applyCoverFallback(image);
  assert.equal(
    image.src,
    "https://www.giga-web.jp/db_titles/spsf/spsf-44.jpg",
  );
  assert.equal(image.dataset.originalRetried, "true");
  assert.equal(image.dataset.fallback, undefined);
  assert.equal(image.alt, "SPSF-44 封面");

  applyCoverFallback(image);
  assert.match(image.src, /^data:image\/svg\+xml,/u);
  assert.equal(image.dataset.fallback, "true");
  assert.equal(image.alt, "封面加载失败");
  assert.deepEqual(addedClasses, ["is-error"]);
});

test("external links are grouped deterministically and unsafe URLs are dropped", () => {
  assert.deepEqual(
    collectLinkGroups({
      gofile: "https://files.example/item",
      streamtape: "https://video.example/watch",
      player4me: "javascript:alert(1)",
      subtitle: "https://subtitles.example/file",
      uncensored: {
        player4me: "https://player.example/watch",
        gofile: "data:text/html,bad",
      },
    }),
    [
      {
        key: "standard",
        label: "普通版",
        links: [
          {
            provider: "streamtape",
            slot: "standard.streamtape",
            label: "Streamtape",
            url: "https://video.example/watch",
          },
          {
            provider: "gofile",
            slot: "standard.gofile",
            label: "Gofile",
            url: "https://files.example/item",
          },
        ],
      },
      {
        key: "uncensored",
        label: "无码版",
        links: [
          {
            provider: "player4me",
            slot: "uncensored.player4me",
            label: "Player4me",
            url: "https://player.example/watch",
          },
        ],
      },
      {
        key: "subtitle",
        label: "字幕",
        links: [
          {
            provider: "subtitle",
            slot: "subtitle.subtitle",
            label: "字幕",
            url: "https://subtitles.example/file",
          },
        ],
      },
    ],
  );
});

test("resolved cache upgrades matching slots without colliding with uncensored links", async () => {
  const groups = collectLinkGroups({
    gofile: "https://ouo.io/mT78vqU",
    streamtape: "https://ouo.io/kPWPLr",
    uncensored: { gofile: "https://ouo.io/HNjGRu" },
  });
  const manifest = new Map([
    [
      "SPSF-58\u0000standard.gofile",
      {
        sourceUrlHash:
          "sha256:8e4a74b155b39a37bc851982ed6c75f3b6ee95f0b42528b11cc6cc62afe198fc",
        finalUrl: "https://gofile.io/d/N87ugOtd",
        kind: "external",
        status: "verified",
      },
    ],
  ]);

  const upgraded = await upgradeLinkGroups("SPSF-58", groups, manifest);

  assert.deepEqual(upgraded[0].links, [
    {
      provider: "streamtape",
      slot: "standard.streamtape",
      label: "Streamtape",
      url: "https://ouo.io/kPWPLr",
      resolved: false,
    },
    {
      provider: "gofile",
      slot: "standard.gofile",
      label: "直达 Gofile",
      url: "https://gofile.io/d/N87ugOtd",
      resolved: true,
    },
  ]);
  assert.deepEqual(upgraded[1].links, [
    {
      provider: "gofile",
      slot: "uncensored.gofile",
      label: "Gofile",
      url: "https://ouo.io/HNjGRu",
      resolved: false,
    },
  ]);
});

test("global subtitle resource is normalized once and unsafe metadata is dropped", () => {
  assert.deepEqual(
    normalizeSubtitleDirectoryResource({
      subtitleDirectory: {
        label: 'SRT <img src=x onerror="bad">',
        url: "https://ouo.io/BAbfv4",
      },
      unresolved: [{ series: "PGHD", url: "https://ouo.io/private" }],
    }),
    {
      label: 'SRT <img src=x onerror="bad">',
      url: "https://ouo.io/BAbfv4",
    },
  );
  assert.equal(
    normalizeSubtitleDirectoryResource({
      subtitleDirectory: {
        label: "SRT ENGSUB DOWNLOAD",
        url: "javascript:alert(1)",
      },
    }),
    null,
  );

  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  const matches = html.match(/id="subtitle-directory-link"/gu) ?? [];
  assert.equal(matches.length, 1);
  assert.match(
    html,
    /id="subtitle-directory-link"[^>]*target="_blank"[^>]*rel="noopener noreferrer"[^>]*hidden/u,
  );
  const css = readFileSync(
    new URL("../../public/css/style.css", import.meta.url),
    "utf8",
  );
  assert.match(
    css,
    /\.subtitle-directory-link\s*\{[^}]*width:\s*44px;[^}]*overflow:\s*hidden;/su,
  );
});

test("preview observation is rooted in the active gallery rail", () => {
  let callback;
  let options;
  let observed;
  let visibleBatches = 0;
  class FakeIntersectionObserver {
    constructor(next, nextOptions) {
      callback = next;
      options = nextOptions;
    }

    observe(element) {
      observed = element;
    }
  }
  const rail = {};
  const sentinel = {};

  const observer = createPreviewIntersectionObserver(
    FakeIntersectionObserver,
    rail,
    sentinel,
    () => {
      visibleBatches += 1;
    },
  );

  assert.ok(observer instanceof FakeIntersectionObserver);
  assert.equal(options.root, rail);
  assert.equal(observed, sentinel);
  callback([{ isIntersecting: false }]);
  assert.equal(visibleBatches, 0);
  callback([{ isIntersecting: true }]);
  assert.equal(visibleBatches, 1);
});
