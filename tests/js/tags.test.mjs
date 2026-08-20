import assert from "node:assert/strict";
import test from "node:test";

import { createTagIndex, filterVideosByTags } from "../../public/js/tags.js";


const tags = [
  { id: 6, group: "genre", nameJa: "陰落", nameZh: "沦陷", count: 2 },
  { id: 25, group: "genre", nameJa: "黒髪", nameZh: "黑发", count: 1 },
  { id: 2342, group: "character", nameJa: "戦隊ピンク", nameZh: "战队粉色队员", count: 1 },
];

const videos = [
  { code: "SPSF-1", releaseDate: "2026-01-01", tagIds: [6, 25] },
  { code: "SPSF-2", releaseDate: "2026-02-01", tagIds: [6, 2342] },
  { code: "SPSF-3", releaseDate: "2026-03-01", tagIds: [] },
];

test("tag index searches Chinese and Japanese names and groups tags", () => {
  const index = createTagIndex(tags);

  assert.deepEqual(index.search("沦陷").map((tag) => tag.id), [6]);
  assert.deepEqual(index.search("戦隊").map((tag) => tag.id), [2342]);
  assert.deepEqual(index.getGroup("genre").map((tag) => tag.id), [6, 25]);
  assert.equal(index.get(25).nameZh, "黑发");
});

test("tag filters support all, any, and exclusion without mutating videos", () => {
  assert.deepEqual(
    filterVideosByTags(videos, { include: [6, 25], match: "all" }).map(
      (video) => video.code,
    ),
    ["SPSF-1"],
  );
  assert.deepEqual(
    filterVideosByTags(videos, {
      include: [25, 2342],
      exclude: [6],
      match: "any",
    }),
    [],
  );
  assert.deepEqual(videos[0].tagIds, [6, 25]);
});

test("invalid tag records and filter ids are ignored deterministically", () => {
  const index = createTagIndex([
    ...tags,
    { id: 6, group: "genre", nameJa: "duplicate", nameZh: "duplicate" },
    { id: 0, group: "genre", nameJa: "bad", nameZh: "bad" },
  ]);

  assert.equal(index.getAll().length, 3);
  assert.deepEqual(
    filterVideosByTags(videos, { include: ["6", 0, 6], match: "all" }).map(
      (video) => video.code,
    ),
    ["SPSF-1", "SPSF-2"],
  );
});
