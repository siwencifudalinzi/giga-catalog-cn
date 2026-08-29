import {
  parseBootstrap,
  parseSearchPayload,
  parseSeriesPayload,
  parseTagPayload,
} from "./runtime-catalog.js";
import { unavailableCatalogCache } from "./catalog-cache.js";

function abortError() {
  if (typeof DOMException === "function") return new DOMException("The operation was aborted", "AbortError");
  const error = new Error("The operation was aborted");
  error.name = "AbortError";
  return error;
}

function isAbort(error) {
  return error?.name === "AbortError";
}

function callerPromise(entry, signal) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const subscriber = {
      resolve: (value) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", onAbort);
        entry.subscribers.delete(subscriber);
        resolve(value);
      },
      reject: (error) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener("abort", onAbort);
        entry.subscribers.delete(subscriber);
        reject(error);
      },
    };
    const onAbort = () => subscriber.reject(abortError());
    if (signal?.aborted) {
      onAbort();
      return;
    }
    entry.subscribers.add(subscriber);
    signal?.addEventListener("abort", onAbort, { once: true });
    entry.promise.then(subscriber.resolve, subscriber.reject);
  });
}

function responsePayload(response) {
  if (!response?.ok) {
    throw new Error(`请求失败（${response?.status ?? "unknown"}）`);
  }
  return response.json();
}

export function createRuntimeLoader({
  fetcher = globalThis.fetch,
  cache,
  bootstrapUrl = new URL("../data/catalog-bootstrap.json", import.meta.url),
} = {}) {
  const cachePromise = Promise.resolve(cache ?? unavailableCatalogCache())
    .catch(() => unavailableCatalogCache());
  let activeBootstrap = null;
  const pending = new Map();

  function setBootstrap(value) {
    const checked = parseBootstrap(value);
    if (activeBootstrap?.generation !== checked.generation) {
      for (const [key, entry] of pending) {
        if (entry.generation === checked.generation) continue;
        entry.controller.abort();
        for (const subscriber of [...entry.subscribers]) subscriber.reject(abortError());
        pending.delete(key);
      }
    }
    activeBootstrap = checked;
    return checked;
  }

  function fetchBootstrap(signal) {
    if (typeof fetcher !== "function") return Promise.reject(new Error("fetch 不可用"));
    try {
      return Promise.resolve(fetcher(bootstrapUrl, {
        headers: { Accept: "application/json" },
        cache: "no-cache",
        signal,
      })).then(responsePayload).then(parseBootstrap);
    } catch (error) {
      return Promise.reject(error);
    }
  }

  function ensureArtifact(kind, code, signal) {
    const bootstrap = activeBootstrap;
    if (!bootstrap) return Promise.reject(new Error("目录尚未载入"));
    let path;
    let parser;
    if (kind === "series") {
      const normalized = typeof code === "string" ? code.trim().toUpperCase() : "";
      const summary = bootstrap.series.find((item) => item.code === normalized);
      if (!summary) return Promise.reject(new Error(`未知系列（${normalized}）`));
      path = summary.artifact;
      parser = (value) => parseSeriesPayload(value, bootstrap, normalized);
    } else if (kind === "search") {
      path = bootstrap.artifacts.search;
      parser = (value) => parseSearchPayload(value, bootstrap);
    } else {
      path = bootstrap.artifacts.tags;
      parser = (value) => parseTagPayload(value, bootstrap);
    }
    const key = `${bootstrap.generation}:${path}`;
    let entry = pending.get(key);
    if (!entry) {
      const controller = new AbortController();
      entry = { controller, generation: bootstrap.generation, subscribers: new Set(), promise: null };
      entry.promise = (async () => {
        const runtimeCache = await cachePromise;
        let cached;
        try {
          cached = await runtimeCache.getArtifact(bootstrap.generation, path);
          if (cached != null) {
            const parsed = parser(cached);
            if (activeBootstrap?.generation !== bootstrap.generation) throw abortError();
            return parsed;
          }
        } catch (error) {
          if (isAbort(error)) throw error;
          cached = null;
        }
        if (typeof fetcher !== "function") throw new Error("fetch 不可用");
        let response;
        try {
          response = await fetcher(path, {
            headers: { Accept: "application/json" },
            cache: "no-cache",
            signal: controller.signal,
          });
          const parsed = parser(await responsePayload(response));
          if (activeBootstrap?.generation !== bootstrap.generation) throw abortError();
          await runtimeCache.putArtifact(bootstrap.generation, path, parsed);
          return parsed;
        } catch (error) {
          throw error;
        }
      })();
      pending.set(key, entry);
      entry.promise.then(
        () => { if (pending.get(key) === entry) pending.delete(key); },
        () => { if (pending.get(key) === entry) pending.delete(key); },
      );
    }
    return callerPromise(entry, signal);
  }

  async function start({ signal, onCached, onFresh } = {}) {
    const network = fetchBootstrap(signal);
    const cached = cachePromise.then(async (runtimeCache) => {
      try {
        const value = await runtimeCache.getLatestBootstrap();
        return value == null ? null : parseBootstrap(value);
      } catch {
        return null;
      }
    });
    const cachedValue = await cached;
    if (cachedValue) {
      setBootstrap(cachedValue);
      onCached?.(cachedValue);
    }
    const networkResult = await Promise.allSettled([network]);
    const networkValue = networkResult[0].status === "fulfilled" ? networkResult[0].value : null;
    if (networkValue) {
      const sameGeneration = cachedValue?.generation === networkValue.generation
        || activeBootstrap?.generation === networkValue.generation;
      if (!sameGeneration) {
        setBootstrap(networkValue);
        const runtimeCache = await cachePromise;
        try {
          await runtimeCache.putBootstrap(networkValue);
          await runtimeCache.prune?.(2);
        } catch {
          // Runtime storage is an optimization; a valid network response remains usable.
        }
        onFresh?.(networkValue);
      }
      return networkValue;
    }
    if (cachedValue) return cachedValue;
    if (networkResult.status === "rejected") throw networkResult.reason;
    throw new Error("目录数据不可用");
  }

  return Object.freeze({
    start,
    ensureSeries(code, { signal } = {}) { return ensureArtifact("series", code, signal); },
    ensureSearch({ signal } = {}) { return ensureArtifact("search", null, signal); },
    ensureTags({ signal } = {}) { return ensureArtifact("tags", null, signal); },
    setBootstrap,
  });
}
