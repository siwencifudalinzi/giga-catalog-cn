import assert from "node:assert/strict";
import test from "node:test";

import {
  filterLinkUpdates,
  groupLinkUpdates,
  linkUpdateSummary,
  parseLinkUpdates,
} from "../../public/js/link-updates.js";

const validEntry = Object.freeze({
  id: "sha256:abc",
  at: "2026-09-11T04:36:23Z",
  source: "resolved",
  code: "SPSF-58",
  slot: "standard.reupload",
  action: "resolved",
  provider: "streamtape",
});

test("summary states the bounded history instead of claiming an exact total", () => {
  assert.equal(
    linkUpdateSummary(5000, 3542, "catalog"),
    "最近 30 天已记录 5,000 条（最多保留 5,000 条），当前筛选 3,542 条，不显示真实链接地址。",
  );
});

test("source filter separates spreadsheet imports from resolved links", () => {
  const catalog = { ...validEntry, id: "sha256:catalog", source: "catalog", action: "updated" };
  assert.deepEqual(filterLinkUpdates([validEntry, catalog], "catalog"), [catalog]);
  assert.deepEqual(filterLinkUpdates([validEntry, catalog], "resolved"), [validEntry]);
  assert.equal(filterLinkUpdates([validEntry, catalog], "all").length, 2);
});

test("parser accepts the public no-URL changelog contract", () => {
  const parsed = parseLinkUpdates({
    schemaVersion: 1,
    generatedAt: "2026-09-11T04:36:23Z",
    entries: [validEntry],
  });

  assert.equal(parsed.entries.length, 1);
  assert.deepEqual(parsed.entries[0], validEntry);
});

test("parser fails closed when an entry contains a URL field", () => {
  assert.throws(
    () => parseLinkUpdates({
      schemaVersion: 1,
      generatedAt: "2026-09-11T04:36:23Z",
      entries: [{ ...validEntry, finalUrl: "https://example.com/private" }],
    }),
    /invalid link update entry/u,
  );
});

test("grouping uses Beijing calendar dates and Chinese labels", () => {
  const groups = groupLinkUpdates([
    validEntry,
    {
      ...validEntry,
      id: "sha256:def",
      at: "2026-09-10T16:30:00Z",
      source: "catalog",
      action: "updated",
      provider: "reupload",
    },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].date, "2026-09-11");
  assert.equal(groups[0].items[0].sourceLabel, "直达解析");
  assert.equal(groups[0].items[1].actionLabel, "链接已更新");
});
