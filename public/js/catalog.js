import { createTagIndex, filterVideosByTags } from "./tags.js";

function cloneValue(value) {
  if (Array.isArray(value)) {
    return value.map(cloneValue);
  }
  if (value && typeof value === "object") {
    const clone = {};
    for (const key of Object.keys(value)) {
      clone[key] = cloneValue(value[key]);
    }
    return clone;
  }
  return value;
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) {
    return value;
  }
  for (const child of Object.values(value)) {
    deepFreeze(child);
  }
  return Object.freeze(value);
}

function normalizeText(value) {
  return typeof value === "string"
    ? value.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase()
    : "";
}

function normalizeVideoCode(value) {
  if (typeof value !== "string") {
    return null;
  }
  const match = value.normalize("NFKC").match(
    /^\s*([A-Za-z][A-Za-z0-9]*)[\s_-](\d+)\s*$/u,
  );
  if (!match) {
    return null;
  }
  const number = Number.parseInt(match[2], 10);
  return number >= 0 ? `${match[1].toUpperCase()}-${number}` : null;
}

function compareText(left, right) {
  if (left === right) {
    return 0;
  }
  return left < right ? -1 : 1;
}

function compareRecentSeries(left, right) {
  const byDate = compareText(
    String(right.latestReleaseDate ?? ""),
    String(left.latestReleaseDate ?? ""),
  );
  return byDate || compareText(left.code, right.code);
}

function compareVideos(left, right) {
  const leftNumber = Number.isFinite(left.number)
    ? left.number
    : Number.MAX_SAFE_INTEGER;
  const rightNumber = Number.isFinite(right.number)
    ? right.number
    : Number.MAX_SAFE_INTEGER;
  return leftNumber - rightNumber || compareText(left.code, right.code);
}

function buildSeries(source) {
  if (!source || typeof source !== "object") {
    return null;
  }
  const code = String(source.code ?? "").trim().toUpperCase();
  if (!code) {
    return null;
  }
  const videos = Array.isArray(source.videos)
    ? source.videos
        .filter((video) => video && typeof video === "object")
        .map((video) => ({ ...cloneValue(video), series: code }))
        .sort(compareVideos)
        .map(deepFreeze)
    : [];
  return deepFreeze({
    ...cloneValue(source),
    code,
    videos,
  });
}

function normalizedLimit(limit, length) {
  if (limit === undefined) {
    return length;
  }
  const number = Number(limit);
  if (!Number.isFinite(number) || number <= 0) {
    return 0;
  }
  return Math.min(Math.trunc(number), length);
}

/**
 * Build a DOM-independent, immutable view over a generated catalog payload.
 */
export function createCatalogModel(payload = {}) {
  const tagIndex = createTagIndex(payload?.tags);
  const sourceSeries = Array.isArray(payload?.series) ? payload.series : [];
  const recentSeries = sourceSeries
    .map(buildSeries)
    .filter(Boolean)
    .sort(compareRecentSeries);
  const seriesByCode = new Map();
  const videosByCode = new Map();
  const searchIndex = [];
  const allVideos = [];

  for (const series of recentSeries) {
    seriesByCode.set(series.code, series);
    for (const video of series.videos) {
      const code = normalizeVideoCode(video.code);
      if (!code || videosByCode.has(code)) {
        continue;
      }
      videosByCode.set(code, video);
      allVideos.push(video);
      const actors = Array.isArray(video.actors) ? video.actors : [];
      const assignedTags = Array.isArray(video.tagIds)
        ? video.tagIds.map((tagId) => tagIndex.get(tagId)).filter(Boolean)
        : [];
      const haystack = normalizeText(
        [
          video.code,
          video.title,
          ...actors,
          series.code,
          ...assignedTags.flatMap((tag) => [tag.nameZh, tag.nameJa]),
        ].join(" "),
      );
      searchIndex.push(Object.freeze({ video, haystack }));
    }
  }

  const metadata = deepFreeze({
    schemaVersion: cloneValue(payload?.schemaVersion),
    generatedAt: cloneValue(payload?.generatedAt),
    totals: cloneValue(payload?.totals ?? {}),
    refresh: cloneValue(payload?.refresh ?? {}),
    resources: cloneValue(payload?.resources ?? {}),
  });

  return Object.freeze({
    metadata,

    search(query) {
      const needle = normalizeText(normalizeVideoCode(query) ?? query);
      if (!needle) {
        return Object.freeze([]);
      }
      return Object.freeze(
        searchIndex
          .filter((entry) => entry.haystack.includes(needle))
          .map((entry) => entry.video),
      );
    },

    getSeries(code) {
      const key =
        typeof code === "string" ? code.trim().toUpperCase() : "";
      return seriesByCode.get(key) ?? null;
    },

    getRecentSeries(limit) {
      const count = normalizedLimit(limit, recentSeries.length);
      return Object.freeze(recentSeries.slice(0, count));
    },

    getVideo(code) {
      const key = normalizeVideoCode(code);
      return key ? videosByCode.get(key) ?? null : null;
    },

    getTag(id) {
      return tagIndex.get(id);
    },

    getTags(group) {
      return group === undefined ? tagIndex.getAll() : tagIndex.getGroup(group);
    },

    searchTags(query) {
      return tagIndex.search(query);
    },

    filterByTags(options) {
      return filterVideosByTags(allVideos, options);
    },
  });
}
