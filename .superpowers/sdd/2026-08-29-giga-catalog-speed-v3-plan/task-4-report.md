# Task 4 report — IndexedDB cache and lazy artifact loader

Date: 2026-08-29

## Result

Implemented the Task 4 cache and loader contracts without changing `app.js`.

- `public/js/catalog-cache.js`
  - Owns IndexedDB database `giga_catalog_runtime_v3`, version 1.
  - Creates `bootstraps`, `artifacts`, and `meta` stores.
  - Validates bootstraps through the Task 3 `parseBootstrap` boundary before writes.
  - Enforces generation-declared artifact paths, clones values on reads/writes, atomically updates metadata, prunes old generations, and provides a safe no-op cache when IndexedDB is unavailable or fails.
  - Does not access `localStorage`.
- `public/js/runtime-loader.js`
  - Starts bootstrap network fetch before awaiting a cache-opening promise.
  - Activates valid cached data immediately, then activates only a different valid network generation.
  - Uses exact validated artifact paths and Task 3 parsers.
  - Deduplicates one in-flight request per `generation:path`.
  - Keeps caller aborts isolated from the shared request, while generation changes abort and reject old callers and prevent obsolete responses from being cached.
- `tests/js/catalog-cache.test.mjs`
- `tests/js/runtime-loader.test.mjs`

## TDD evidence

### RED

Before production modules existed:

```text
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
```

Both test files failed with `ERR_MODULE_NOT_FOUND` for their respective new modules.

### GREEN

Focused verification:

```text
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
```

Result: 9 tests passed, 0 failed.

The focused tests cover IndexedDB cloning/path ownership/pruning and no-op fallback; cache-first startup, same-generation suppression, invalid network preservation; exact-path deduplication; per-caller abort behavior; generation invalidation; and delayed-cache generation invalidation.

Full JavaScript suite:

```text
npm run test:js
```

Result: 91 tests passed, 0 failed.

`git diff --check` passed.

## Self-review

- Task 3 parsers remain the sole validation boundary for bootstrap and artifact payloads.
- Cache writes are cloned and IndexedDB transactions resolve only on `oncomplete`, rejecting on `onerror`/`onabort`.
- Network bootstrap failures do not overwrite a valid cache; storage failures are treated as an optimization failure after valid network data is accepted.
- Caller cancellation never aborts the deduplicated artifact request. Generation changes abort internal old-generation controllers and reject all old subscribers immediately.
- Late old-generation responses are checked before cache writes and are discarded.
- No application wiring was added, per Task 4 scope.

## Concerns

No outstanding test failures or known Task 4 blockers. `app.js` still requires a later task to consume these new interfaces.

## Review fix round 1 — cache artifact validation

Finding addressed: `putArtifact` previously checked only the declared path, allowing an arbitrary payload to overwrite a valid approved artifact.

### RED

Added focused coverage for malformed search, tag, and series overwrites, preservation of the existing valid records, rejection of undeclared paths, and cloning of valid writes. Before the fix:

```text
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
```

Result: 8 passed, 2 failed. Both failures were the expected `Missing expected rejection` assertions for undeclared/malformed artifact writes.

### GREEN

`putArtifact` now parses the stored generation bootstrap, dispatches by exact declared path to `parseSearchPayload`, `parseTagPayload`, or `parseSeriesPayload` with the declared series code, and opens the write transaction only after validation. Unknown paths reject before any write.

```text
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
```

Result: 10 passed, 0 failed.

```text
node --test
```

Result: 92 passed, 0 failed.

```text
git diff --check
```

Result: passed.

The fix was committed in the follow-up commit reported with this round.
