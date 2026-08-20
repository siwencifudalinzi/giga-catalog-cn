function normalizeText(value) {
  return typeof value === "string"
    ? value.normalize("NFKC").trim().replace(/\s+/gu, " ").toLowerCase()
    : "";
}

function normalizeTagIds(values) {
  if (!Array.isArray(values)) {
    return [];
  }
  return [...new Set(values.filter(
    (value) => Number.isInteger(value) && value > 0,
  ))].sort((left, right) => left - right);
}

function compareTags(left, right) {
  const byCount = (right.count ?? 0) - (left.count ?? 0);
  if (byCount) {
    return byCount;
  }
  const leftName = normalizeText(left.nameZh || left.nameJa);
  const rightName = normalizeText(right.nameZh || right.nameJa);
  return leftName.localeCompare(rightName, "zh-CN") || left.id - right.id;
}

export function createTagIndex(source = []) {
  const byId = new Map();
  for (const value of Array.isArray(source) ? source : []) {
    if (
      !value ||
      typeof value !== "object" ||
      !Number.isInteger(value.id) ||
      value.id <= 0 ||
      !["genre", "character"].includes(value.group) ||
      byId.has(value.id)
    ) {
      continue;
    }
    const nameJa = typeof value.nameJa === "string" ? value.nameJa.trim() : "";
    const nameZh = typeof value.nameZh === "string" ? value.nameZh.trim() : "";
    if (!nameJa || !nameZh) {
      continue;
    }
    const tag = Object.freeze({
      id: value.id,
      group: value.group,
      nameJa,
      nameZh,
      count: Number.isInteger(value.count) && value.count >= 0 ? value.count : 0,
    });
    byId.set(tag.id, tag);
  }
  const all = Object.freeze([...byId.values()].sort(compareTags));
  const groups = new Map(
    ["genre", "character"].map((group) => [
      group,
      Object.freeze(all.filter((tag) => tag.group === group)),
    ]),
  );
  const searchRows = all.map((tag) => ({
    tag,
    haystack: normalizeText(`${tag.nameZh} ${tag.nameJa}`),
  }));

  return Object.freeze({
    get(id) {
      return Number.isInteger(id) ? byId.get(id) ?? null : null;
    },
    getAll() {
      return all;
    },
    getGroup(group) {
      return groups.get(group) ?? Object.freeze([]);
    },
    search(query) {
      const needle = normalizeText(query);
      if (!needle) {
        return Object.freeze([]);
      }
      return Object.freeze(
        searchRows
          .filter((entry) => entry.haystack.includes(needle))
          .map((entry) => entry.tag),
      );
    },
  });
}

export function filterVideosByTags(videos, options = {}) {
  const include = normalizeTagIds(options.include);
  const exclude = new Set(normalizeTagIds(options.exclude));
  const match = options.match === "any" ? "any" : "all";
  return Object.freeze(
    (Array.isArray(videos) ? videos : []).filter((video) => {
      const assigned = new Set(normalizeTagIds(video?.tagIds));
      if ([...exclude].some((tagId) => assigned.has(tagId))) {
        return false;
      }
      if (!include.length) {
        return true;
      }
      return match === "any"
        ? include.some((tagId) => assigned.has(tagId))
        : include.every((tagId) => assigned.has(tagId));
    }),
  );
}
