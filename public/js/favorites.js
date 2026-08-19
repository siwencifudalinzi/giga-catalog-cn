export const FAVORITES_STORAGE_KEY = "giga_favorites_v2";

function isFavoriteState(value) {
  return value === 0 || value === 1 || value === 2;
}

function isFavoriteRecord(value) {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      Object.entries(value).every(
        ([code, state]) => code.trim() && isFavoriteState(state),
      ),
  );
}

function normalizeCode(value) {
  return typeof value === "string"
    ? value.normalize("NFKC").trim().toUpperCase()
    : "";
}

export function loadFavorites(storage = globalThis.localStorage) {
  try {
    const parsed = JSON.parse(storage?.getItem(FAVORITES_STORAGE_KEY));
    return isFavoriteRecord(parsed) ? { ...parsed } : {};
  } catch {
    return {};
  }
}

export function createFavoritesStore(storage = globalThis.localStorage) {
  let state = loadFavorites(storage);

  function persist(next) {
    state = next;
    try {
      storage?.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(next));
    } catch {
      // localStorage can be unavailable in private or restricted contexts.
    }
  }

  return Object.freeze({
    getAll() {
      return { ...state };
    },

    getState(code) {
      const key = normalizeCode(code);
      return isFavoriteState(state[key]) ? state[key] : 0;
    },

    cycle(code) {
      const key = normalizeCode(code);
      if (!key) {
        return 0;
      }
      const nextState = (this.getState(key) + 1) % 3;
      persist({ ...state, [key]: nextState });
      return nextState;
    },

    getCount() {
      return Object.values(state).filter((value) => value === 1 || value === 2)
        .length;
    },
  });
}

export function getFavoriteVideos(favorites, getVideo) {
  if (!isFavoriteRecord(favorites) || typeof getVideo !== "function") {
    return [];
  }
  return Object.entries(favorites)
    .filter(([, state]) => state === 1 || state === 2)
    .sort(([leftCode, leftState], [rightCode, rightState]) => {
      return leftState - rightState || leftCode.localeCompare(rightCode);
    })
    .flatMap(([code, state]) => {
      const video = getVideo(code);
      return video ? [{ video, state }] : [];
    });
}
