/** Accessing the storage property itself can throw in restricted browsers. */
export function getLocalStorage() {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}
