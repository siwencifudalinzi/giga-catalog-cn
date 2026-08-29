import { normalizeText, normalizeVideoCode } from "./catalog.js";
import { createTagIndex, filterVideosByTags } from "./tags.js";

const GENERATION_RE = /^[0-9a-f]{64}$/u;
const SERIES_RE = /^[A-Z][A-Z0-9]*$/u;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/u;

export class RuntimeCatalogError extends Error {
  constructor(kind) {
    super(`运行目录数据无效（${kind}）`);
    this.name = "RuntimeCatalogError";
    this.kind = kind;
  }
}

function fail(kind) {
  throw new RuntimeCatalogError(kind);
}

function isPlainObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function object(value, kind, keys) {
  if (!isPlainObject(value)) fail(kind);
  const actual = Object.keys(value);
  if (actual.some((key) => {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return !descriptor || !Object.hasOwn(descriptor, "value");
  })) fail(kind);
  if (
    (keys.required ?? []).some((key) => !Object.hasOwn(value, key))
    || actual.some((key) => !keys.allowed.includes(key))
  ) {
    fail(kind);
  }
  return value;
}

function integer(value, kind, { minimum = 0, maximum = Number.MAX_SAFE_INTEGER } = {}) {
  if (!Number.isFinite(value) || typeof value !== "number" || !Number.isInteger(value)
    || value < minimum || value > maximum) fail(kind);
  return value;
}

function text(value, kind, { allowEmpty = false } = {}) {
  if (typeof value !== "string" || (!allowEmpty && !value.trim())) fail(kind);
  return value;
}

function date(value, kind) {
  text(value, kind);
  if (!DATE_RE.test(value)) fail(kind);
  const parsed = new Date(`${value}T00:00:00.000Z`);
  if (Number.isNaN(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) fail(kind);
  return value;
}

function safeHttpUrl(value, kind) {
  if (typeof value !== "string" || !value.trim() || /[\u0000-\u001f\u007f]/u.test(value)) fail(kind);
  try {
    const parsed = new URL(value.trim());
    if (!(["http:", "https:"].includes(parsed.protocol) && parsed.host)) fail(kind);
  } catch {
    fail(kind);
  }
  return value;
}

function clone(value) {
  if (Array.isArray(value)) return value.map(clone);
  if (isPlainObject(value)) return Object.fromEntries(Object.keys(value).map((key) => [key, clone(value[key])]));
  return value;
}

function freeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freeze(child);
  return Object.freeze(value);
}

function frozen(value) {
  return freeze(clone(value));
}

function sameValue(left, right) {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) {
    return left.length === right.length && left.every((value, index) => sameValue(value, right[index]));
  }
  if (!isPlainObject(left) || !isPlainObject(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index] && sameValue(left[key], right[key]));
}

function canonicalSeriesCode(value, kind) {
  if (typeof value !== "string" || !SERIES_RE.test(value)) fail(kind);
  return value;
}

function canonicalVideoCode(value, kind) {
  if (typeof value !== "string" || normalizeVideoCode(value) !== value) fail(kind);
  return value;
}

function relativePath(value, expected, kind) {
  if (typeof value !== "string" || value !== expected) fail(kind);
  return value;
}

function links(value, kind, { series = false } = {}) {
  if (!isPlainObject(value) || !Object.keys(value).length) fail(kind);
  for (const [name, child] of Object.entries(value)) {
    if (!name || /[\u0000-\u001f\u007f]/u.test(name)) fail(kind);
    if (isPlainObject(child)) {
      if (series) fail(kind);
      links(child, kind);
    } else {
      safeHttpUrl(child, kind);
    }
  }
  if (series && (Object.keys(value).length !== 1 || !Object.hasOwn(value, "subtitle"))) fail(kind);
  return value;
}

function video(value, kind, { includeSeries } = {}) {
  const required = ["code", "number", "title", "actors", "releaseDate", "cover"];
  if (includeSeries) required.push("series");
  const allowed = [...required, "productId", "previewBase", "previewCount", "links"];
  object(value, kind, { required, allowed });
  const code = canonicalVideoCode(value.code, kind);
  const number = integer(value.number, kind);
  const split = code.lastIndexOf("-");
  if (`${code.slice(0, split)}-${number}` !== code) fail(kind);
  text(value.title, kind);
  if (!Array.isArray(value.actors) || value.actors.some((actor) => typeof actor !== "string" || !actor.trim())) fail(kind);
  date(value.releaseDate, kind);
  if (value.cover !== null) safeHttpUrl(value.cover, kind);
  if (Object.hasOwn(value, "series")) canonicalSeriesCode(value.series, kind);
  if (Object.hasOwn(value, "productId")) integer(value.productId, kind, { minimum: 1 });
  const hasBase = Object.hasOwn(value, "previewBase");
  const hasCount = Object.hasOwn(value, "previewCount");
  if (hasBase !== hasCount) fail(kind);
  if (hasBase) {
    safeHttpUrl(value.previewBase, kind);
    integer(value.previewCount, kind, { minimum: 1, maximum: 99 });
  }
  if (Object.hasOwn(value, "links")) links(value.links, kind);
  return value;
}

function summary(value, generation, kind, { withVideos = false } = {}) {
  object(value, kind, {
    required: ["code", "count", "firstReleaseDate", "latestReleaseDate", "artifact"],
    allowed: ["code", "count", "firstReleaseDate", "latestReleaseDate", "links", "artifact", ...(withVideos ? ["videos"] : [])],
  });
  const code = canonicalSeriesCode(value.code, kind);
  integer(value.count, kind, { minimum: 1 });
  date(value.firstReleaseDate, kind);
  date(value.latestReleaseDate, kind);
  if (value.firstReleaseDate > value.latestReleaseDate) fail(kind);
  relativePath(value.artifact, `runtime/g/${generation}/series/${code.toLowerCase()}.json`, kind);
  if (Object.hasOwn(value, "links")) links(value.links, kind, { series: true });
  return value;
}

function headers(value, bootstrap, kind, payloadKeys) {
  object(value, kind, { required: payloadKeys, allowed: payloadKeys });
  if (value.schemaVersion !== 3 || value.generation !== bootstrap.generation
    || value.generatedAt !== bootstrap.generatedAt) fail(kind);
}

function validateResources(value, kind) {
  object(value, kind, { required: [], allowed: ["subtitleDirectory"] });
  if (!Object.hasOwn(value, "subtitleDirectory")) return;
  const directory = object(value.subtitleDirectory, kind, {
    required: ["label", "url"], allowed: ["label", "url"],
  });
  if (directory.label !== "SRT ENGSUB DOWNLOAD") fail(kind);
  safeHttpUrl(directory.url, kind);
}

function validateRefresh(value, kind) {
  if (!isPlainObject(value)) fail(kind);
  const walk = (child) => {
    if (Array.isArray(child)) {
      for (const item of child) walk(item);
    } else if (isPlainObject(child)) {
      for (const item of Object.values(child)) walk(item);
    } else if (typeof child === "number") {
      integer(child, kind);
    } else if (typeof child === "boolean" || typeof child === "string" || child === null) {
      return;
    } else {
      fail(kind);
    }
  };
  walk(value);
}

export function parseBootstrap(value) {
  const kind = "bootstrap";
  object(value, kind, {
    required: ["schemaVersion", "generation", "generatedAt", "totals", "refresh", "resources", "artifacts", "recentVideos", "series"],
    allowed: ["schemaVersion", "generation", "generatedAt", "totals", "refresh", "resources", "artifacts", "recentVideos", "series"],
  });
  if (value.schemaVersion !== 3 || typeof value.generation !== "string" || !GENERATION_RE.test(value.generation)) fail(kind);
  text(value.generatedAt, kind);
  const totals = object(value.totals, kind, {
    required: ["series", "videos", "linkedVideos"], allowed: ["series", "videos", "linkedVideos"],
  });
  integer(totals.series, kind);
  integer(totals.videos, kind);
  integer(totals.linkedVideos, kind);
  validateRefresh(value.refresh, kind);
  validateResources(value.resources, kind);
  object(value.artifacts, kind, { required: ["search", "tags"], allowed: ["search", "tags"] });
  relativePath(value.artifacts.search, `runtime/g/${value.generation}/search.json`, kind);
  relativePath(value.artifacts.tags, `runtime/g/${value.generation}/tags.json`, kind);
  if (!Array.isArray(value.series) || !Array.isArray(value.recentVideos)) fail(kind);
  const seriesCodes = new Set();
  let videoCount = 0;
  for (const item of value.series) {
    summary(item, value.generation, kind);
    if (seriesCodes.has(item.code)) fail(kind);
    seriesCodes.add(item.code);
    videoCount += item.count;
  }
  if (totals.series !== value.series.length || totals.videos !== videoCount || totals.linkedVideos > totals.videos) fail(kind);
  const recentCodes = new Set();
  for (const item of value.recentVideos) {
    video(item, kind, { includeSeries: true });
    if (!seriesCodes.has(item.series) || recentCodes.has(item.code)
      || item.code.slice(0, item.code.lastIndexOf("-")) !== item.series) fail(kind);
    recentCodes.add(item.code);
  }
  return frozen(value);
}

export function parseSearchPayload(value, bootstrap) {
  const checked = parseBootstrap(bootstrap);
  const kind = "search";
  headers(value, checked, kind, ["schemaVersion", "generation", "generatedAt", "videos"]);
  if (!Array.isArray(value.videos) || value.videos.length !== checked.totals.videos) fail(kind);
  const seriesCodes = new Set(checked.series.map((item) => item.code));
  const codes = new Set();
  for (const item of value.videos) {
    video(item, kind, { includeSeries: true });
    if (!seriesCodes.has(item.series) || codes.has(item.code)
      || item.code.slice(0, item.code.lastIndexOf("-")) !== item.series) fail(kind);
    codes.add(item.code);
  }
  return frozen(value);
}

export function parseSeriesPayload(value, bootstrap, code) {
  const checked = parseBootstrap(bootstrap);
  const kind = "series";
  canonicalSeriesCode(code, kind);
  headers(value, checked, kind, ["schemaVersion", "generation", "generatedAt", "series"]);
  object(value.series, kind, {
    required: ["code", "count", "firstReleaseDate", "latestReleaseDate", "artifact", "videos"],
    allowed: ["code", "count", "firstReleaseDate", "latestReleaseDate", "links", "artifact", "videos"],
  });
  if (value.series.code !== code) fail(kind);
  summary(value.series, checked.generation, kind, { withVideos: true });
  const expected = checked.series.find((item) => item.code === code);
  if (!expected || ["code", "count", "firstReleaseDate", "latestReleaseDate", "artifact", "links"].some(
    (key) => !sameValue(value.series[key], expected[key]),
  )) fail(kind);
  if (!Array.isArray(value.series.videos) || value.series.videos.length !== value.series.count) fail(kind);
  const codes = new Set();
  for (const item of value.series.videos) {
    video(item, kind);
    if (item.code.slice(0, item.code.lastIndexOf("-")) !== code || codes.has(item.code)) fail(kind);
    codes.add(item.code);
  }
  return frozen(value);
}

export function parseTagPayload(value, bootstrap) {
  const checked = parseBootstrap(bootstrap);
  const kind = "tags";
  headers(value, checked, kind, ["schemaVersion", "generation", "generatedAt", "tags", "assignments"]);
  if (!Array.isArray(value.tags) || !Array.isArray(value.assignments)) fail(kind);
  const tagIds = new Set();
  const counts = new Map();
  for (const tag of value.tags) {
    object(tag, kind, { required: ["id", "group", "nameJa", "nameZh", "count"], allowed: ["id", "group", "nameJa", "nameZh", "count"] });
    integer(tag.id, kind, { minimum: 1 });
    if (!(["genre", "character"].includes(tag.group)) || tagIds.has(tag.id)) fail(kind);
    text(tag.nameJa, kind);
    text(tag.nameZh, kind);
    counts.set(tag.id, integer(tag.count, kind));
    tagIds.add(tag.id);
  }
  const assigned = new Set();
  const observed = new Map([...tagIds].map((id) => [id, 0]));
  for (const assignment of value.assignments) {
    if (!Array.isArray(assignment) || assignment.length !== 2) fail(kind);
    const [code, ids] = assignment;
    canonicalVideoCode(code, kind);
    if (assigned.has(code) || !Array.isArray(ids) || !ids.length) fail(kind);
    assigned.add(code);
    const unique = new Set();
    for (const id of ids) {
      integer(id, kind, { minimum: 1 });
      if (!tagIds.has(id) || unique.has(id)) fail(kind);
      unique.add(id);
      observed.set(id, observed.get(id) + 1);
    }
  }
  for (const [id, count] of counts) if (observed.get(id) !== count) fail(kind);
  return frozen(value);
}

function addTags(value, assignments) {
  return frozen({ ...value, tagIds: assignments.get(value.code) ?? [] });
}

export function createRuntimeCatalogStore(bootstrap) {
  const checked = parseBootstrap(bootstrap);
  const rawSeries = new Map();
  const rawRecent = new Map(checked.recentVideos.map((item) => [item.code, item]));
  let rawSearch = new Map();
  let assignments = new Map();
  let tagIndex = createTagIndex([]);

  const currentRecent = () => Object.freeze([...rawRecent.values()].map((item) => addTags(item, assignments)));
  const currentSearch = () => new Map([...rawSearch].map(([code, item]) => [code, addTags(item, assignments)]));

  return Object.freeze({
    metadata: frozen({
      schemaVersion: checked.schemaVersion,
      generation: checked.generation,
      generatedAt: checked.generatedAt,
      totals: checked.totals,
      refresh: checked.refresh,
      resources: checked.resources,
    }),
    getSeriesSummaries() {
      return checked.series;
    },
    getRecentVideos() {
      return currentRecent();
    },
    getSeries(code) {
      const key = typeof code === "string" ? code.trim().toUpperCase() : "";
      const payload = rawSeries.get(key);
      if (!payload) return null;
      return frozen({ ...payload.series, videos: payload.series.videos.map((item) => addTags(item, assignments)) });
    },
    getVideo(code) {
      const key = normalizeVideoCode(code);
      if (!key) return null;
      for (const payload of rawSeries.values()) {
        const found = payload.series.videos.find((item) => item.code === key);
        if (found) return addTags(found, assignments);
      }
      if (rawRecent.has(key)) return addTags(rawRecent.get(key), assignments);
      return currentSearch().get(key) ?? null;
    },
    search(query) {
      const needle = normalizeText(normalizeVideoCode(query) ?? query);
      if (!needle) return Object.freeze([]);
      return Object.freeze([...currentSearch().values()].filter((item) => {
        const tags = item.tagIds.map((id) => tagIndex.get(id)).filter(Boolean);
        return normalizeText([item.code, item.title, ...item.actors, item.series,
          ...tags.flatMap((tag) => [tag.nameZh, tag.nameJa])].join(" ")).includes(needle);
      }));
    },
    getTag(id) {
      return tagIndex.get(id);
    },
    getTags(group) {
      return group === undefined ? tagIndex.getAll() : tagIndex.getGroup(group);
    },
    filterByTags(options) {
      return filterVideosByTags([...currentSearch().values()], options);
    },
    installSeries(payload) {
      const code = payload?.series?.code;
      const next = parseSeriesPayload(payload, checked, code);
      rawSeries.set(code, next);
    },
    installSearch(payload) {
      const next = parseSearchPayload(payload, checked);
      rawSearch = new Map(next.videos.map((item) => [item.code, item]));
    },
    installTags(payload) {
      const next = parseTagPayload(payload, checked);
      const searchCodes = new Set(rawSearch.keys());
      const nextAssignments = new Map();
      for (const [code, ids] of next.assignments) {
        if (!searchCodes.has(code)) fail("tags");
        nextAssignments.set(code, ids);
      }
      assignments = nextAssignments;
      tagIndex = createTagIndex(next.tags);
    },
  });
}
