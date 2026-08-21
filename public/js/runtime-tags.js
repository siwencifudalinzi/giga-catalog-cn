function clone(value) {
  if (Array.isArray(value)) return value.map(clone);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, clone(child)]));
  }
  return value;
}

function fail(message) {
  throw new TypeError(`Invalid runtime tag payload: ${message}`);
}

export function hydrateCatalogTags(core, payload) {
  if (!core || typeof core !== "object" || !Array.isArray(core.series)) {
    fail("core catalog shape");
  }
  if (
    !payload ||
    typeof payload !== "object" ||
    payload.schemaVersion !== 1 ||
    payload.generatedAt !== core.generatedAt ||
    !Array.isArray(payload.tags) ||
    !Array.isArray(payload.assignments)
  ) {
    fail("header or generation");
  }

  const videos = new Map();
  for (const series of core.series) {
    if (!series || typeof series !== "object" || !Array.isArray(series.videos)) {
      fail("series shape");
    }
    for (const video of series.videos) {
      const code = typeof video?.code === "string" ? video.code : "";
      if (!code || videos.has(code)) fail("video code");
      videos.set(code, video);
    }
  }

  const tagIds = new Set();
  const expectedCounts = new Map();
  for (const tag of payload.tags) {
    if (
      !tag ||
      typeof tag !== "object" ||
      !Number.isInteger(tag.id) ||
      tag.id <= 0 ||
      tagIds.has(tag.id) ||
      !Number.isInteger(tag.count) ||
      tag.count < 0
    ) {
      fail("tag definition");
    }
    tagIds.add(tag.id);
    expectedCounts.set(tag.id, tag.count);
  }

  const assignments = new Map();
  const observedCounts = new Map([...tagIds].map((id) => [id, 0]));
  for (const assignment of payload.assignments) {
    if (!Array.isArray(assignment) || assignment.length !== 2) fail("assignment shape");
    const [code, ids] = assignment;
    if (!videos.has(code) || assignments.has(code) || !Array.isArray(ids) || !ids.length) {
      fail("assignment boundary");
    }
    const unique = new Set();
    for (const id of ids) {
      if (!Number.isInteger(id) || !tagIds.has(id) || unique.has(id)) fail("assignment tag");
      unique.add(id);
      observedCounts.set(id, observedCounts.get(id) + 1);
    }
    assignments.set(code, [...ids]);
  }
  for (const [id, expected] of expectedCounts) {
    if (observedCounts.get(id) !== expected) fail("tag count");
  }

  const hydrated = clone(core);
  hydrated.tags = clone(payload.tags);
  for (const series of hydrated.series) {
    for (const video of series.videos) {
      video.tagIds = clone(assignments.get(video.code) ?? []);
    }
  }
  return hydrated;
}

export function createLazyTagLoader({ getCore, loadPayload, onHydrated }) {
  let value;
  let pending = null;
  return function ensureTags() {
    if (value) return Promise.resolve(value);
    if (!pending) {
      pending = Promise.resolve()
        .then(loadPayload)
        .then((payload) => hydrateCatalogTags(getCore(), payload))
        .then((catalog) => {
          value = catalog;
          onHydrated?.(catalog);
          return catalog;
        })
        .catch((error) => {
          pending = null;
          throw error;
        });
    }
    return pending;
  };
}
