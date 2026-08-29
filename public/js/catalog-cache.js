import { parseBootstrap } from "./runtime-catalog.js";

const DATABASE_NAME = "giga_catalog_runtime_v3";
const DATABASE_VERSION = 1;
const BOOTSTRAPS = "bootstraps";
const ARTIFACTS = "artifacts";
const META = "meta";
const LATEST_KEY = "latestGeneration";
const GENERATIONS_KEY = "generations";

function clone(value) {
  return value === undefined ? value : structuredClone(value);
}

function noOpCache() {
  return Object.freeze({
    available: false,
    async getLatestBootstrap() { return null; },
    async getArtifact() { return null; },
    async putBootstrap() {},
    async putArtifact() {},
    async prune() {},
    close() {},
  });
}

export function unavailableCatalogCache() {
  return noOpCache();
}

function requestResult(request) {
  return request?.result;
}

function transaction(db, stores, mode, operation) {
  return new Promise((resolve, reject) => {
    let tx;
    let result;
    try {
      tx = db.transaction(stores, mode);
      tx.oncomplete = () => {
        try {
          resolve(typeof result === "function" ? result() : result);
        } catch (error) {
          reject(error);
        }
      };
      tx.onerror = () => reject(tx.error ?? new Error("IndexedDB transaction failed"));
      tx.onabort = () => reject(tx.error ?? new Error("IndexedDB transaction aborted"));
      result = operation(tx);
    } catch (error) {
      reject(error);
    }
  });
}

function getRecord(db, storeName, key) {
  return transaction(db, [storeName], "readonly", (tx) => {
    const request = tx.objectStore(storeName).get(key);
    return () => requestResult(request);
  });
}

function readMeta(db) {
  return transaction(db, [META], "readonly", (tx) => {
    const store = tx.objectStore(META);
    const latest = store.get(LATEST_KEY);
    const generations = store.get(GENERATIONS_KEY);
    return () => ({
      latestGeneration: requestResult(latest)?.value ?? null,
      generations: Array.isArray(requestResult(generations)?.value)
        ? [...requestResult(generations).value]
        : [],
    });
  });
}

function declaredPaths(bootstrap) {
  return [
    bootstrap.artifacts.search,
    bootstrap.artifacts.tags,
    ...bootstrap.series.map((item) => item.artifact),
  ];
}

function createCache(db) {
  return Object.freeze({
    available: true,

    async getLatestBootstrap() {
      const meta = await readMeta(db);
      if (!meta.latestGeneration) return null;
      const value = await getRecord(db, BOOTSTRAPS, meta.latestGeneration);
      return value ? clone(value) : null;
    },

    async getArtifact(generation, path) {
      const value = await getRecord(db, ARTIFACTS, `${generation}:${path}`);
      return value?.payload === undefined ? null : clone(value.payload);
    },

    async putBootstrap(value) {
      const checked = parseBootstrap(value);
      const meta = await readMeta(db);
      const generations = meta.generations.filter((item) => item !== checked.generation);
      generations.push(checked.generation);
      await transaction(db, [BOOTSTRAPS, META], "readwrite", (tx) => {
        tx.objectStore(BOOTSTRAPS).put(clone(checked));
        tx.objectStore(META).put({ key: LATEST_KEY, value: checked.generation });
        tx.objectStore(META).put({ key: GENERATIONS_KEY, value: clone(generations) });
      });
    },

    async putArtifact(generation, path, payload) {
      const bootstrap = await getRecord(db, BOOTSTRAPS, generation);
      if (!bootstrap || !declaredPaths(bootstrap).includes(path)) return;
      await transaction(db, [ARTIFACTS], "readwrite", (tx) => {
        tx.objectStore(ARTIFACTS).put({
          key: `${generation}:${path}`,
          generation,
          path,
          payload: clone(payload),
        });
      });
    },

    async prune(keep = 2) {
      const count = Number.isInteger(keep) && keep >= 0 ? keep : 2;
      const meta = await readMeta(db);
      const generations = meta.generations;
      const retained = generations.slice(Math.max(0, generations.length - count));
      const removed = generations.slice(0, Math.max(0, generations.length - count));
      if (!removed.length) return;
      const oldBootstraps = await Promise.all(removed.map((generation) => getRecord(db, BOOTSTRAPS, generation)));
      await transaction(db, [BOOTSTRAPS, ARTIFACTS, META], "readwrite", (tx) => {
        const bootstrapStore = tx.objectStore(BOOTSTRAPS);
        const artifactStore = tx.objectStore(ARTIFACTS);
        removed.forEach((generation, index) => {
          bootstrapStore.delete(generation);
          const bootstrap = oldBootstraps[index];
          if (bootstrap) {
            declaredPaths(bootstrap).forEach((path) => artifactStore.delete(`${generation}:${path}`));
          }
        });
        tx.objectStore(META).put({ key: GENERATIONS_KEY, value: clone(retained) });
        if (meta.latestGeneration && removed.includes(meta.latestGeneration)) {
          tx.objectStore(META).put({
            key: LATEST_KEY,
            value: retained.at(-1) ?? null,
          });
        }
      });
    },

    close() {
      db.close?.();
    },
  });
}

export async function openCatalogCache(indexedDB = globalThis.indexedDB) {
  if (!indexedDB || typeof indexedDB.open !== "function") return unavailableCatalogCache();
  try {
    const db = await new Promise((resolve, reject) => {
      let request;
      try {
        request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          if (!database.objectStoreNames.contains(BOOTSTRAPS)) {
            database.createObjectStore(BOOTSTRAPS, { keyPath: "generation" });
          }
          if (!database.objectStoreNames.contains(ARTIFACTS)) {
            database.createObjectStore(ARTIFACTS, { keyPath: "key" });
          }
          if (!database.objectStoreNames.contains(META)) {
            database.createObjectStore(META, { keyPath: "key" });
          }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
        request.onblocked = () => reject(new Error("IndexedDB open blocked"));
      } catch (error) {
        reject(error);
      }
    });
    return createCache(db);
  } catch {
    return unavailableCatalogCache();
  }
}
