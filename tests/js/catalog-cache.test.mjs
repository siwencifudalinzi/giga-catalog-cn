import assert from "node:assert/strict";
import test from "node:test";

import { openCatalogCache, unavailableCatalogCache } from "../../public/js/catalog-cache.js";

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

function request(result, transaction) {
  const value = { result, error: null, onsuccess: null, onerror: null };
  queueMicrotask(() => {
    if (transaction.failed) return;
    value.onsuccess?.({ target: value });
    transaction.finish();
  });
  return value;
}

function createFakeIndexedDB() {
  const databases = new Map();
  return {
    open(name, version) {
      const openRequest = { result: null, error: null, onsuccess: null, onerror: null, onupgradeneeded: null };
      queueMicrotask(() => {
        let state = databases.get(name);
        if (!state) {
          state = { version: 0, stores: new Map() };
          databases.set(name, state);
        }
        const db = {
          get objectStoreNames() {
            return { contains: (storeName) => state.stores.has(storeName) };
          },
          createObjectStore(storeName, options) {
            state.stores.set(storeName, { keyPath: options.keyPath, values: new Map() });
          },
          transaction(storeNames, mode) {
            const names = Array.isArray(storeNames) ? storeNames : [storeNames];
            const transaction = {
              mode,
              oncomplete: null,
              onabort: null,
              onerror: null,
              error: null,
              pending: 0,
              finished: false,
              failed: false,
              finish() {
                this.pending -= 1;
                if (this.pending === 0 && !this.finished && !this.failed) {
                  this.finished = true;
                  queueMicrotask(() => this.oncomplete?.({ target: this }));
                }
              },
              objectStore(storeName) {
                if (!names.includes(storeName)) throw new Error("store not in transaction");
                const store = state.stores.get(storeName);
                return {
                  get(key) {
                    transaction.pending += 1;
                    return request(store.values.has(key) ? clone(store.values.get(key)) : undefined, transaction);
                  },
                  put(value) {
                    transaction.pending += 1;
                    const key = value[store.keyPath];
                    store.values.set(key, clone(value));
                    return request(key, transaction);
                  },
                  delete(key) {
                    transaction.pending += 1;
                    store.values.delete(key);
                    return request(undefined, transaction);
                  },
                };
              },
            };
            queueMicrotask(() => {
              if (transaction.pending === 0 && !transaction.finished) {
                transaction.finished = true;
                transaction.oncomplete?.({ target: transaction });
              }
            });
            return transaction;
          },
          close() {},
        };
        openRequest.result = db;
        if (version > state.version) {
          const event = { target: openRequest, oldVersion: state.version, newVersion: version };
          state.version = version;
          openRequest.onupgradeneeded?.(event);
        }
        openRequest.onsuccess?.({ target: openRequest });
      });
      return openRequest;
    },
  };
}

test("cache clones validated bootstrap and declared artifacts, then prunes old generations", async () => {
  const cache = await openCatalogCache(createFakeIndexedDB());
  const first = bootstrap("a".repeat(64));
  const second = bootstrap("b".repeat(64));
  const third = bootstrap("c".repeat(64));
  const search = searchPayload(first);

  await cache.putBootstrap(first);
  await cache.putArtifact(first.generation, first.artifacts.search, search);
  first.recentVideos[0].title = "mutated after write";
  search.videos[0].title = "mutated after write";

  const cachedBootstrap = await cache.getLatestBootstrap();
  const cachedSearch = await cache.getArtifact("a".repeat(64), `runtime/g/${"a".repeat(64)}/search.json`);
  assert.equal(cachedBootstrap.recentVideos[0].title, "女战士");
  assert.equal(cachedSearch.videos[0].title, "女战士");
  cachedBootstrap.recentVideos[0].title = "mutated after read";
  assert.equal((await cache.getLatestBootstrap()).recentVideos[0].title, "女战士");

  await cache.putArtifact(first.generation, "runtime/unapproved.json", { ignored: true });
  assert.equal(await cache.getArtifact(first.generation, "runtime/unapproved.json"), null);

  await cache.putBootstrap(second);
  await cache.putBootstrap(third);
  await cache.prune(2);
  assert.equal(await cache.getArtifact(first.generation, first.artifacts.search), null);
  assert.equal((await cache.getLatestBootstrap()).generation, third.generation);
  cache.close();
});

test("unavailable or failing IndexedDB produces a safe no-op cache without localStorage access", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() { throw new Error("catalog cache must not access localStorage"); },
  });
  try {
    for (const indexedDB of [undefined, { open() { throw new Error("blocked"); } }]) {
      const cache = await openCatalogCache(indexedDB);
      assert.equal(cache.available, false);
      await cache.putBootstrap(bootstrap("d".repeat(64)));
      await cache.putArtifact("d".repeat(64), "runtime/ignored.json", {});
      await cache.prune();
      assert.equal(await cache.getLatestBootstrap(), null);
      assert.equal(await cache.getArtifact("d".repeat(64), "runtime/ignored.json"), null);
      cache.close();
    }
    assert.equal(unavailableCatalogCache().available, false);
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
});
