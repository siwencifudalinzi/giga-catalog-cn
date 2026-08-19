import assert from "node:assert/strict";
import test from "node:test";

import {
  FAVORITES_STORAGE_KEY,
  createFavoritesStore,
  getFavoriteVideos,
  loadFavorites,
} from "../../public/js/favorites.js";

class MemoryStorage {
  constructor(entries = {}) {
    this.entries = new Map(Object.entries(entries));
  }

  getItem(key) {
    return this.entries.has(key) ? this.entries.get(key) : null;
  }

  setItem(key, value) {
    this.entries.set(key, String(value));
  }
}

test("legacy favorite state objects load unchanged", () => {
  const legacy = {
    "SPSF-44": 0,
    "SPSF-45": 1,
    "ABGD-2": 2,
  };
  const storage = new MemoryStorage({
    [FAVORITES_STORAGE_KEY]: JSON.stringify(legacy),
  });

  assert.deepEqual(loadFavorites(storage), legacy);
});

test("invalid favorite storage recovers to an empty object", () => {
  for (const value of ["{broken", "null", "[]", '"text"']) {
    const storage = new MemoryStorage({
      [FAVORITES_STORAGE_KEY]: value,
    });

    assert.deepEqual(loadFavorites(storage), {}, value);
  }
});

test("favorite state cycles from none to want-to-watch to watched to none", () => {
  const storage = new MemoryStorage();
  const favorites = createFavoritesStore(storage);

  assert.equal(favorites.getState("SPSF-44"), 0);
  assert.equal(favorites.cycle("SPSF-44"), 1);
  assert.equal(favorites.cycle("SPSF-44"), 2);
  assert.equal(favorites.cycle("SPSF-44"), 0);
  assert.deepEqual(
    JSON.parse(storage.getItem(FAVORITES_STORAGE_KEY)),
    { "SPSF-44": 0 },
  );
});

test("favorite video generation ignores missing catalog entries", () => {
  const videos = new Map([
    ["SPSF-44", { code: "SPSF-44", title: "Available" }],
    ["ABGD-2", { code: "ABGD-2", title: "Watched" }],
  ]);
  const favorites = {
    "SPSF-44": 1,
    "MISSING-9": 1,
    "ABGD-2": 2,
    "IGNORED-1": 0,
  };

  assert.deepEqual(
    getFavoriteVideos(favorites, (code) => videos.get(code) ?? null),
    [
      { video: videos.get("SPSF-44"), state: 1 },
      { video: videos.get("ABGD-2"), state: 2 },
    ],
  );
});
