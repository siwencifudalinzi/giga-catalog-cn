import assert from "node:assert/strict";
import test from "node:test";

import { createRuntimeLoader } from "../../public/js/runtime-loader.js";

const generatedAt = "2026-08-29T00:00:00Z";

function clone(value) {
  return structuredClone(value);
}

function video(generation) {
  return {
    code: "SPSF-1",
    number: 1,
    title: "女战士",
    actors: ["演员"],
    releaseDate: "2026-08-01",
    cover: `https://images.example/${generation}.jpg`,
    series: "SPSF",
  };
}

function bootstrap(generation) {
  return {
    schemaVersion: 3,
    generation,
    generatedAt,
    totals: { series: 1, videos: 1, linkedVideos: 0 },
    refresh: { mode: "incremental", sourceComplete: true },
    resources: {},
    artifacts: {
      search: `runtime/g/${generation}/search.json`,
      tags: `runtime/g/${generation}/tags.json`,
    },
    recentVideos: [video(generation)],
    series: [{
      code: "SPSF",
      count: 1,
      firstReleaseDate: "2026-08-01",
      latestReleaseDate: "2026-08-01",
      artifact: `runtime/g/${generation}/series/spsf.json`,
    }],
  };
}

function searchPayload(value) {
  return {
    schemaVersion: 3,
    generation: value.generation,
    generatedAt: value.generatedAt,
    videos: [video(value.generation)],
  };
}

function seriesPayload(value) {
  const item = video(value.generation);
  delete item.series;
  return {
    schemaVersion: 3,
    generation: value.generation,
    generatedAt: value.generatedAt,
    series: { ...value.series[0], videos: [item] },
  };
}

function tagsPayload(value) {
  return {
    schemaVersion: 3,
    generation: value.generation,
    generatedAt: value.generatedAt,
    tags: [],
    assignments: [],
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function response(value) {
  return { ok: true, status: 200, json: async () => clone(value) };
}

function memoryCache(initial = null) {
  const bootstraps = new Map(initial ? [[initial.generation, clone(initial)]] : []);
  const artifacts = new Map();
  const calls = [];
  return {
    available: true,
    calls,
    async getLatestBootstrap() {
      calls.push("getLatestBootstrap");
      const latest = [...bootstraps.values()].at(-1);
      return latest ? clone(latest) : null;
    },
    async getArtifact(generation, path) {
      calls.push(`getArtifact:${generation}:${path}`);
      const value = artifacts.get(`${generation}:${path}`);
      return value ? clone(value) : null;
    },
    async putBootstrap(value) {
      calls.push(`putBootstrap:${value.generation}`);
      bootstraps.set(value.generation, clone(value));
    },
    async putArtifact(generation, path, value) {
      calls.push(`putArtifact:${generation}:${path}`);
      artifacts.set(`${generation}:${path}`, clone(value));
    },
    latest() {
      const value = [...bootstraps.values()].at(-1);
      return value ? clone(value) : null;
    },
    artifact(generation, path) {
      const value = artifacts.get(`${generation}:${path}`);
      return value ? clone(value) : null;
    },
  };
}

function abortError(error) {
  return error?.name === "AbortError";
}

test("start renders a valid cache before a different valid network generation", async () => {
  const oldBootstrap = bootstrap("a".repeat(64));
  const newBootstrap = bootstrap("b".repeat(64));
  const cacheReady = deferred();
  const network = deferred();
  const seen = [];
  let calls = 0;
  const loader = createRuntimeLoader({
    cache: cacheReady.promise,
    bootstrapUrl: "bootstrap.json",
    fetcher(url) {
      calls += 1;
      assert.equal(url, "bootstrap.json");
      return network.promise;
    },
  });

  const started = loader.start({
    onCached(value) { seen.push(["cache", value.generation]); },
    onFresh(value) { seen.push(["network", value.generation]); },
  });
  assert.equal(calls, 1, "network begins before the cache-opening promise resolves");
  cacheReady.resolve(memoryCache(oldBootstrap));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(seen, [["cache", oldBootstrap.generation]]);
  network.resolve(response(newBootstrap));
  await started;

  assert.deepEqual(seen, [["cache", oldBootstrap.generation], ["network", newBootstrap.generation]]);
});

test("start does not activate or cache the same generation twice", async () => {
  const value = bootstrap("a".repeat(64));
  const cache = memoryCache(value);
  const seen = [];
  const loader = createRuntimeLoader({
    cache,
    bootstrapUrl: "bootstrap.json",
    fetcher: async () => response(value),
  });

  await loader.start({
    onCached(item) { seen.push(["cache", item.generation]); },
    onFresh(item) { seen.push(["network", item.generation]); },
  });

  assert.deepEqual(seen, [["cache", value.generation]]);
  assert.equal(cache.calls.filter((item) => item.startsWith("putBootstrap:")).length, 0);
});

test("an invalid network bootstrap leaves the valid cached generation intact", async () => {
  const oldBootstrap = bootstrap("a".repeat(64));
  const invalid = bootstrap("b".repeat(64));
  invalid.schemaVersion = 2;
  const cache = memoryCache(oldBootstrap);
  const loader = createRuntimeLoader({
    cache,
    bootstrapUrl: "bootstrap.json",
    fetcher: async () => response(invalid),
  });

  await loader.start({ onCached() {}, onFresh() {} });

  assert.equal(cache.latest().generation, oldBootstrap.generation);
  assert.equal(cache.calls.filter((item) => item.startsWith("putBootstrap:")).length, 0);
});

test("concurrent series callers share an exact-path fetch and cache the parsed payload", async () => {
  const value = bootstrap("a".repeat(64));
  const cache = memoryCache();
  const network = deferred();
  const urls = [];
  const loader = createRuntimeLoader({
    cache,
    fetcher(url) {
      urls.push(url);
      return network.promise;
    },
  });
  loader.setBootstrap(value);

  const first = loader.ensureSeries("SPSF", {});
  const second = loader.ensureSeries("SPSF", {});
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(urls, [`/data/${value.series[0].artifact}`]);
  network.resolve(response(seriesPayload(value)));

  assert.deepEqual(await first, seriesPayload(value));
  assert.deepEqual(await second, seriesPayload(value));
  assert.deepEqual(await cache.artifact(value.generation, value.series[0].artifact), seriesPayload(value));
});

test("runtime artifact fetches resolve logical paths beneath the public data base", async () => {
  const value = bootstrap("a".repeat(64));
  const cache = memoryCache();
  const urls = [];
  const loader = createRuntimeLoader({
    cache,
    fetcher(url) {
      urls.push(String(url));
      return response(seriesPayload(value));
    },
  });
  loader.setBootstrap(value);
  await loader.ensureSeries("SPSF");
  assert.equal(new URL(urls[0], "http://catalog.test/").pathname,
    `/data/${value.series[0].artifact}`);
  assert.doesNotMatch(urls[0], /^(?:https?:|file:|\/runtime\/)/u);
});

test("an aborted caller does not cancel the shared fetch or poison its cached result", async () => {
  const value = bootstrap("a".repeat(64));
  const cache = memoryCache();
  const network = deferred();
  const controller = new AbortController();
  let sharedSignal;
  const loader = createRuntimeLoader({
    cache,
    fetcher(_url, options) {
      sharedSignal = options.signal;
      return network.promise;
    },
  });
  loader.setBootstrap(value);

  const aborted = loader.ensureSeries("SPSF", { signal: controller.signal });
  const survivor = loader.ensureSeries("SPSF", {});
  await new Promise((resolve) => setImmediate(resolve));
  controller.abort();
  await assert.rejects(aborted, abortError);
  assert.equal(sharedSignal.aborted, false);
  network.resolve(response(seriesPayload(value)));
  assert.deepEqual(await survivor, seriesPayload(value));
  assert.deepEqual(await loader.ensureSeries("SPSF", {}), seriesPayload(value));
});

test("a generation change aborts all old artifact callers and discards late old responses", async () => {
  const oldBootstrap = bootstrap("a".repeat(64));
  const newBootstrap = bootstrap("b".repeat(64));
  const cache = memoryCache();
  const requests = new Map();
  const loader = createRuntimeLoader({
    cache,
    fetcher(url, options) {
      const pending = deferred();
      requests.set(String(url).replace(/^\/data\//u, ""), { pending, signal: options.signal });
      return pending.promise;
    },
  });
  loader.setBootstrap(oldBootstrap);

  const series = loader.ensureSeries("SPSF", {});
  const search = loader.ensureSearch({});
  const tags = loader.ensureTags({});
  await new Promise((resolve) => setImmediate(resolve));
  loader.setBootstrap(newBootstrap);

  await Promise.all([
    assert.rejects(series, abortError),
    assert.rejects(search, abortError),
    assert.rejects(tags, abortError),
  ]);
  for (const value of requests.values()) assert.equal(value.signal.aborted, true);
  requests.get(oldBootstrap.series[0].artifact).pending.resolve(response(seriesPayload(oldBootstrap)));
  requests.get(oldBootstrap.artifacts.search).pending.resolve(response(searchPayload(oldBootstrap)));
  requests.get(oldBootstrap.artifacts.tags).pending.resolve(response(tagsPayload(oldBootstrap)));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(cache.artifact(oldBootstrap.generation, oldBootstrap.series[0].artifact), null);
  assert.equal(cache.artifact(oldBootstrap.generation, oldBootstrap.artifacts.search), null);
  assert.equal(cache.artifact(oldBootstrap.generation, oldBootstrap.artifacts.tags), null);
});

test("a generation change rejects an artifact stalled in the cache read", async () => {
  const oldBootstrap = bootstrap("a".repeat(64));
  const newBootstrap = bootstrap("b".repeat(64));
  const cacheRead = deferred();
  const cache = {
    available: true,
    getLatestBootstrap: async () => null,
    getArtifact: async () => cacheRead.promise,
    async putBootstrap() {},
    async putArtifact() {},
    async prune() {},
    close() {},
  };
  const loader = createRuntimeLoader({ cache, fetcher: async () => response(seriesPayload(oldBootstrap)) });
  loader.setBootstrap(oldBootstrap);
  const pending = loader.ensureSeries("SPSF", {});
  loader.setBootstrap(newBootstrap);
  cacheRead.resolve(seriesPayload(oldBootstrap));
  await assert.rejects(pending, abortError);
});
