import { createCatalogModel } from "./catalog.js";
import {
  derivePreviewUrls,
  mountSeries,
  normalizeFeaturedCovers,
  observeDeferredCovers,
  optimizedCoverUrl,
  renderSearchResults,
  renderSeriesShell,
  unmountSeries,
} from "./render.js";
import { createFavoritesStore, getFavoriteVideos } from "./favorites.js";
import { createLazyTagLoader } from "./runtime-tags.js";
import {
  createResolvedLinkLoader,
  resolveLinkTarget,
} from "./resolved-links.js";

export const UI_STORAGE_KEY = "giga_catalog_ui_v1";

const SEARCH_DELAY_MS = 180;
const FEATURED_COVERS_TIMEOUT_MS = 2000;
const RECENT_SERIES_COUNT = 6;
const PREVIEW_BATCH_SIZE = 4;
const loadResolvedLinks = createResolvedLinkLoader();
const PROVIDERS = Object.freeze([
  ["streamtape", "Streamtape"],
  ["player4me", "Player4me"],
  ["gofile", "Gofile"],
]);
const FAVORITE_PRESENTATION = Object.freeze({
  0: { label: "加入想看", shortLabel: "未收藏" },
  1: { label: "标记为已看", shortLabel: "想看" },
  2: { label: "移出收藏", shortLabel: "已看" },
});
const COVER_FALLBACK =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='480' viewBox='0 0 320 480'%3E%3Crect width='320' height='480' fill='%23192536'/%3E%3Cpath d='M78 198h164v84H78z' fill='none' stroke='%239cabbb' stroke-width='4'/%3E%3Cpath d='m96 264 38-38 28 26 24-20 38 32' fill='none' stroke='%239cabbb' stroke-width='4'/%3E%3C/svg%3E";

export function tabKeyTargetIndex(key, currentIndex, tabCount) {
  if (
    !Number.isInteger(currentIndex) ||
    !Number.isInteger(tabCount) ||
    tabCount <= 0 ||
    currentIndex < 0 ||
    currentIndex >= tabCount
  ) {
    return null;
  }
  if (key === "ArrowRight") {
    return (currentIndex + 1) % tabCount;
  }
  if (key === "ArrowLeft") {
    return (currentIndex - 1 + tabCount) % tabCount;
  }
  if (key === "Home") {
    return 0;
  }
  if (key === "End") {
    return tabCount - 1;
  }
  return null;
}

export function applyRenderFocus(main, { focusMain = false } = {}) {
  if (!focusMain || typeof main?.focus !== "function") {
    return false;
  }
  main.focus({ preventScroll: true });
  return true;
}

export function bindDebouncedSearchInput({
  input,
  clearButton,
  state,
  render,
  clearTimer = globalThis.clearTimeout,
  setTimer = globalThis.setTimeout,
  delayMs = SEARCH_DELAY_MS,
}) {
  input.addEventListener("input", () => {
    clearTimer(state.searchTimer);
    clearButton.hidden = !input.value;
    state.searchTimer = setTimer(() => {
      state.query = input.value.trim();
      state.searchStart = 0;
      render();
    }, delayMs);
  });
}

export function clearActiveSearch({
  state,
  input,
  clearButton,
  clearTimer = globalThis.clearTimeout,
}) {
  clearTimer(state.searchTimer);
  state.searchTimer = null;
  state.query = "";
  state.searchStart = 0;
  input.value = "";
  clearButton.hidden = true;
}

function isRecord(value) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function safeHttpUrl(value) {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    /[\u0000-\u001f\u007f]/u.test(value)
  ) {
    return null;
  }
  try {
    const parsed = new URL(value.trim());
    return ["http:", "https:"].includes(parsed.protocol) && parsed.host
      ? parsed.href
      : null;
  } catch {
    return null;
  }
}

/** Read the optional LCP manifest without ever making the catalog wait indefinitely. */
export async function loadFeaturedCovers(
  fetcher = globalThis.fetch,
  { signal, timeoutMs = FEATURED_COVERS_TIMEOUT_MS } = {},
) {
  if (typeof fetcher !== "function") {
    return new Map();
  }
  const controller = new AbortController();
  let resolveAborted;
  const aborted = new Promise((resolve) => {
    resolveAborted = () => {
      controller.abort();
      resolve(new Map());
    };
  });
  const abortFromCaller = () => resolveAborted();
  if (signal) {
    if (signal.aborted) {
      abortFromCaller();
    } else {
      signal.addEventListener("abort", abortFromCaller, { once: true });
    }
  }
  const request = Promise.resolve()
    .then(() => fetcher(new URL("../data/featured-covers.json", import.meta.url), {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    }))
    .then(async (response) => {
      if (!response?.ok) {
        return new Map();
      }
      try {
        return normalizeFeaturedCovers(await response.json());
      } catch {
        return new Map();
      }
    })
    .catch(() => new Map());
  const delay = Number.isFinite(timeoutMs) && timeoutMs >= 0
    ? timeoutMs
    : FEATURED_COVERS_TIMEOUT_MS;
  let timer;
  const deadline = new Promise((resolve) => {
    timer = globalThis.setTimeout(() => {
      resolveAborted();
      resolve(new Map());
    }, delay);
  });
  try {
    return await Promise.race([request, deadline, aborted]);
  } finally {
    globalThis.clearTimeout(timer);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export function densityToggleLabel(currentDensity) {
  return currentDensity === "compact"
    ? "切换到舒适布局"
    : "切换到紧凑布局";
}

export function applyCoverFallback(image) {
  if (!image || image.dataset?.fallback === "true") {
    return;
  }
  const original = safeHttpUrl(image.dataset?.originalSrc);
  if (original && image.dataset.originalRetried !== "true") {
    image.dataset.originalRetried = "true";
    image.src = original;
    return;
  }
  image.dataset.fallback = "true";
  image.src = COVER_FALLBACK;
  image.alt = "封面加载失败";
  image.classList.add("is-error");
}

export function loadUiPreferences(
  storage = globalThis.localStorage,
  prefersDark = globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches ??
    true,
) {
  const fallback = {
    theme: prefersDark ? "dark" : "light",
    density: "comfortable",
    selectedSeries: "",
    slots: {},
  };
  let parsed;
  try {
    parsed = JSON.parse(storage?.getItem(UI_STORAGE_KEY));
  } catch {
    return fallback;
  }
  if (!isRecord(parsed)) {
    return fallback;
  }
  const slots = {};
  if (isRecord(parsed.slots)) {
    for (const [rawCode, value] of Object.entries(parsed.slots)) {
      const code = rawCode.normalize("NFKC").trim().toUpperCase();
      if (code && typeof value === "boolean") {
        slots[code] = value;
      }
    }
  }
  return {
    theme: parsed.theme === "light" || parsed.theme === "dark"
      ? parsed.theme
      : fallback.theme,
    density: parsed.density === "compact" ? "compact" : "comfortable",
    selectedSeries:
      typeof parsed.selectedSeries === "string"
        ? parsed.selectedSeries.normalize("NFKC").trim().toUpperCase()
        : "",
    slots,
  };
}

export function collectLinkGroups(links) {
  if (!isRecord(links)) {
    return [];
  }
  const groups = [];
  const collectProviders = (source) => {
    if (!isRecord(source)) {
      return [];
    }
    return PROVIDERS.flatMap(([provider, label]) => {
      const url = safeHttpUrl(source[provider]);
      return url ? [{ provider, label, url }] : [];
    });
  };
  const standard = collectProviders(links);
  const uncensored = collectProviders(links.uncensored);
  const subtitleUrl = safeHttpUrl(links.subtitle);
  if (standard.length) {
    groups.push({ key: "standard", label: "普通版", links: standard });
  }
  if (uncensored.length) {
    groups.push({
      key: "uncensored",
      label: "无码版",
      links: uncensored,
    });
  }
  if (subtitleUrl) {
    groups.push({
      key: "subtitle",
      label: "字幕",
      links: [
        {
          provider: "subtitle",
          label: "字幕",
          url: subtitleUrl,
        },
      ],
    });
  }
  return groups;
}

export async function upgradeLinkGroups(videoCode, groups, manifest) {
  return Promise.all(
    groups.map(async (group) => ({
      ...group,
      links: await Promise.all(
        group.links.map(async (item) => ({
          ...item,
          ...await resolveLinkTarget(
            {
              code: videoCode,
              provider: item.provider,
              label: item.label,
              sourceUrl: item.url,
            },
            manifest,
          ),
        })),
      ),
    })),
  );
}

export function normalizeSubtitleDirectoryResource(resources) {
  if (!isRecord(resources) || !isRecord(resources.subtitleDirectory)) {
    return null;
  }
  const label = typeof resources.subtitleDirectory.label === "string"
    ? resources.subtitleDirectory.label.trim()
    : "";
  const url = safeHttpUrl(resources.subtitleDirectory.url);
  return label && url ? Object.freeze({ label, url }) : null;
}

export function createPreviewIntersectionObserver(
  Observer,
  rail,
  sentinel,
  onVisible,
) {
  if (
    typeof Observer !== "function" ||
    !rail ||
    !sentinel ||
    typeof onVisible !== "function"
  ) {
    return null;
  }
  const observer = new Observer(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        onVisible();
      }
    },
    { root: rail, rootMargin: "0px 160px 0px 0px", threshold: 0.01 },
  );
  observer.observe(sentinel);
  return observer;
}

export function attachPreviewProbe(
  image,
  { onSuccess, onFailure } = {},
) {
  if (
    !image ||
    typeof image.addEventListener !== "function" ||
    typeof onSuccess !== "function" ||
    typeof onFailure !== "function"
  ) {
    return false;
  }
  let settled = false;
  const settle = (callback) => {
    if (settled) {
      return;
    }
    settled = true;
    callback();
  };
  image.addEventListener("load", () => settle(onSuccess), { once: true });
  image.addEventListener("error", () => settle(onFailure), { once: true });
  return true;
}

export function startPreviewProbeRequest(
  image,
  source,
  { onSuccess, onFailure } = {},
) {
  const safeSource = safeHttpUrl(source);
  if (
    !safeSource ||
    !attachPreviewProbe(image, { onSuccess, onFailure })
  ) {
    return false;
  }
  image.src = safeSource;
  return true;
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) {
    element.className = className;
  }
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : "—";
}

function formatDate(value) {
  if (typeof value !== "string" || !value) {
    return "尚未同步";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}

function startApplication() {
  const ui = {
    main: document.querySelector("#main-content"),
    search: document.querySelector("#catalog-search"),
    searchClear: document.querySelector("#search-clear"),
    summaryVideos: document.querySelector("#summary-videos"),
    summarySeries: document.querySelector("#summary-series"),
    summaryLinked: document.querySelector("#summary-linked"),
    summaryAdded: document.querySelector("#summary-added"),
    syncTime: document.querySelector("#sync-time"),
    connectionStatus: document.querySelector("#connection-status"),
    subtitleDirectory: document.querySelector("#subtitle-directory-link"),
    seriesRail: document.querySelector("#series-rail-list"),
    seriesDrawer: document.querySelector("#series-drawer"),
    seriesDrawerList: document.querySelector("#series-drawer-list"),
    videoDialog: document.querySelector("#video-detail"),
    videoDialogContent: document.querySelector("#video-detail-content"),
    toast: document.querySelector("#toast"),
    toastMessage: document.querySelector("#toast-message"),
    backToTop: document.querySelector("#back-to-top"),
    pageTop: document.querySelector("#page-top"),
    favoriteCount: document.querySelector("#favorite-count"),
    themeToggle: document.querySelector("#theme-toggle"),
    densityToggle: document.querySelector("#density-toggle"),
  };
  if (Object.values(ui).some((element) => !element)) {
    return;
  }

  const preferences = loadUiPreferences(
    globalThis.localStorage,
    globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true,
  );
  const favorites = createFavoritesStore(globalThis.localStorage);
  const state = {
    model: null,
    payload: null,
    featuredCovers: new Map(),
    view: "recent",
    query: "",
    mountedSeries: null,
    searchStart: 0,
    favoriteStarts: { 1: 0, 2: 0 },
    preferences,
    fetchController: null,
    searchTimer: null,
    toastTimer: null,
    dialogTrigger: null,
    drawerTrigger: null,
    previewObserver: null,
    previewRail: null,
    previewSentinel: null,
    previewVideo: null,
    previewStart: 0,
    previewLoading: false,
    coverObserver: null,
    tagInclude: new Set(),
    tagExclude: new Set(),
    tagMatch: "all",
    tagSearch: "",
    tagSort: "newest",
    tagStart: 0,
    tagsReady: false,
    tagError: null,
    ensureTags: null,
    activeDialogCode: null,
  };

  function persistPreferences() {
    try {
      localStorage.setItem(UI_STORAGE_KEY, JSON.stringify(state.preferences));
    } catch {
      // Preferences remain usable for this session when storage is blocked.
    }
  }

  function applyPreferences() {
    document.documentElement.dataset.theme = state.preferences.theme;
    document.documentElement.dataset.density = state.preferences.density;
    const light = state.preferences.theme === "light";
    ui.themeToggle.setAttribute("aria-pressed", String(light));
    ui.themeToggle.setAttribute(
      "aria-label",
      light ? "切换到深色主题" : "切换到浅色主题",
    );
    ui.themeToggle.querySelector("[data-control-label]").textContent = light
      ? "深色"
      : "浅色";
    const compact = state.preferences.density === "compact";
    ui.densityToggle.setAttribute("aria-pressed", String(compact));
    ui.densityToggle.setAttribute(
      "aria-label",
      densityToggleLabel(state.preferences.density),
    );
    ui.densityToggle.querySelector("[data-control-label]").textContent = compact
      ? "舒适"
      : "紧凑";
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    ui.toastMessage.textContent = message;
    ui.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      ui.toast.hidden = true;
    }, 3200);
  }

  function updateFavoriteCount() {
    ui.favoriteCount.textContent = formatNumber(favorites.getCount());
  }

  function renderLoading() {
    ui.main.setAttribute("aria-busy", "true");
    ui.main.innerHTML = [
      '<section class="state-panel loading-panel" aria-labelledby="loading-title">',
      '<span class="state-kicker">正在整理展柜</span>',
      '<h2 id="loading-title">载入影片目录…</h2>',
      '<p>只挂载首个系列，完整数据在内存中建立索引。</p>',
      '<div class="skeleton-grid" aria-hidden="true">',
      '<span class="skeleton-card"></span><span class="skeleton-card"></span>',
      '<span class="skeleton-card"></span><span class="skeleton-card"></span>',
      "</div></section>",
    ].join("");
  }

  function renderTagLoadState(error = null) {
    ui.main.setAttribute("aria-busy", String(!error));
    const panel = createElement("section", `state-panel ${error ? "error-panel" : "loading-panel"}`);
    panel.append(
      createElement("span", "state-kicker", error ? "标签暂不可用" : "按需加载"),
      createElement("h2", "", error ? "标签载入失败" : "正在载入中文标签…"),
      createElement(
        "p",
        "",
        error
          ? "影片目录仍可正常浏览，可以重新载入标签。"
          : "基础目录已经就绪，只在需要时下载标签索引。",
      ),
    );
    if (error) {
      const retry = createElement("button", "button button--primary", "重新载入标签");
      retry.type = "button";
      retry.dataset.action = "retry-tags";
      panel.append(retry);
    }
    ui.main.replaceChildren(panel);
  }

  function renderLoadError(error) {
    ui.main.removeAttribute("aria-busy");
    const panel = createElement("section", "state-panel error-panel");
    const kicker = createElement(
      "span",
      "state-kicker",
      navigator.onLine === false ? "当前离线" : "目录暂不可用",
    );
    const heading = createElement("h2", "", "没有用空目录掩盖错误");
    const message = createElement(
      "p",
      "",
      navigator.onLine === false
        ? "网络恢复后可重试，上一份正常数据不会被自动任务覆盖。"
        : "数据文件加载失败，请稍后重试。",
    );
    const detail = createElement(
      "p",
      "error-detail",
      error instanceof Error ? error.message : String(error),
    );
    const retry = createElement("button", "button button--primary", "重新载入");
    retry.type = "button";
    retry.dataset.action = "retry";
    panel.append(kicker, heading, message, detail, retry);
    ui.main.replaceChildren(panel);
  }

  function updateSummary() {
    const metadata = state.model.metadata;
    const totals = metadata.totals ?? {};
    const counts = metadata.refresh?.counts ?? {};
    ui.summaryVideos.textContent = formatNumber(totals.videos);
    ui.summarySeries.textContent = formatNumber(totals.series);
    ui.summaryLinked.textContent = formatNumber(totals.linkedVideos);
    ui.summaryAdded.textContent = `+${formatNumber(counts.added ?? 0)}`;
    ui.syncTime.textContent = formatDate(metadata.generatedAt);
    const subtitleDirectory = normalizeSubtitleDirectoryResource(
      metadata.resources,
    );
    if (subtitleDirectory) {
      ui.subtitleDirectory.textContent = subtitleDirectory.label;
      ui.subtitleDirectory.href = subtitleDirectory.url;
      ui.subtitleDirectory.hidden = false;
    } else {
      ui.subtitleDirectory.removeAttribute("href");
      ui.subtitleDirectory.hidden = true;
    }
  }

  function renderSeriesNavigation() {
    const series = state.model.getRecentSeries();
    const buildList = (container) => {
      const fragment = document.createDocumentFragment();
      for (const item of series) {
        const button = createElement("button", "series-index__item");
        button.type = "button";
        button.dataset.action = "select-series";
        button.dataset.series = item.code;
        button.setAttribute(
          "aria-current",
          state.preferences.selectedSeries === item.code ? "true" : "false",
        );
        button.append(
          createElement("span", "series-index__code", item.code),
          createElement(
            "span",
            "series-index__count",
            `${formatNumber(item.count ?? item.videos.length)} 部`,
          ),
        );
        fragment.append(button);
      }
      container.replaceChildren(fragment);
    };
    buildList(ui.seriesRail);
    buildList(ui.seriesDrawerList);
  }

  function updateSeriesSelection() {
    for (const button of document.querySelectorAll("[data-action='select-series']")) {
      button.setAttribute(
        "aria-current",
        String(button.dataset.series === state.preferences.selectedSeries),
      );
    }
  }

  function updateTabs() {
    for (const tab of document.querySelectorAll("[role='tab'][data-view]")) {
      const selected = tab.dataset.view === state.view && !state.query;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
  }

  function appendPagination(container, metadata, context) {
    if (!metadata.hasPrevious && !metadata.hasMore) {
      return;
    }
    const nav = createElement("nav", "pagination");
    nav.setAttribute("aria-label", "分页");
    const status = createElement(
      "span",
      "pagination__status",
      `${formatNumber(metadata.start + 1)}–${formatNumber(metadata.end)} / ${formatNumber(metadata.total)}`,
    );
    const previous = createElement("button", "button button--quiet", "上一段");
    previous.type = "button";
    previous.disabled = !metadata.hasPrevious;
    previous.dataset.action = context.action;
    previous.dataset.start = String(metadata.previousStart);
    if (context.series) {
      previous.dataset.series = context.series;
    }
    if (context.favoriteState) {
      previous.dataset.favoriteState = String(context.favoriteState);
    }
    const next = createElement("button", "button button--quiet", "下一段");
    next.type = "button";
    next.disabled = !metadata.hasMore;
    next.dataset.action = context.action;
    next.dataset.start = String(metadata.nextStart);
    if (context.series) {
      next.dataset.series = context.series;
    }
    if (context.favoriteState) {
      next.dataset.favoriteState = String(context.favoriteState);
    }
    nav.append(previous, status, next);
    container.append(nav);
  }

  function findSeriesMount(code) {
    return [...ui.main.querySelectorAll("[data-series-mount]")].find(
      (element) => element.dataset.seriesMount === code,
    );
  }

  function resetShellToggles() {
    for (const button of ui.main.querySelectorAll("[data-action='mount-series']")) {
      button.setAttribute("aria-expanded", "false");
      button.textContent = "展开系列";
    }
  }

  function mountSeriesWindow(code, start = 0) {
    const series = state.model.getSeries(code);
    const container = findSeriesMount(code);
    if (!series || !container) {
      return;
    }
    resetShellToggles();
    const mode = state.preferences.slots[code] ? "slots" : "real-only";
    const metadata = mountSeries(container, series, {
      mode,
      start,
      featuredCovers: state.featuredCovers,
      tagLookup: (tagId) => state.model.getTag(tagId),
    });
    const toolbar = createElement("div", "series-controls");
    const modeButton = createElement(
      "button",
      "button button--quiet",
      mode === "slots" ? "仅显示现有影片" : "显示 01–99 编号格",
    );
    modeButton.type = "button";
    modeButton.dataset.action = "toggle-slots";
    modeButton.dataset.series = code;
    modeButton.setAttribute("aria-pressed", String(mode === "slots"));
    const windowNote = createElement(
      "span",
      "series-controls__note",
      mode === "slots"
        ? "缺号只在此系列显示，不会进入搜索或收藏。"
        : "当前仅显示真实影片。",
    );
    toolbar.append(modeButton, windowNote);
    container.prepend(toolbar);
    appendPagination(container, metadata, {
      action: "page-series",
      series: code,
    });
    const toggle = ui.main.querySelector(
      `[data-action="mount-series"][data-series="${code}"]`,
    );
    if (toggle) {
      toggle.setAttribute("aria-expanded", "true");
      toggle.textContent = "收起系列";
    }
    state.mountedSeries = code;
    state.preferences.selectedSeries = code;
    persistPreferences();
    updateSeriesSelection();
    refreshDeferredCovers();
  }

  function renderViewHeading(kicker, title, description) {
    const header = createElement("header", "view-heading");
    header.append(
      createElement("span", "state-kicker", kicker),
      createElement("h2", "", title),
      createElement("p", "", description),
    );
    return header;
  }

  function renderRecentView() {
    const recent = state.model.getRecentSeries(RECENT_SERIES_COUNT);
    const fragment = document.createDocumentFragment();
    fragment.append(
      renderViewHeading(
        "RECENT EDITIONS",
        "最近更新",
        "先陈列六个最近系列，只展开第一组影片，保持页面轻快。",
      ),
    );
    if (!recent.length) {
      fragment.append(createElement("p", "empty-state", "目录中暂无影片。"));
      ui.main.replaceChildren(fragment);
      return;
    }
    const shells = createElement("div", "recent-series");
    shells.innerHTML = recent.map((series) => renderSeriesShell(series)).join("");
    fragment.append(shells);
    ui.main.replaceChildren(fragment);
    state.mountedSeries = null;
    mountSeriesWindow(recent[0].code);
  }

  function renderAllSeriesView() {
    const allSeries = state.model.getRecentSeries();
    const selected =
      state.model.getSeries(state.preferences.selectedSeries) ?? allSeries[0] ?? null;
    const fragment = document.createDocumentFragment();
    fragment.append(
      renderViewHeading(
        "SERIES DIRECTORY",
        "全部系列",
        "从左侧索引或移动端目录选择一个系列，同一时间只挂载一组卡片。",
      ),
    );
    if (!selected) {
      fragment.append(createElement("p", "empty-state", "目录中暂无系列。"));
      ui.main.replaceChildren(fragment);
      return;
    }
    const shell = createElement("div", "selected-series");
    shell.innerHTML = renderSeriesShell(selected);
    fragment.append(shell);
    ui.main.replaceChildren(fragment);
    state.mountedSeries = null;
    mountSeriesWindow(selected.code);
  }

  function renderSearchView() {
    const results = state.model.search(state.query);
    const fragment = document.createDocumentFragment();
    fragment.append(
      renderViewHeading(
        "SEARCH RESULTS",
        `“${state.query}” 的结果`,
        `找到 ${formatNumber(results.length)} 部真实影片，不生成缺号卡片。`,
      ),
    );
    const container = createElement("div", "result-section");
    fragment.append(container);
    ui.main.replaceChildren(fragment);
    state.mountedSeries = null;
    const metadata = renderSearchResults(container, results, {
      start: state.searchStart,
      featuredCovers: state.featuredCovers,
      emptyMessage: "没有匹配的影片，试试番号、标题、演员、系列名或标签。",
      tagLookup: (tagId) => state.model.getTag(tagId),
    });
    appendPagination(container, metadata, { action: "page-results" });
    refreshDeferredCovers();
  }

  function renderFavoritesView() {
    const entries = getFavoriteVideos(
      favorites.getAll(),
      (code) => state.model.getVideo(code),
    );
    const fragment = document.createDocumentFragment();
    fragment.append(
      renderViewHeading(
        "LOCAL COLLECTION",
        "我的收藏",
        "收藏只保存在当前浏览器；缺失或已下架的番号会被安全忽略。",
      ),
    );
    if (!entries.length) {
      fragment.append(
        createElement(
          "p",
          "empty-state",
          "还没有收藏。打开任意影片，可在“想看 / 已看 / 未收藏”之间切换。",
        ),
      );
      ui.main.replaceChildren(fragment);
      state.mountedSeries = null;
      refreshDeferredCovers();
      return;
    }
    for (const favoriteState of [1, 2]) {
      const videos = entries
        .filter((entry) => entry.state === favoriteState)
        .map((entry) => entry.video);
      if (!videos.length) {
        continue;
      }
      const section = createElement("section", "favorite-group");
      const heading = createElement(
        "h3",
        "",
        `${FAVORITE_PRESENTATION[favoriteState].shortLabel} · ${formatNumber(videos.length)}`,
      );
      const container = createElement("div", "favorite-group__grid");
      section.append(heading, container);
      fragment.append(section);
      const metadata = renderSearchResults(container, videos, {
        context: "favorites",
        start: state.favoriteStarts[favoriteState],
        featuredCovers: state.featuredCovers,
        tagLookup: (tagId) => state.model.getTag(tagId),
      });
      appendPagination(container, metadata, {
        action: "page-favorites",
        favoriteState,
      });
    }
    ui.main.replaceChildren(fragment);
    state.mountedSeries = null;
    refreshDeferredCovers();
  }

  function tagSelectionState(tagId) {
    if (state.tagInclude.has(tagId)) {
      return "include";
    }
    return state.tagExclude.has(tagId) ? "exclude" : "neutral";
  }

  function cycleTagSelection(tagId) {
    const current = tagSelectionState(tagId);
    state.tagInclude.delete(tagId);
    state.tagExclude.delete(tagId);
    if (current === "neutral") {
      state.tagInclude.add(tagId);
    } else if (current === "include") {
      state.tagExclude.add(tagId);
    }
    state.tagStart = 0;
  }

  function createTagChip(tag) {
    const selected = tagSelectionState(tag.id);
    const chip = createElement("button", "tag-chip");
    chip.type = "button";
    chip.dataset.action = "cycle-tag";
    chip.dataset.tagId = String(tag.id);
    chip.dataset.state = selected;
    chip.setAttribute(
      "aria-pressed",
      selected === "exclude" ? "mixed" : String(selected === "include"),
    );
    const stateLabel =
      selected === "include" ? "已包含" : selected === "exclude" ? "已排除" : "未选";
    chip.setAttribute(
      "aria-label",
      `${tag.nameZh}，${formatNumber(tag.count)} 部，${stateLabel}；按下切换状态`,
    );
    chip.append(
      createElement("span", "tag-chip__name", tag.nameZh),
      createElement("span", "tag-chip__count", formatNumber(tag.count)),
    );
    return chip;
  }

  function sortTagResults(videos) {
    const sorted = [...videos];
    if (state.tagSort === "oldest") {
      sorted.sort(
        (left, right) =>
          String(left.releaseDate ?? "").localeCompare(String(right.releaseDate ?? "")) ||
          String(left.code).localeCompare(String(right.code)),
      );
    } else if (state.tagSort === "code") {
      sorted.sort((left, right) =>
        String(left.code).localeCompare(String(right.code), "en", { numeric: true }),
      );
    } else {
      sorted.sort(
        (left, right) =>
          String(right.releaseDate ?? "").localeCompare(String(left.releaseDate ?? "")) ||
          String(left.code).localeCompare(String(right.code)),
      );
    }
    return sorted;
  }

  function renderTagView() {
    const fragment = document.createDocumentFragment();
    fragment.append(
      renderViewHeading(
        "OFFICIAL TAG DIRECTORY",
        "中文标签索引",
        "完整收录 GIGA 官网的类型、玩法、角色和造型小标签。点击依次切换：包含 → 排除 → 取消。",
      ),
    );

    const toolbar = createElement("section", "tag-toolbar");
    toolbar.setAttribute("aria-label", "标签筛选设置");
    const searchLabel = createElement("label", "tag-toolbar__search");
    searchLabel.append(createElement("span", "visually-hidden", "搜索标签"));
    const search = createElement("input", "tag-search");
    search.type = "search";
    search.id = "tag-directory-search";
    search.placeholder = "搜索中文或日文标签";
    search.value = state.tagSearch;
    searchLabel.append(search);

    const matchLabel = createElement("label", "tag-toolbar__field");
    matchLabel.append(createElement("span", "", "包含方式"));
    const matchSelect = createElement("select", "tag-select");
    matchSelect.id = "tag-match-mode";
    for (const [value, label] of [["all", "同时包含全部"], ["any", "包含任意一个"]]) {
      const option = createElement("option", "", label);
      option.value = value;
      option.selected = state.tagMatch === value;
      matchSelect.append(option);
    }
    matchLabel.append(matchSelect);

    const sortLabel = createElement("label", "tag-toolbar__field");
    sortLabel.append(createElement("span", "", "结果排序"));
    const sortSelect = createElement("select", "tag-select");
    sortSelect.id = "tag-result-sort";
    for (const [value, label] of [["newest", "最新发布"], ["oldest", "最早发布"], ["code", "番号名称"]]) {
      const option = createElement("option", "", label);
      option.value = value;
      option.selected = state.tagSort === value;
      sortSelect.append(option);
    }
    sortLabel.append(sortSelect);
    const clear = createElement("button", "button button--quiet", "清空已选");
    clear.type = "button";
    clear.dataset.action = "clear-tags";
    clear.disabled = !state.tagInclude.size && !state.tagExclude.size;
    toolbar.append(searchLabel, matchLabel, sortLabel, clear);
    fragment.append(toolbar);

    const selected = createElement("div", "tag-selection");
    selected.setAttribute("aria-live", "polite");
    const selectionParts = [];
    for (const tagId of [...state.tagInclude].sort((a, b) => a - b)) {
      const tag = state.model.getTag(tagId);
      if (tag) selectionParts.push(`+ ${tag.nameZh}`);
    }
    for (const tagId of [...state.tagExclude].sort((a, b) => a - b)) {
      const tag = state.model.getTag(tagId);
      if (tag) selectionParts.push(`− ${tag.nameZh}`);
    }
    selected.textContent = selectionParts.length
      ? `已选：${selectionParts.join("  ")}`
      : "尚未选择标签";
    fragment.append(selected);

    const matchingIds = state.tagSearch
      ? new Set(state.model.searchTags(state.tagSearch).map((tag) => tag.id))
      : null;
    const groups = createElement("div", "tag-groups");
    for (const [group, title] of [["genre", "类型与玩法"], ["character", "角色与造型"]]) {
      const tags = state.model
        .getTags(group)
        .filter((tag) => !matchingIds || matchingIds.has(tag.id));
      const section = createElement("section", "tag-group");
      const heading = createElement("h3", "tag-group__title", `${title} · ${formatNumber(tags.length)}`);
      const cloud = createElement("div", "tag-cloud");
      for (const tag of tags) {
        cloud.append(createTagChip(tag));
      }
      if (!tags.length) {
        cloud.append(createElement("p", "empty-state empty-state--compact", "没有匹配标签"));
      }
      section.append(heading, cloud);
      groups.append(section);
    }
    fragment.append(groups);

    const resultSection = createElement("section", "tag-results");
    const hasSelection = state.tagInclude.size || state.tagExclude.size;
    if (!hasSelection) {
      resultSection.append(
        createElement("p", "empty-state empty-state--compact", "选择标签后，这里会按时间或番号显示影片。"),
      );
    } else {
      const videos = sortTagResults(
        state.model.filterByTags({
          include: [...state.tagInclude],
          exclude: [...state.tagExclude],
          match: state.tagMatch,
        }),
      );
      resultSection.append(
        createElement("h3", "tag-results__title", `匹配影片 · ${formatNumber(videos.length)}`),
      );
      const grid = createElement("div", "tag-results__grid");
      resultSection.append(grid);
      fragment.append(resultSection);
      ui.main.replaceChildren(fragment);
      state.mountedSeries = null;
      const metadata = renderSearchResults(grid, videos, {
        start: state.tagStart,
        context: "tags",
        tagLookup: (tagId) => state.model.getTag(tagId),
        emptyMessage: "没有同时满足条件的影片。",
      });
      appendPagination(grid, metadata, { action: "page-tag-results" });
      refreshDeferredCovers();
      return;
    }
    fragment.append(resultSection);
    ui.main.replaceChildren(fragment);
    state.mountedSeries = null;
    refreshDeferredCovers();
  }

  function renderCurrentView(options) {
    if (!state.model) {
      return;
    }
    ui.main.removeAttribute("aria-busy");
    document.body.dataset.view = state.query ? "search" : state.view;
    if (state.query) {
      renderSearchView();
      if (!state.tagsReady && !state.tagError) void ensureTagCatalog();
    } else if (state.view === "tags") {
      if (state.tagsReady) {
        renderTagView();
      } else {
        renderTagLoadState(state.tagError);
        if (!state.tagError) void ensureTagCatalog();
      }
    } else if (state.view === "all") {
      renderAllSeriesView();
    } else if (state.view === "favorites") {
      renderFavoritesView();
    } else {
      renderRecentView();
    }
    updateTabs();
    applyRenderFocus(ui.main, options);
  }

  function configureTagLoader() {
    state.tagsReady = false;
    state.tagError = null;
    state.ensureTags = createLazyTagLoader({
      getCore: () => state.payload,
      loadPayload: async () => {
        const response = await fetch(
          new URL("../data/catalog-tags.json", import.meta.url),
          {
            headers: { Accept: "application/json" },
            signal: state.fetchController?.signal,
          },
        );
        if (!response.ok) {
          throw new Error(`标签请求失败（HTTP ${response.status}）`);
        }
        return response.json();
      },
      onHydrated: (payload) => {
        state.payload = payload;
        state.model = createCatalogModel(payload);
        state.tagsReady = true;
        state.tagError = null;
      },
    });
  }

  async function ensureTagCatalog() {
    if (state.tagsReady) return true;
    try {
      await state.ensureTags?.();
      if (state.query || state.view === "tags") renderCurrentView();
      if (ui.videoDialog.open && state.activeDialogCode) {
        openVideoDialog(state.activeDialogCode, state.dialogTrigger);
      }
      return true;
    } catch (error) {
      if (error?.name === "AbortError") return false;
      state.tagError = error;
      if (!state.query && state.view === "tags") {
        renderTagLoadState(error);
        updateTabs();
      }
      return false;
    }
  }

  async function loadCatalog() {
    state.fetchController?.abort();
    state.fetchController = new AbortController();
    renderLoading();
    ui.connectionStatus.textContent =
      navigator.onLine === false ? "离线" : "同步目录";
    try {
      const catalogRequest = fetch(new URL("../data/catalog-core.json", import.meta.url), {
        headers: { Accept: "application/json" },
        signal: state.fetchController.signal,
      });
      const featuredCoversRequest = loadFeaturedCovers(fetch, {
        signal: state.fetchController.signal,
      });
      const response = await catalogRequest;
      if (!response.ok) {
        throw new Error(`目录请求失败（HTTP ${response.status}）`);
      }
      const payload = await response.json();
      if (!isRecord(payload) || !Array.isArray(payload.series)) {
        throw new Error("目录数据结构无效");
      }
      state.featuredCovers = await featuredCoversRequest;
      state.payload = payload;
      state.model = createCatalogModel(payload);
      configureTagLoader();
      const fallbackSeries = state.model.getRecentSeries(1)[0]?.code ?? "";
      if (!state.model.getSeries(state.preferences.selectedSeries)) {
        state.preferences.selectedSeries = fallbackSeries;
        persistPreferences();
      }
      updateSummary();
      renderSeriesNavigation();
      renderCurrentView();
      ui.connectionStatus.textContent = "数据已就绪";
    } catch (error) {
      if (error?.name !== "AbortError") {
        renderLoadError(error);
        ui.connectionStatus.textContent =
          navigator.onLine === false ? "当前离线" : "载入失败";
      }
    }
  }

  function selectSeries(code) {
    if (!state.model?.getSeries(code)) {
      return;
    }
    state.query = "";
    state.searchStart = 0;
    ui.search.value = "";
    ui.searchClear.hidden = true;
    state.view = "all";
    state.preferences.selectedSeries = code;
    persistPreferences();
    renderCurrentView({ focusMain: true });
  }

  function clearSearch({ focus = false, focusMain = false } = {}) {
    window.clearTimeout(state.searchTimer);
    state.query = "";
    state.searchStart = 0;
    ui.search.value = "";
    ui.searchClear.hidden = true;
    renderCurrentView({ focusMain });
    if (focus) {
      ui.search.focus();
    }
  }

  function closeDrawer() {
    if (ui.seriesDrawer.open) {
      ui.seriesDrawer.close();
    }
  }

  function disconnectPreviewObserver() {
    state.previewObserver?.disconnect();
    state.previewObserver = null;
    state.previewRail = null;
    state.previewSentinel = null;
    state.previewVideo = null;
    state.previewStart = 0;
    state.previewLoading = false;
  }

  function refreshDeferredCovers() {
    state.coverObserver?.disconnect();
    state.coverObserver = observeDeferredCovers(
      window.IntersectionObserver,
      ui.main,
    );
  }

  function replacePreviewWithError(image) {
    const error = createElement("div", "preview-error", "预览图加载失败");
    error.setAttribute("role", "img");
    error.setAttribute("aria-label", "预览图加载失败");
    image.replaceWith(error);
  }

  function appendPreviewBatch(
    limit = PREVIEW_BATCH_SIZE,
    { probe = false } = {},
  ) {
    if (
      !ui.videoDialog.open ||
      !state.previewVideo ||
      !state.previewRail ||
      state.previewLoading
    ) {
      return [];
    }
    state.previewLoading = true;
    const urls = derivePreviewUrls(state.previewVideo, {
      start: state.previewStart,
      limit,
    });
    const images = [];
    for (const [offset, url] of urls.entries()) {
      const figure = createElement("figure", "preview-item");
      const image = createElement("img", "preview-image");
      image.alt = `${state.previewVideo.code} 预览 ${state.previewStart + offset + 1}`;
      image.width = 480;
      image.height = 320;
      image.decoding = "async";
      if (probe && offset === 0) {
        image.loading = "eager";
        image.dataset.previewProbe = "true";
        image.dataset.previewSource = url;
      } else {
        image.loading = "lazy";
        image.src = url;
      }
      figure.append(image);
      state.previewRail.insertBefore(figure, state.previewSentinel);
      images.push(image);
    }
    state.previewStart += urls.length;
    state.previewLoading = false;
    if (!urls.length || state.previewStart >= Number(state.previewVideo.previewCount)) {
      state.previewObserver?.disconnect();
      state.previewSentinel?.remove();
      state.previewSentinel = null;
    }
    return images;
  }

  function beginPreviewObservation() {
    if (
      !state.previewSentinel ||
      !state.previewRail ||
      !("IntersectionObserver" in window)
    ) {
      return;
    }
    state.previewObserver = createPreviewIntersectionObserver(
      window.IntersectionObserver,
      state.previewRail,
      state.previewSentinel,
      appendPreviewBatch,
    );
  }

  function failPreviewProbe(image) {
    const rail = state.previewRail;
    if (!rail || !rail.contains(image)) {
      return;
    }
    state.previewObserver?.disconnect();
    state.previewObserver = null;
    state.previewSentinel?.remove();
    const empty = createElement(
      "p",
      "empty-state empty-state--compact",
      "暂无预览",
    );
    rail.replaceWith(empty);
    state.previewRail = null;
    state.previewSentinel = null;
    state.previewVideo = null;
    state.previewStart = 0;
    state.previewLoading = false;
  }

  function createPreviewSection(video) {
    const section = createElement("section", "preview-section");
    const heading = createElement("h3", "", "预览图");
    section.append(heading);
    const firstBatch = derivePreviewUrls(video, {
      start: 0,
      limit: PREVIEW_BATCH_SIZE,
    });
    if (!firstBatch.length) {
      section.append(createElement("p", "empty-state empty-state--compact", "暂无预览"));
      return section;
    }
    const rail = createElement("div", "preview-rail");
    rail.tabIndex = 0;
    rail.setAttribute("aria-label", `${video.code} 预览图画廊`);
    const sentinel = createElement("span", "preview-sentinel");
    sentinel.setAttribute("aria-hidden", "true");
    rail.append(sentinel);
    section.append(rail);
    state.previewRail = rail;
    state.previewSentinel = sentinel;
    state.previewVideo = video;
    state.previewStart = 0;
    return section;
  }

  function startPreviewLoading() {
    const [probe] = appendPreviewBatch(1, { probe: true });
    if (!probe) {
      return;
    }
    const source = probe.dataset.previewSource;
    delete probe.dataset.previewSource;
    const started = startPreviewProbeRequest(probe, source, {
      onSuccess: () => {
        delete probe.dataset.previewProbe;
        if (!state.previewRail?.contains(probe)) {
          return;
        }
        appendPreviewBatch(PREVIEW_BATCH_SIZE - 1);
        beginPreviewObservation();
      },
      onFailure: () => {
        delete probe.dataset.previewProbe;
        failPreviewProbe(probe);
      },
    });
    if (!started) {
      delete probe.dataset.previewProbe;
      failPreviewProbe(probe);
    }
  }

  function renderLinkSection(groups) {
    const section = createElement("section", "detail-links");
    const heading = createElement("h3", "", "观看链接");
    section.append(heading);
    if (!groups.length) {
      section.append(
        createElement("p", "empty-state empty-state--compact", "暂无可用链接"),
      );
      return section;
    }
    for (const group of groups) {
      const block = createElement("div", "link-group");
      block.append(createElement("h4", "", group.label));
      const list = createElement("div", "link-group__items");
      for (const item of group.links) {
        const anchor = createElement("a", "external-link", item.label);
        anchor.href = item.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        list.append(anchor);
      }
      block.append(list);
      section.append(block);
    }
    return section;
  }

  function createLinkSection(video) {
    const groups = collectLinkGroups(video.links);
    const section = renderLinkSection(groups);
    void loadResolvedLinks()
      .then((manifest) => upgradeLinkGroups(video.code, groups, manifest))
      .then((upgraded) => {
        const hasResolved = upgraded.some((group) =>
          group.links.some((item) => item.resolved)
        );
        if (section.isConnected && hasResolved) {
          section.replaceWith(renderLinkSection(upgraded));
        }
      });
    return section;
  }

  function createOfficialTagSection(video) {
    const assigned = Array.isArray(video.tagIds)
      ? video.tagIds.map((tagId) => state.model.getTag(tagId)).filter(Boolean)
      : [];
    if (!assigned.length) {
      return null;
    }
    const section = createElement("section", "detail-tags");
    section.append(createElement("h3", "", "官方标签"));
    for (const [group, title] of [["genre", "类型与玩法"], ["character", "角色与造型"]]) {
      const groupTags = assigned.filter((tag) => tag.group === group);
      if (!groupTags.length) continue;
      const block = createElement("div", "detail-tag-group");
      block.append(createElement("h4", "", title));
      const list = createElement("div", "detail-tag-list");
      for (const tag of groupTags) {
        const button = createElement("button", "detail-tag", tag.nameZh);
        button.type = "button";
        button.dataset.action = "select-detail-tag";
        button.dataset.tagId = String(tag.id);
        button.setAttribute("aria-label", `用标签“${tag.nameZh}”筛选影片`);
        list.append(button);
      }
      block.append(list);
      section.append(block);
    }
    return section;
  }

  function createDialogActions(video) {
    const actions = createElement("div", "detail-actions");
    const copy = createElement("button", "button button--quiet", "复制番号");
    copy.type = "button";
    copy.dataset.action = "copy-code";
    copy.dataset.code = video.code;
    const favoriteState = favorites.getState(video.code);
    const favorite = createElement(
      "button",
      "button button--primary",
      FAVORITE_PRESENTATION[favoriteState].label,
    );
    favorite.type = "button";
    favorite.dataset.action = "cycle-favorite";
    favorite.dataset.code = video.code;
    favorite.dataset.favoriteState = String(favoriteState);
    actions.append(copy, favorite);
    return actions;
  }

  function openVideoDialog(code, trigger) {
    const video = state.model?.getVideo(code);
    if (!video) {
      return;
    }
    disconnectPreviewObserver();
    state.activeDialogCode = code;
    state.dialogTrigger = trigger ?? document.activeElement;
    const layout = createElement("div", "detail-layout");
    const coverFrame = createElement("figure", "detail-cover-frame");
    const originalCoverUrl = safeHttpUrl(video.cover);
    const coverUrl = optimizedCoverUrl(originalCoverUrl, {
      width: 640,
      height: 960,
    });
    if (coverUrl) {
      const cover = createElement("img", "detail-cover");
      cover.src = coverUrl;
      if (originalCoverUrl && coverUrl !== originalCoverUrl) {
        cover.dataset.originalSrc = originalCoverUrl;
      }
      cover.alt = `${video.code} 封面`;
      cover.width = 320;
      cover.height = 480;
      cover.decoding = "async";
      coverFrame.append(cover);
    } else {
      const missing = createElement("div", "detail-cover detail-cover--missing");
      missing.setAttribute("role", "img");
      missing.setAttribute("aria-label", "暂无封面");
      coverFrame.append(missing);
    }
    const details = createElement("div", "detail-copy");
    details.append(
      createElement("span", "detail-code", video.code),
      createElement("h2", "detail-title", video.title || video.code),
    );
    details.querySelector("h2").id = "video-detail-title";
    details.append(
      createElement(
        "p",
        "detail-date",
        video.releaseDate ? `发行日期 ${video.releaseDate}` : "发行日期未知",
      ),
    );
    if (Array.isArray(video.actors) && video.actors.length) {
      const actors = createElement("div", "actor-chips");
      actors.setAttribute("aria-label", "演员");
      for (const actor of video.actors) {
        if (typeof actor !== "string" || !actor.trim()) {
          continue;
        }
        const chip = createElement("button", "actor-chip", actor.trim());
        chip.type = "button";
        chip.dataset.action = "actor-search";
        chip.dataset.actor = actor.trim();
        actors.append(chip);
      }
      details.append(actors);
    }
    const officialTags = createOfficialTagSection(video);
    details.append(createDialogActions(video));
    if (officialTags) details.append(officialTags);
    details.append(createLinkSection(video));
    layout.append(coverFrame, details);
    ui.videoDialogContent.replaceChildren(layout, createPreviewSection(video));
    if (!ui.videoDialog.open) {
      ui.videoDialog.showModal();
    }
    startPreviewLoading();
    ui.videoDialog.querySelector("[data-action='close-dialog']").focus();
  }

  function closeVideoDialog() {
    if (ui.videoDialog.open) {
      ui.videoDialog.close();
    }
    state.activeDialogCode = null;
  }

  function trapDialogFocus(event) {
    if (event.key !== "Tab") {
      return;
    }
    const dialog = event.currentTarget;
    const focusable = [
      ...dialog.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ].filter((element) => !element.hidden);
    if (!focusable.length) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function copyCode(code) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(code);
      } else {
        const input = createElement("textarea", "visually-hidden");
        input.value = code;
        document.body.append(input);
        input.select();
        if (!document.execCommand("copy")) {
          throw new Error("copy command failed");
        }
        input.remove();
      }
      showToast(`已复制 ${code}`);
    } catch {
      showToast("复制失败，请手动选择番号");
    }
  }

  document.addEventListener("click", (event) => {
    const control = event.target.closest("[data-action]");
    if (!control) {
      return;
    }
    const action = control.dataset.action;
    if (action === "switch-view") {
      state.view = control.dataset.view;
      clearSearch({ focusMain: true });
    } else if (action === "clear-search") {
      clearSearch({ focus: true });
    } else if (action === "toggle-theme") {
      state.preferences.theme =
        state.preferences.theme === "dark" ? "light" : "dark";
      persistPreferences();
      applyPreferences();
    } else if (action === "toggle-density") {
      state.preferences.density =
        state.preferences.density === "compact" ? "comfortable" : "compact";
      persistPreferences();
      applyPreferences();
    } else if (action === "open-drawer") {
      state.drawerTrigger = control;
      ui.seriesDrawer.showModal();
      document.body.classList.add("modal-open");
      ui.seriesDrawer.querySelector("[data-action='close-drawer']").focus();
    } else if (action === "close-drawer") {
      closeDrawer();
    } else if (action === "select-series") {
      selectSeries(control.dataset.series);
      closeDrawer();
    } else if (action === "mount-series") {
      const code = control.dataset.series;
      if (state.mountedSeries === code) {
        const container = findSeriesMount(code);
        if (container) {
          unmountSeries(container);
        }
        control.setAttribute("aria-expanded", "false");
        control.textContent = "展开系列";
        state.mountedSeries = null;
        refreshDeferredCovers();
      } else {
        mountSeriesWindow(code);
      }
    } else if (action === "toggle-slots") {
      const code = control.dataset.series;
      state.preferences.slots[code] = !state.preferences.slots[code];
      persistPreferences();
      mountSeriesWindow(code, 0);
    } else if (action === "open-video") {
      openVideoDialog(control.dataset.code, control);
      if (!state.tagsReady && !state.tagError) void ensureTagCatalog();
    } else if (action === "close-dialog") {
      closeVideoDialog();
    } else if (action === "copy-code") {
      void copyCode(control.dataset.code);
    } else if (action === "cycle-favorite") {
      const nextState = favorites.cycle(control.dataset.code);
      control.dataset.favoriteState = String(nextState);
      control.textContent = FAVORITE_PRESENTATION[nextState].label;
      updateFavoriteCount();
      showToast(
        `${control.dataset.code}：${FAVORITE_PRESENTATION[nextState].shortLabel}`,
      );
      if (state.view === "favorites" && !state.query) {
        renderFavoritesView();
      }
    } else if (action === "actor-search") {
      const actor = control.dataset.actor ?? "";
      closeVideoDialog();
      ui.search.value = actor;
      ui.searchClear.hidden = !actor;
      state.query = actor.trim();
      state.searchStart = 0;
      renderCurrentView();
      ui.search.focus();
    } else if (action === "cycle-tag") {
      const tagId = Number.parseInt(control.dataset.tagId, 10);
      if (state.model.getTag(tagId)) {
        cycleTagSelection(tagId);
        renderTagView();
      }
    } else if (action === "clear-tags") {
      state.tagInclude.clear();
      state.tagExclude.clear();
      state.tagStart = 0;
      renderTagView();
    } else if (action === "select-detail-tag") {
      const tagId = Number.parseInt(control.dataset.tagId, 10);
      if (state.model.getTag(tagId)) {
        state.tagInclude.add(tagId);
        state.tagExclude.delete(tagId);
        state.tagStart = 0;
        state.view = "tags";
        clearActiveSearch({
          state,
          input: ui.search,
          clearButton: ui.searchClear,
        });
        closeVideoDialog();
        renderCurrentView({ focusMain: true });
      }
    } else if (action === "page-series") {
      mountSeriesWindow(
        control.dataset.series,
        Number.parseInt(control.dataset.start, 10) || 0,
      );
    } else if (action === "page-results") {
      state.searchStart = Number.parseInt(control.dataset.start, 10) || 0;
      renderSearchView();
    } else if (action === "page-favorites") {
      const favoriteState = Number.parseInt(control.dataset.favoriteState, 10);
      state.favoriteStarts[favoriteState] =
        Number.parseInt(control.dataset.start, 10) || 0;
      renderFavoritesView();
    } else if (action === "page-tag-results") {
      state.tagStart = Number.parseInt(control.dataset.start, 10) || 0;
      renderTagView();
    } else if (action === "retry") {
      void loadCatalog();
    } else if (action === "retry-tags") {
      state.tagError = null;
      renderTagLoadState();
      void ensureTagCatalog();
    } else if (action === "back-top") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } else if (action === "close-toast") {
      ui.toast.hidden = true;
    }
  });

  document.addEventListener(
    "error",
    (event) => {
      const image = event.target;
      if (!(image instanceof HTMLImageElement)) {
        return;
      }
      if (image.classList.contains("preview-image")) {
        if (image.dataset.previewProbe === "true") {
          return;
        }
        replacePreviewWithError(image);
      } else if (
        image.classList.contains("video-cover") ||
        image.classList.contains("detail-cover")
      ) {
        applyCoverFallback(image);
      }
    },
    true,
  );

  bindDebouncedSearchInput({
    input: ui.search,
    clearButton: ui.searchClear,
    state,
    render: renderCurrentView,
  });

  document.addEventListener("input", (event) => {
    if (event.target?.id === "tag-directory-search") {
      state.tagSearch = event.target.value;
      state.tagStart = 0;
      renderTagView();
      const replacement = document.querySelector("#tag-directory-search");
      replacement?.focus();
      replacement?.setSelectionRange(state.tagSearch.length, state.tagSearch.length);
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target?.id === "tag-match-mode") {
      state.tagMatch = event.target.value === "any" ? "any" : "all";
      state.tagStart = 0;
      renderTagView();
    } else if (event.target?.id === "tag-result-sort") {
      state.tagSort = ["newest", "oldest", "code"].includes(event.target.value)
        ? event.target.value
        : "newest";
      state.tagStart = 0;
      renderTagView();
    }
  });

  document.querySelector("#search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    window.clearTimeout(state.searchTimer);
    state.query = ui.search.value.trim();
    state.searchStart = 0;
    renderCurrentView();
  });

  document.addEventListener("keydown", (event) => {
    const tab = event.target?.closest?.("[role='tab'][data-view]");
    const tablist = tab?.closest("[role='tablist']");
    if (tablist) {
      const tabs = [...tablist.querySelectorAll("[role='tab'][data-view]")];
      const targetIndex = tabKeyTargetIndex(
        event.key,
        tabs.indexOf(tab),
        tabs.length,
      );
      if (targetIndex !== null) {
        event.preventDefault();
        const target = tabs[targetIndex];
        state.view = target.dataset.view;
        clearSearch();
        target.focus();
        return;
      }
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      ui.search.focus();
      ui.search.select();
    } else if (
      event.key === "Escape" &&
      !ui.videoDialog.open &&
      !ui.seriesDrawer.open &&
      (state.query || ui.search.value)
    ) {
      clearSearch({ focus: true });
    }
  });

  for (const dialog of [ui.videoDialog, ui.seriesDrawer]) {
    dialog.addEventListener("keydown", trapDialogFocus);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        dialog.close();
      }
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      dialog.close();
    });
  }

  ui.videoDialog.addEventListener("close", () => {
    disconnectPreviewObserver();
    ui.videoDialogContent.replaceChildren();
    if (state.dialogTrigger?.isConnected) {
      state.dialogTrigger.focus();
    }
    state.dialogTrigger = null;
  });

  ui.seriesDrawer.addEventListener("close", () => {
    document.body.classList.remove("modal-open");
    if (state.drawerTrigger?.isConnected) {
      state.drawerTrigger.focus();
    }
    state.drawerTrigger = null;
  });

  window.addEventListener("offline", () => {
    ui.connectionStatus.textContent = "当前离线";
    showToast("网络已断开，已载入的目录仍可浏览");
  });
  window.addEventListener("online", () => {
    ui.connectionStatus.textContent = state.model ? "数据已就绪" : "网络已恢复";
    showToast("网络已恢复");
  });

  if ("IntersectionObserver" in window) {
    const topObserver = new IntersectionObserver(([entry]) => {
      ui.backToTop.hidden = entry.isIntersecting;
    });
    topObserver.observe(ui.pageTop);
  }

  applyPreferences();
  updateFavoriteCount();
  ui.searchClear.hidden = true;
  void loadCatalog();
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startApplication, {
      once: true,
    });
  } else {
    startApplication();
  }
}
