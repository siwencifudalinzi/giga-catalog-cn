import test from "node:test";
import assert from "node:assert/strict";
import { createCatalogPrefetcher } from "../../public/js/catalog-prefetch.js";

test("intent warming respects save-data and slow connections", () => {
  for (const connection of [{ saveData: true }, { effectiveType: "2g" }, { effectiveType: "slow-2g" }, { downlink: 0.5 }]) {
    let calls = 0;
    const warm = createCatalogPrefetcher({ getLoader: () => ({ ensureSearch: () => { calls++; } }), getGeneration: () => "one", connection });
    warm("search"); assert.equal(calls, 0);
  }
});
test("warming coalesces repeated intent and is bounded within each generation", async () => {
  const calls = []; let generation = "one";
  const loader = { ensureSearch: async () => calls.push("search"), ensureSeries: async (code) => calls.push(code) };
  const warm = createCatalogPrefetcher({ getLoader: () => loader, getGeneration: () => generation });
  warm("search"); warm("search"); warm("series", "ABC"); warm("series", "DEF"); warm("series", "GHI");
  await Promise.resolve();
  assert.deepEqual(calls, ["search", "ABC", "DEF"]);
  generation = "two"; warm("series", "GHI");
  await Promise.resolve();
  assert.deepEqual(calls, ["search", "ABC", "DEF", "GHI"]);
});
