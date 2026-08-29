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

test("startup preloads only the bootstrap and its V3 module graph", () => {
  const source = readFileSync(
    new URL("../../public/js/app.js", import.meta.url),
    "utf8",
  );
  assert.match(source, /openCatalogCache/u);
  assert.match(source, /createRuntimeCatalogStore/u);
  assert.match(source, /createRuntimeLoader/u);
  assert.doesNotMatch(source, /catalog-core\.json/u);
  assert.doesNotMatch(source, /catalog-tags\.json/u);
  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  assert.match(html, /rel="preload" href="data\/catalog-bootstrap\.json"/u);
  assert.doesNotMatch(html, /catalog-core\.json/u);
  assert.doesNotMatch(html, /catalog-search\.json/u);
  assert.doesNotMatch(html, /catalog-tags\.json/u);
  for (const module of [
    "js/app.js",
    "js/runtime-catalog.js",
    "js/catalog-cache.js",
    "js/runtime-loader.js",
  ]) {
    assert.equal(
      (html.match(new RegExp(`rel="modulepreload" href="${module.replace(".", "\\.")}"`, "gu")) ?? []).length,
      1,
      module,
    );
  }
  assert.doesNotMatch(html, /resolved-links\.json/u);
});

test("cached and offline runtime states use exact accessible production copy", () => {
  assert.equal(
    runtimeCatalogStatus({ cached: true, online: true }),
    "已显示缓存，正在检查更新",
  );
  assert.equal(
    runtimeCatalogStatus({ cached: true, online: false }),
    "离线使用已缓存目录",
  );
  assert.equal(runtimeCatalogStatus({ cached: false, online: true }), "数据已就绪");

  const html = readFileSync(
    new URL("../../public/index.html", import.meta.url),
    "utf8",
  );
  assert.match(
    html,
    /id="connection-status"[^>]*class="[^"]*catalog-cache-state[^"]*"[^>]*role="status"[^>]*aria-live="polite"[^>]*aria-atomic="true"/u,
  );
});

test("startup error copy never exposes an error message or source URL", () => {
  const source = readFileSync(
    new URL("../../public/js/app.js", import.meta.url),
    "utf8",
  );
  const renderError = source.match(/function renderLoadError\([\s\S]*?\n  \}/u)?.[0] ?? "";
  assert.ok(renderError);
  assert.doesNotMatch(renderError, /error\.message|String\(error\)/u);
  const hostile = new Error("fetch failed https://private.example/secret?token=abc");
  const publicCopy = runtimeLoadErrorMessage(hostile);
  assert.equal(publicCopy, "目录加载失败，请稍后重试。");
  assert.doesNotMatch(publicCopy, /https:\/\/private\.example|token=abc/u);
});

test("catalog cache and progressive controls have responsive accessible states", () => {
  const css = readFileSync(
    new URL("../../public/css/style.css", import.meta.url),
    "utf8",
  );
  assert.match(css, /\.catalog-cache-state\s*\{/u);
  assert.match(css, /\.load-more\s*\{/u);
  assert.match(css, /\.progressive-status\s*\{/u);
  const mobileCascade = css.match(
    /@media\s*\(max-width:\s*74\.99rem\)[\s\S]*?(?=\n@media|$)/u,
  )?.[0] ?? "";
  assert.match(mobileCascade, /\.connection-status,\s*\.header-control/u);
  const ordinaryConnectionStatus = mobileCascade.match(
    /\.connection-status,\s*\.header-control\s+\[data-control-label\]\s*\{[\s\S]*?\}/u,
  )?.[0] ?? "";
  assert.match(ordinaryConnectionStatus, /display:\s*none;/u);
  const mobileCacheState = mobileCascade.match(
    /\.connection-status\.catalog-cache-state\s*\{[\s\S]*?\}/u,
  )?.[0] ?? "";
  assert.match(mobileCacheState, /display:\s*(?:block|inline-flex|flex);/u);
  assert.doesNotMatch(mobileCacheState, /display:\s*none/u);
  assert.match(mobileCacheState, /position:\s*absolute;/u);
  assert.match(mobileCacheState, /width:\s*1px;/u);
  assert.match(mobileCacheState, /height:\s*1px;/u);
  assert.match(mobileCacheState, /clip-path:\s*inset\(50%\);/u);
  assert.match(css, /\.load-more\s*\{[\s\S]*?min-height:\s*44px;/u);
  assert.match(
    css,
    /@media\s*\(max-width:\s*47\.99rem\)[\s\S]*?\.pagination\s+\.load-more\s*\{[\s\S]*?width:\s*100%;/u,
  );
  assert.match(
    css,
    /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?scroll-behavior:\s*auto/u,
  );
});

test("narrow zoomed headers wrap without clipping touch controls", () => {
  const css = readFileSync(
    new URL("../../public/css/style.css", import.meta.url),
    "utf8",
  );
  const narrowCascade = css.match(
    /@media\s*\(max-width:\s*23\.74rem\)[\s\S]*?(?=\n@media|$)/u,
  )?.[0] ?? "";
  assert.match(narrowCascade, /\.app-header__main\s*\{[\s\S]*?display:\s*flex;/u);
  assert.match(narrowCascade, /\.header-tools\s*\{[\s\S]*?flex-wrap:\s*wrap;/u);
  assert.match(css, /\.search-box\s*\{[\s\S]*?max-width:\s*100%;/u);
  assert.match(css, /\.header-control\s*\{[\s\S]*?min-width:\s*44px;/u);
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
  runtimeCatalogStatus,
  runtimeLoadErrorMessage,
  startPreviewProbeRequest,
  tabKeyTargetIndex,
  upgradeLinkGroups,
} from "../../public/js/app.js";
import * as runtimeApplication from "../../public/js/app.js";

function runtimeVideo(number, overrides = {}) {
  return {
    code: `SPSF-${number}`,
    series: "SPSF",
    title: `Title ${number}`,
    actors: [],
    releaseDate: "2026-08-01",
    ...overrides,
  };
}

function runtimeStore({ recent = [], search = [] } = {}) {
  const series = new Map();
  let searchVideos = search;
  let tags = new Map();
  return {
    getRecentVideos: () => recent,
    getSeries: (code) => series.get(code) ?? null,
    getVideo(code) {
      return recent.concat(searchVideos).find((video) => video.code === code) ?? null;
    },
    search(query) {
      const needle = String(query ?? "").toLowerCase();
      return searchVideos.filter((video) => [
        video.code,
        video.title,
        ...(video.tagIds ?? []).flatMap((id) => {
          const tag = tags.get(id);
          return tag ? [tag.nameZh, tag.nameJa] : [];
        }),
      ].some((value) => String(value ?? "").toLowerCase().includes(needle)));
    },
    installSeries(payload) {
      series.set(payload.series.code, payload.series);
    },
    installSearch(payload) {
      searchVideos = payload.videos;
    },
    installTags(payload) {
      tags = new Map((payload?.tags ?? []).map((tag) => [tag.id, tag]));
    },
  };
}

function runtimeLoader({ series = [], search = [], tags = [] } = {}) {
  return {
    calls: [],
    setBootstrap(bootstrap) {
      this.bootstrap = bootstrap;
    },
    async ensureSeries(code) {
      this.calls.push(`series:${code}`);
      return { series: { code, videos: series } };
    },
    async ensureSearch() {
      this.calls.push("search");
      return { videos: search };
    },
    async ensureTags() {
      this.calls.push("tags");
      return { tags, assignments: [] };
    },
  };
}

test("progressive counters grow by one 24-card window and reset for changed contexts", () => {
  assert.equal(typeof runtimeApplication.increaseVisibleCount, "function");
  assert.deepEqual(
    runtimeApplication.createProgressiveCounters(["spsf", "TGS"]),
    {
      recent: 24,
      series: { SPSF: 24, TGS: 24 },
      search: 24,
      tags: 24,
      favorites: { 1: 24, 2: 24 },
    },
  );
  assert.equal(runtimeApplication.increaseVisibleCount(24, 60, 24), 48);
  assert.equal(runtimeApplication.increaseVisibleCount(48, 60, 24), 60);
  assert.equal(runtimeApplication.increaseVisibleCount(60, 60, 24), 60);

  assert.equal(typeof runtimeApplication.resetProgressiveCounter, "function");
  const visible = {
    recent: 48,
    search: 48,
    tags: 48,
    series: { SPSF: 48 },
    favorites: { 1: 48, 2: 48 },
  };
  runtimeApplication.resetProgressiveCounter(visible, { view: "recent" });
  runtimeApplication.resetProgressiveCounter(visible, { query: "SPSF" });
  runtimeApplication.resetProgressiveCounter(visible, { series: "SPSF" });

  assert.equal(visible.recent, 24);
  assert.equal(visible.search, 24);
  assert.equal(visible.series.SPSF, 24);
  assert.equal(visible.tags, 48);
  assert.equal(visible.favorites[1], 48);
});

test("load-more labels announce the next visible count", () => {
  assert.equal(typeof runtimeApplication.progressiveLoadMoreLabel, "function");
  assert.equal(
    runtimeApplication.progressiveLoadMoreLabel(48),
    "加载更多，显示至 48 部",
  );
});

test("runtime activation fetches the tag index before rendering tag-aware search results", async () => {
  assert.equal(typeof runtimeApplication.activateSeries, "function");
  assert.equal(typeof runtimeApplication.activateSearch, "function");
  const render = { calls: [], render(value) { this.calls.push(value); } };
  const seriesVideos = Array.from({ length: 30 }, (_, index) => runtimeVideo(index + 1));
  const loader = runtimeLoader({
    series: seriesVideos,
    search: [runtimeVideo(61)],
  });
  const store = runtimeStore();

  await runtimeApplication.activateSeries({
    code: "SPSF",
    loader,
    store,
    render: (value) => render.render(value),
  });
  assert.deepEqual(loader.calls, ["series:SPSF"]);
  assert.equal(render.calls.at(-1).videos.length, 24);

  loader.calls.length = 0;
  await runtimeApplication.activateSearch({
    query: "SPSF-61",
    loader,
    store,
    render: (value) => render.render(value),
  });
  assert.deepEqual(loader.calls, ["series:SPSF", "search", "tags"]);
  assert.deepEqual(render.calls.at(-1).videos.map((video) => video.code), ["SPSF-61"]);
});

test("production search activation matches Chinese and Japanese tag names after tag installation", async () => {
  const tagged = runtimeVideo(62, { tagIds: [7] });
  const loader = runtimeLoader({
    search: [tagged],
    tags: [{ id: 7, nameZh: "战士", nameJa: "戦士", group: "character" }],
  });
  const store = runtimeStore({ search: [tagged] });
  const renders = [];

  for (const query of ["战士", "戦士"]) {
    await runtimeApplication.activateSearch({
      query,
      loader,
      store,
      render: (value) => renders.push(value),
    });
    assert.deepEqual(renders.at(-1).videos.map((video) => video.code), ["SPSF-62"]);
  }
  assert.deepEqual(loader.calls, ["search", "tags", "search", "tags"]);
});

test("view activation resets its target progressive context and favorite changes reset both groups", () => {
  assert.equal(typeof runtimeApplication.resetViewProgressiveCounter, "function");
  assert.equal(typeof runtimeApplication.resetFavoriteProgressiveCounters, "function");
  const visible = {
    recent: 48,
    search: 48,
    tags: 48,
    series: { SPSF: 48 },
    favorites: { 1: 48, 2: 48 },
  };

  runtimeApplication.resetViewProgressiveCounter(visible, "recent");
  runtimeApplication.resetViewProgressiveCounter(visible, "all", "SPSF");
  runtimeApplication.resetViewProgressiveCounter(visible, "favorites");
  runtimeApplication.resetFavoriteProgressiveCounters(visible, 2, 0);

  assert.equal(visible.recent, 24);
  assert.equal(visible.series.SPSF, 24);
  assert.equal(visible.favorites[1], 24);
  assert.equal(visible.favorites[2], 24);
});

test("retrying a failed tag activation re-renders the current query result", async () => {
  assert.equal(typeof runtimeApplication.createTagRetryHandler, "function");
  let attempts = 0;
  const renders = [];
  const handler = runtimeApplication.createTagRetryHandler({
    ensureTags: async () => {
      attempts += 1;
      return attempts > 1;
    },
    getQuery: () => "战士",
    getView: () => "recent",
    renderSearch: () => renders.push("search"),
    renderTags: () => renders.push("tags"),
  });

  assert.equal(await handler.retry(), false);
  assert.deepEqual(renders, []);
  assert.equal(await handler.retry(), true);
  assert.deepEqual(renders, ["search"]);
});

test("stale resource-load cleanup cannot clear a newer generation", () => {
  assert.equal(typeof runtimeApplication.createResourceLoadTracker, "function");
  const tracker = runtimeApplication.createResourceLoadTracker();
  const oldLoad = tracker.begin();
  tracker.nextGeneration();
  const freshLoad = tracker.begin();

  assert.equal(tracker.end(oldLoad), false);
  assert.equal(tracker.activeCount, 1);
  assert.equal(tracker.end(freshLoad), true);
  assert.equal(tracker.activeCount, 0);
});

test("detail hydration deduplicates pending opens and retries after a bounded failure", async () => {
  assert.equal(typeof runtimeApplication.createDetailHydrationController, "function");
  assert.equal(typeof runtimeApplication.resolveDetailDialogTrigger, "function");
  assert.deepEqual(runtimeApplication.detailRetryAction("spsf-61"), {
    action: "retry-detail",
    code: "SPSF-61",
  });
  assert.equal(runtimeApplication.detailRetryAction("https://private.example/secret"), null);
  const unloaded = runtimeVideo(61);
  const store = runtimeStore({ search: [unloaded] });
  const calls = [];
  let attempts = 0;
  const loader = {
    async ensureSeries(code) {
      attempts += 1;
      calls.push(code);
      if (attempts === 1) throw new Error("https://private.example/secret-url");
      return { series: { code, videos: [unloaded] } };
    },
  };
  const failures = [];
  const loading = [];
  const retryControl = {
    disabled: false,
    setAttribute() {},
    removeAttribute() {},
  };
  const trigger = {
    disabled: false,
    setAttribute() {},
    removeAttribute() {},
  };
  assert.equal(
    runtimeApplication.resolveDetailDialogTrigger(trigger, retryControl),
    trigger,
  );
  const controller = runtimeApplication.createDetailHydrationController({
    loader,
    store,
    onLoading: (code, active) => loading.push([code, active]),
    onFailure: (code) => failures.push(code),
  });

  const first = controller.open(unloaded.code, trigger, {
    controls: [retryControl],
  });
  const duplicate = controller.open(unloaded.code, trigger, {
    controls: [retryControl],
  });
  assert.equal(first, duplicate);
  assert.equal(trigger.disabled, true);
  assert.equal(retryControl.disabled, true);
  assert.equal(await first, null);
  assert.deepEqual(calls, ["SPSF"]);
  assert.deepEqual(loading, [[unloaded.code, true], [unloaded.code, false]]);
  assert.equal(trigger.disabled, false);
  assert.equal(retryControl.disabled, false);
  assert.equal(failures.length, 1);
  assert.equal(failures[0], unloaded.code);

  const retried = await controller.open(unloaded.code, trigger, {
    controls: [retryControl],
  });
  assert.equal(retried.code, unloaded.code);
  assert.deepEqual(calls, ["SPSF", "SPSF"]);
});

test("tag resource activation is deduplicated and exposes a public retry action", async () => {
  assert.equal(typeof runtimeApplication.createTagActivationController, "function");
  assert.equal(typeof runtimeApplication.tagRetryAction, "function");
  const store = runtimeStore();
  let attempts = 0;
  const loader = {
    async ensureSearch() {
      return { videos: [] };
    },
    async ensureTags() {
      attempts += 1;
      if (attempts === 1) throw new Error("private tag source");
      return { tags: ["日本語"], assignments: [] };
    },
  };
  const controller = runtimeApplication.createTagActivationController({
    loader,
    store,
  });
  const first = controller.ensure();
  const duplicate = controller.ensure();
  await assert.rejects(first, /private tag source/u);
  await assert.rejects(duplicate, /private tag source/u);
  assert.deepEqual(runtimeApplication.tagRetryAction(), {
    action: "retry-tags",
  });
  await controller.ensure();
  assert.equal(attempts, 2);
});

test("aborted detail hydration restores its trigger without exposing a retry error", async () => {
  const unloaded = runtimeVideo(63);
  const store = runtimeStore({ search: [unloaded] });
  const trigger = {
    disabled: false,
    setAttribute() {},
    removeAttribute() {},
  };
  const failures = [];
  const controller = runtimeApplication.createDetailHydrationController({
    loader: {
      async ensureSeries() {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      },
    },
    store,
    onFailure: (code) => failures.push(code),
  });

  assert.equal(await controller.open(unloaded.code, trigger), null);
  assert.equal(trigger.disabled, false);
  assert.deepEqual(failures, []);
});

test("runtime activation keeps startup lazy and hydrates only non-recent detail results", async () => {
  assert.equal(typeof runtimeApplication.activateBootstrap, "function");
  assert.equal(typeof runtimeApplication.activateFavorites, "function");
  assert.equal(typeof runtimeApplication.activateTags, "function");
  assert.equal(typeof runtimeApplication.hydrateDetailVideo, "function");
  const recent = [runtimeVideo(1)];
  const unloaded = runtimeVideo(61);
  const bootstrap = {
    generation: "a".repeat(64),
    generatedAt: "2026-08-29T00:00:00Z",
    series: [{ code: "SPSF" }],
  };
  const loader = runtimeLoader({ series: [unloaded], search: [unloaded] });
  const store = runtimeStore({ recent });
  const render = { calls: [], render(value) { this.calls.push(value); } };
  const state = {
    visible: {
      recent: 48,
      search: 48,
      tags: 48,
      series: { SPSF: 48 },
      favorites: { 1: 48, 2: 48 },
    },
    preferences: { selectedSeries: "SPSF" },
  };

  runtimeApplication.activateBootstrap({
    bootstrap,
    loader,
    state,
    createStore: () => store,
    render: (value) => render.render(value),
  });
  assert.deepEqual(loader.calls, []);
  assert.equal(loader.bootstrap, bootstrap);
  assert.equal(render.calls.at(-1).asOfDate, "2026-08-29");
  assert.equal(state.visible.recent, 24);

  await runtimeApplication.activateFavorites({
    loader,
    store,
    render: (value) => render.render(value),
    favorites: [],
  });
  assert.deepEqual(loader.calls, ["search"]);

  loader.calls.length = 0;
  await runtimeApplication.activateTags({
    loader,
    store,
    render: (value) => render.render(value),
  });
  assert.deepEqual(loader.calls, ["search", "tags"]);

  loader.calls.length = 0;
  assert.equal(
    await runtimeApplication.hydrateDetailVideo({
      code: recent[0].code,
      loader,
      store,
    }),
    recent[0],
  );
  assert.deepEqual(loader.calls, []);

  await runtimeApplication.activateSearch({
    query: unloaded.code,
    loader,
    store,
    render: (value) => render.render(value),
  });
  loader.calls.length = 0;
  assert.equal(
    (await runtimeApplication.hydrateDetailVideo({
      code: unloaded.code,
      loader,
      store,
    })).code,
    unloaded.code,
  );
  assert.deepEqual(loader.calls, ["series:SPSF"]);
});

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
      vidara: "https://vidara.example/watch",
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
            provider: "vidara",
            slot: "standard.vidara",
            label: "Vidara",
            url: "https://vidara.example/watch",
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
