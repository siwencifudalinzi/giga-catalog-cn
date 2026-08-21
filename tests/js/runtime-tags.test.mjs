import assert from "node:assert/strict";
import test from "node:test";

import {
  createLazyTagLoader,
  hydrateCatalogTags,
} from "../../public/js/runtime-tags.js";


function fixture() {
  return {
    core: {
      schemaVersion: 1,
      generatedAt: "2026-08-21T00:00:00Z",
      series: [
        {
          code: "SPSF",
          videos: [
            { code: "SPSF-1", title: "One" },
            { code: "SPSF-2", title: "Two" },
          ],
        },
      ],
    },
    payload: {
      schemaVersion: 1,
      generatedAt: "2026-08-21T00:00:00Z",
      tags: [
        {
          id: 10,
          group: "genre",
          nameJa: "黒ストッキング",
          nameZh: "黑丝袜",
          count: 1,
        },
      ],
      assignments: [["SPSF-1", [10]]],
    },
  };
}

test("hydrates a matching compact tag payload without mutating inputs", () => {
  const { core, payload } = fixture();

  const hydrated = hydrateCatalogTags(core, payload);

  assert.equal(core.tags, undefined);
  assert.equal(core.series[0].videos[0].tagIds, undefined);
  assert.deepEqual(hydrated.tags, payload.tags);
  assert.deepEqual(hydrated.series[0].videos[0].tagIds, [10]);
  assert.deepEqual(hydrated.series[0].videos[1].tagIds, []);
  assert.notEqual(hydrated.tags, payload.tags);
  assert.notEqual(hydrated.series, core.series);
});

test("rejects mismatched generations and malformed assignment boundaries", () => {
  for (const mutate of [
    ({ payload }) => { payload.generatedAt = "2026-08-22T00:00:00Z"; },
    ({ payload }) => { payload.assignments.push(["SPSF-1", [10]]); },
    ({ payload }) => { payload.assignments = [["UNKNOWN-1", [10]]]; },
    ({ payload }) => { payload.assignments = [["SPSF-1", [999]]]; },
    ({ payload }) => { payload.assignments = [["SPSF-1", [10, 10]]]; },
    ({ payload }) => { payload.tags[0].count = 2; },
  ]) {
    const value = fixture();
    mutate(value);
    assert.throws(() => hydrateCatalogTags(value.core, value.payload));
  }
});

test("lazy loader shares one in-flight request and retries after failure", async () => {
  const value = fixture();
  let calls = 0;
  let fail = true;
  const hydrated = [];
  const ensure = createLazyTagLoader({
    getCore: () => value.core,
    loadPayload: async () => {
      calls += 1;
      if (fail) throw new Error("offline");
      return value.payload;
    },
    onHydrated: (catalog) => hydrated.push(catalog),
  });

  await assert.rejects(Promise.all([ensure(), ensure()]), /offline/u);
  assert.equal(calls, 1);

  fail = false;
  const [first, second] = await Promise.all([ensure(), ensure()]);
  assert.equal(calls, 2);
  assert.equal(first, second);
  assert.equal(hydrated.length, 1);
  assert.equal(await ensure(), first);
  assert.equal(calls, 2);
});
