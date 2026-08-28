const LARGE_SET_THRESHOLD = 250;
const WINDOW_SIZE = 100;
const COVER_WIDTH = 320;
const COVER_HEIGHT = 480;
const DEFAULT_PREVIEW_BATCH = 4;
const MAX_PREVIEW_BATCH = 6;
const MOBILE_PRIORITY_COVERS = 2;
const DESKTOP_PRIORITY_COVERS = 6;
const DESKTOP_BREAKPOINT = 768;

const LINK_LABELS = Object.freeze({
  gofile: "Gofile",
  player4me: "Player4me",
  streamtape: "Streamtape",
  subtitle: "字幕",
  vidara: "Vidara",
});

let activeSeriesContainer = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function safeDeferredCoverUrl(value) {
  const absolute = safeHttpUrl(value);
  if (absolute) {
    return absolute;
  }
  if (
    typeof value !== "string" ||
    !value.trim() ||
    /[\u0000-\u001f\u007f]/u.test(value)
  ) {
    return null;
  }
  const source = value.trim();
  if (
    /^\/media\/featured-covers\/g\/[0-9a-f]{64}\/[a-z][a-z0-9]*-(?:0|[1-9]\d*)\.webp$/u
      .test(source)
  ) {
    return source;
  }
  try {
    const base = "https://catalog.invalid";
    const parsed = new URL(source, base);
    if (
      parsed.origin !== base ||
      parsed.pathname !== "/.netlify/images" ||
      parsed.hash
    ) {
      return null;
    }
    const allowedParameters = new Set(["url", "w", "h", "fit"]);
    if (
      [...parsed.searchParams.keys()].some(
        (name) => !allowedParameters.has(name),
      ) ||
      [...allowedParameters].some(
        (name) => parsed.searchParams.getAll(name).length !== 1,
      )
    ) {
      return null;
    }
    const remote = safeHttpUrl(parsed.searchParams.get("url"));
    const remoteUrl = remote ? new URL(remote) : null;
    const width = parsed.searchParams.get("w");
    const height = parsed.searchParams.get("h");
    if (
      remoteUrl?.origin !== "https://www.giga-web.jp" ||
      !remoteUrl.pathname.startsWith("/db_titles/") ||
      !/^[1-9]\d{0,3}$/u.test(width) ||
      !/^[1-9]\d{0,3}$/u.test(height) ||
      Number(width) > 4096 ||
      Number(height) > 4096 ||
      parsed.searchParams.get("fit") !== "cover"
    ) {
      return null;
    }
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}

function revealDeferredCover(image) {
  const source = safeDeferredCoverUrl(image?.getAttribute?.("data-src"));
  image?.removeAttribute?.("data-src");
  if (source) {
    image.setAttribute("src", source);
  }
}

/**
 * Keep non-priority card covers network-idle until they approach the viewport.
 */
export function observeDeferredCovers(Observer, container) {
  const images = [
    ...(container?.querySelectorAll?.("img.video-cover[data-src]") ?? []),
  ];
  if (!images.length) {
    return null;
  }
  if (typeof Observer !== "function") {
    images.forEach(revealDeferredCover);
    return null;
  }

  let observer;
  observer = new Observer(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) {
          continue;
        }
        revealDeferredCover(entry.target);
        observer.unobserve(entry.target);
      }
    },
    {
      root: null,
      rootMargin: "120px 0px",
      threshold: 0.01,
    },
  );
  images.forEach((image) => observer.observe(image));
  return observer;
}

function normalizedCoverDimension(value, fallback) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 && parsed <= 4096
    ? parsed
    : fallback;
}

function canonicalVideoCode(value) {
  if (typeof value !== "string") {
    return null;
  }
  const match = value.trim().toUpperCase().match(/^([A-Z][A-Z0-9]*)-(\d+)$/u);
  return match ? `${match[1]}-${Number.parseInt(match[2], 10)}` : null;
}

function isFeaturedGeneration(value) {
  return typeof value === "string" && /^[0-9a-f]{64}$/u.test(value);
}

function featuredCoverPath(code, generation) {
  const canonical = canonicalVideoCode(code);
  return canonical && isFeaturedGeneration(generation)
    ? `/media/featured-covers/g/${generation}/${canonical.toLowerCase()}.webp`
    : null;
}

function isFeaturedCoverPath(code, value) {
  if (typeof value !== "string") {
    return false;
  }
  const match = value.match(
    /^\/media\/featured-covers\/g\/([0-9a-f]{64})\/([a-z][a-z0-9]*-\d+)\.webp$/u,
  );
  return Boolean(match && value === featuredCoverPath(code, match[1]));
}

/** Return only manifest entries that cannot escape the fixed local cover path. */
export function normalizeFeaturedCovers(manifest) {
  const covers = new Map();
  const generation = manifest?.generation;
  if (
    !manifest ||
    typeof manifest !== "object" ||
    !isFeaturedGeneration(generation) ||
    !Array.isArray(manifest.covers)
  ) {
    return covers;
  }
  for (const entry of manifest.covers.slice(0, DESKTOP_PRIORITY_COVERS)) {
    const code = canonicalVideoCode(entry?.code);
    const expectedPath = featuredCoverPath(code, generation);
    if (code && entry?.path === expectedPath) {
      covers.set(code, expectedPath);
    }
  }
  return covers;
}

function priorityCoverLimit(viewportWidth) {
  const width = Number.isFinite(viewportWidth)
    ? viewportWidth
    : globalThis.innerWidth;
  return Number.isFinite(width) && width >= DESKTOP_BREAKPOINT
    ? DESKTOP_PRIORITY_COVERS
    : MOBILE_PRIORITY_COVERS;
}

function isLocalHostname(value) {
  const hostname = String(value ?? "").trim().toLowerCase();
  return (
    !hostname ||
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname === "127.0.0.1" ||
    hostname === "::1" ||
    hostname === "[::1]"
  );
}

function isNetlifyHostname(value) {
  const hostname = String(value ?? "").trim().toLowerCase();
  return hostname === "netlify.app" || hostname.endsWith(".netlify.app");
}

/** Resolve a validated root-local asset beneath the current deployment base. */
export function siteAssetUrl(
  value,
  { baseUrl = globalThis.document?.baseURI ?? null } = {},
) {
  if (
    typeof value !== "string" ||
    !/^\/media\/featured-covers\/g\/[0-9a-f]{64}\/[a-z][a-z0-9]*-(?:0|[1-9]\d*)\.webp$/u
      .test(value)
  ) {
    return null;
  }
  if (!baseUrl) {
    return value;
  }
  try {
    const base = new URL(baseUrl);
    if (!["http:", "https:"].includes(base.protocol)) {
      return value;
    }
    const directory = new URL("./", base);
    const resolved = new URL(value.slice(1), directory);
    return resolved.origin === directory.origin
      ? `${resolved.pathname}${resolved.search}`
      : null;
  } catch {
    return value;
  }
}

export function optimizedCoverUrl(
  value,
  {
    hostname = globalThis.location?.hostname ?? "",
    width = COVER_WIDTH,
    height = COVER_HEIGHT,
  } = {},
) {
  const source = safeHttpUrl(value);
  if (!source || isLocalHostname(hostname) || !isNetlifyHostname(hostname)) {
    return source;
  }
  const parsed = new URL(source);
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname !== "www.giga-web.jp" ||
    !parsed.pathname.startsWith("/db_titles/")
  ) {
    return source;
  }
  const targetWidth = normalizedCoverDimension(width, COVER_WIDTH);
  const targetHeight = normalizedCoverDimension(height, COVER_HEIGHT);
  return (
    `/.netlify/images?url=${encodeURIComponent(source)}` +
    `&w=${targetWidth}&h=${targetHeight}&fit=cover`
  );
}

function requireContainer(container) {
  if (!container || typeof container !== "object" || !("innerHTML" in container)) {
    throw new TypeError("A container with an innerHTML property is required");
  }
}

function clearContainer(container) {
  if (!container) {
    return;
  }
  if (typeof container.replaceChildren === "function") {
    container.replaceChildren();
  } else if ("innerHTML" in container) {
    container.innerHTML = "";
  }
}

function setContainerHtml(container, html) {
  requireContainer(container);
  container.innerHTML = html;
}

function normalizeNonnegativeInteger(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0
    ? Math.trunc(parsed)
    : fallback;
}

function videoNumber(video) {
  const rawNumber = video?.number;
  if (rawNumber !== undefined) {
    const hasSupportedType =
      typeof rawNumber === "number" ||
      (typeof rawNumber === "string" && rawNumber.trim() !== "");
    const declared = hasSupportedType ? Number(rawNumber) : Number.NaN;
    return Number.isInteger(declared) && declared >= 0
      ? declared
      : Number.MAX_SAFE_INTEGER;
  }
  const match =
    typeof video?.code === "string" ? video.code.match(/-(\d+)$/u) : null;
  const derived = match ? Number.parseInt(match[1], 10) : Number.MAX_SAFE_INTEGER;
  return derived >= 0 ? derived : Number.MAX_SAFE_INTEGER;
}

function compareVideos(left, right) {
  const byNumber = videoNumber(left) - videoNumber(right);
  if (byNumber) {
    return byNumber;
  }
  const leftCode = String(left?.code ?? "");
  const rightCode = String(right?.code ?? "");
  return leftCode === rightCode ? 0 : leftCode < rightCode ? -1 : 1;
}

function renderLinkBadges(links) {
  if (!links || typeof links !== "object") {
    return "";
  }
  const badges = [];
  for (const [provider, label] of Object.entries(LINK_LABELS)) {
    if (safeHttpUrl(links[provider])) {
      badges.push(label);
    }
  }
  const uncensored =
    links.uncensored && typeof links.uncensored === "object"
      ? links.uncensored
      : {};
  for (const [provider, label] of Object.entries(LINK_LABELS)) {
    if (safeHttpUrl(uncensored[provider])) {
      badges.push(`无码 ${label}`);
    }
  }
  if (!badges.length) {
    return "";
  }
  return `<span class="link-badges" aria-label="可用链接">${badges
    .map((label) => `<span class="link-badge">${escapeHtml(label)}</span>`)
    .join("")}</span>`;
}

function actorSummary(actors) {
  if (!Array.isArray(actors)) {
    return "";
  }
  const names = actors
    .filter((actor) => typeof actor === "string" && actor.trim())
    .map((actor) => actor.trim());
  if (!names.length) {
    return "";
  }
  const visible = names.slice(0, 2).join("、");
  return names.length > 2 ? `${visible} +${names.length - 2}` : visible;
}

function renderTagPreview(video, tagLookup) {
  if (!Array.isArray(video?.tagIds) || typeof tagLookup !== "function") {
    return "";
  }
  const names = video.tagIds
    .map((tagId) => tagLookup(tagId))
    .filter((tag) => tag && typeof tag.nameZh === "string" && tag.nameZh.trim())
    .map((tag) => tag.nameZh.trim());
  if (!names.length) {
    return "";
  }
  const visible = names.slice(0, 3);
  const remainder = names.length - visible.length;
  return [
    '<span class="video-tags" aria-label="官方标签">',
    ...visible.map(
      (name) => `<span class="video-tag">${escapeHtml(name)}</span>`,
    ),
    remainder > 0
      ? `<span class="video-tag video-tag--more">+${remainder}</span>`
      : "",
    "</span>",
  ].join("");
}

function renderVideoCard(
  video,
  { priority = false, featuredCover = null, tagLookup = null } = {},
) {
  const code = String(video?.code ?? "").trim();
  const title = String(video?.title ?? "").trim() || code || "未命名影片";
  const originalCover = safeHttpUrl(video?.cover);
  const canonicalCode = canonicalVideoCode(code);
  const cover = isFeaturedCoverPath(canonicalCode, featuredCover)
    ? siteAssetUrl(featuredCover)
    : optimizedCoverUrl(originalCover);
  const originalCoverAttribute =
    cover && originalCover && cover !== originalCover
      ? ` data-original-src="${escapeHtml(originalCover)}"`
      : "";
  const loadingAttributes = priority
    ? ' loading="eager" fetchpriority="high"'
    : ' loading="lazy"';
  const sourceAttribute = priority ? "src" : "data-src";
  const coverMarkup = cover
    ? `<img class="video-cover" ${sourceAttribute}="${escapeHtml(cover)}"${originalCoverAttribute} alt="" width="${COVER_WIDTH}" height="${COVER_HEIGHT}"${loadingAttributes} decoding="async">`
    : '<span class="video-cover video-cover--missing" aria-hidden="true"></span>';
  const actors = actorSummary(video?.actors);
  const actorMarkup = actors
    ? `<span class="video-actors">${escapeHtml(actors)}</span>`
    : "";
  const linkMarkup = renderLinkBadges(video?.links);
  const tagMarkup = renderTagPreview(video, tagLookup);

  return [
    `<article class="video-card has-video" data-code="${escapeHtml(code)}">`,
    `<button class="video-card__button" type="button" data-action="open-video" data-code="${escapeHtml(code)}">`,
    `<span class="video-cover-frame">${coverMarkup}</span>`,
    '<span class="video-card__meta">',
    `<span class="video-code">${escapeHtml(code)}</span>`,
    `<span class="video-title">${escapeHtml(title)}</span>`,
    actorMarkup,
    tagMarkup,
    linkMarkup,
    "</span>",
    "</button>",
    "</article>",
  ].join("");
}

function renderEmptySlot(number) {
  const label = String(number).padStart(2, "0");
  return [
    `<div class="empty-slot" data-number="${number}" aria-label="${escapeHtml(
      `${label} 暂无影片`,
    )}">`,
    `<span class="empty-slot__number">${label}</span>`,
    '<span class="empty-slot__label">暂无影片</span>',
    "</div>",
  ].join("");
}

function renderEmptyState(message) {
  return `<p class="empty-state" role="status">${escapeHtml(
    message || "暂无影片",
  )}</p>`;
}

function makeSeriesItems(series, mode) {
  const videos = Array.isArray(series?.videos)
    ? series.videos
        .filter((video) => video && typeof video === "object")
        .slice()
        .sort(compareVideos)
    : [];
  if (mode !== "slots") {
    return videos.map((video) => ({ video }));
  }

  const byNumber = new Map();
  const zeroItems = [];
  let largestNumber = 0;
  for (const video of videos) {
    const number = videoNumber(video);
    if (number === 0) {
      if (!byNumber.has(number)) {
        byNumber.set(number, video);
        zeroItems.push({ video });
      }
      continue;
    }
    if (
      number !== Number.MAX_SAFE_INTEGER &&
      !byNumber.has(number)
    ) {
      byNumber.set(number, video);
      largestNumber = Math.max(largestNumber, number);
    }
  }
  const finalNumber = Math.max(largestNumber, 99);
  return zeroItems.concat(
    Array.from({ length: finalNumber }, (_, index) => {
      const number = index + 1;
      return byNumber.has(number)
        ? { video: byNumber.get(number) }
        : { slotNumber: number };
    }),
  );
}

function resolveWindow(items, options = {}) {
  const total = items.length;
  const start = Math.min(
    normalizeNonnegativeInteger(options.start),
    total,
  );
  const defaultLimit = total > LARGE_SET_THRESHOLD ? WINDOW_SIZE : total;
  const requestedLimit =
    options.limit === undefined
      ? defaultLimit
      : normalizeNonnegativeInteger(options.limit, defaultLimit);
  const pageSize =
    total > LARGE_SET_THRESHOLD
      ? Math.min(requestedLimit || WINDOW_SIZE, WINDOW_SIZE)
      : requestedLimit;
  const end = Math.min(start + pageSize, total);
  return {
    items: items.slice(start, end),
    start,
    end,
    total,
    pageSize,
  };
}

function windowMetadata(window, mode) {
  return Object.freeze({
    mode,
    start: window.start,
    end: window.end,
    rendered: window.items.length,
    total: window.total,
    pageSize: window.pageSize,
    hasPrevious: window.start > 0,
    hasMore: window.end < window.total,
    previousStart: Math.max(0, window.start - window.pageSize),
    nextStart: window.end,
  });
}

function renderItems(window, options = {}) {
  let prioritizedCovers = 0;
  const priorityLimit = Number.isInteger(options.priorityLimit)
    ? Math.max(0, options.priorityLimit)
    : priorityCoverLimit(options.viewportWidth);
  const featuredCovers = options.featuredCovers instanceof Map
    ? options.featuredCovers
    : new Map();
  return window.items
    .map((item) => {
      if (!item.video) {
        return renderEmptySlot(item.slotNumber);
      }
      const priority = prioritizedCovers < priorityLimit;
      prioritizedCovers += 1;
      const code = canonicalVideoCode(item.video.code);
      return renderVideoCard(item.video, {
        priority,
        featuredCover: code ? featuredCovers.get(code) ?? null : null,
        tagLookup: options.tagLookup,
      });
    })
    .join("");
}

/**
 * Return the lightweight series heading. Its mount target starts empty.
 */
export function renderSeriesShell(series = {}) {
  const code = String(series.code ?? "").trim();
  const safeId =
    code
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/gu, "-")
      .replace(/^-+|-+$/gu, "") || "series";
  const count = normalizeNonnegativeInteger(series.count);
  const firstDate = String(series.firstReleaseDate ?? "");
  const latestDate = String(series.latestReleaseDate ?? "");
  const subtitleUrl = safeHttpUrl(series?.links?.subtitle);
  const subtitleAction = subtitleUrl
    ? `<a class="series-subtitle-link" href="${escapeHtml(
        subtitleUrl,
      )}" target="_blank" rel="noopener noreferrer">系列字幕</a>`
    : "";

  return [
    `<section class="series-shell" data-series="${escapeHtml(code)}">`,
    '<header class="series-shell__header">',
    `<span class="series-code">${escapeHtml(code)}</span>`,
    `<span class="series-count">${count} 部</span>`,
    `<span class="series-dates">${escapeHtml(firstDate)} — ${escapeHtml(
      latestDate,
    )}</span>`,
    '<span class="series-shell__actions">',
    subtitleAction,
    `<button class="series-toggle" type="button" data-action="mount-series" data-series="${escapeHtml(
      code,
    )}" aria-expanded="false" aria-controls="series-mount-${safeId}">展开系列</button>`,
    "</span>",
    "</header>",
    `<div class="series-mount" id="series-mount-${safeId}" data-series-mount="${escapeHtml(
      code,
    )}"></div>`,
    "</section>",
  ].join("");
}

/**
 * Mount one series window. Mounting a different series clears the previous one.
 */
export function mountSeries(container, series = {}, options = {}) {
  requireContainer(container);
  if (activeSeriesContainer && activeSeriesContainer !== container) {
    clearContainer(activeSeriesContainer);
  }
  const mode = options.mode === "slots" ? "slots" : "real-only";
  const window = resolveWindow(makeSeriesItems(series, mode), options);
  const content = window.total
    ? `<div class="video-grid series-grid" data-mode="${mode}">${renderItems(
        window,
        options,
      )}</div>`
    : renderEmptyState(options.emptyMessage);
  setContainerHtml(container, content);
  activeSeriesContainer = container;
  return windowMetadata(window, mode);
}

export function unmountSeries(container) {
  requireContainer(container);
  clearContainer(container);
  if (activeSeriesContainer === container) {
    activeSeriesContainer = null;
  }
}

/**
 * Render actual search/favorite matches only; slot mode is intentionally ignored.
 */
export function renderSearchResults(container, videos = [], options = {}) {
  requireContainer(container);
  if (activeSeriesContainer) {
    clearContainer(activeSeriesContainer);
    activeSeriesContainer = null;
  }
  const items = Array.isArray(videos)
    ? videos
        .filter((video) => video && typeof video === "object")
        .map((video) => ({ video }))
    : [];
  const window = resolveWindow(items, options);
  const context = options.context === "favorites" ? "favorites" : "search";
  const content = window.total
    ? `<div class="video-grid results-grid" data-view="${context}">${renderItems(
        window,
        {
          ...options,
          priorityLimit: MOBILE_PRIORITY_COVERS,
          featuredCovers: null,
        },
      )}</div>`
    : renderEmptyState(options.emptyMessage || "没有匹配的影片");
  setContainerHtml(container, content);
  return windowMetadata(window, "real-only");
}

/**
 * Derive a small preview batch from the compact descriptor after a dialog opens.
 */
export function derivePreviewUrls(video = {}, options = {}) {
  const base = safeHttpUrl(video.previewBase);
  const count = normalizeNonnegativeInteger(video.previewCount);
  const start = normalizeNonnegativeInteger(options.start);
  const requestedLimit =
    options.limit === undefined
      ? DEFAULT_PREVIEW_BATCH
      : normalizeNonnegativeInteger(options.limit);
  const limit = Math.min(requestedLimit, MAX_PREVIEW_BATCH);
  if (!base || count === 0 || start >= count || limit === 0) {
    return Object.freeze([]);
  }

  const parsedBase = new URL(base);
  parsedBase.hash = "";
  parsedBase.search = "";
  if (!parsedBase.pathname.endsWith("/")) {
    parsedBase.pathname += "/";
  }
  const end = Math.min(start + limit, count);
  const urls = [];
  for (let index = start; index < end; index += 1) {
    const filename = `${String(index + 1).padStart(3, "0")}_l.jpg`;
    urls.push(new URL(filename, parsedBase).href);
  }
  return Object.freeze(urls);
}
