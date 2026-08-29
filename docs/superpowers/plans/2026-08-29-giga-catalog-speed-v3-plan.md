# GIGA Catalog Speed V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monolithic first-view catalog load with a validated 24-video bootstrap, generation-scoped lazy artifacts, safe IndexedDB reuse, and bounded result rendering while preserving the public catalog and every synchronization/release gate.

**Architecture:** The complete `catalog.json` remains canonical. Python deterministically derives one mutable bootstrap plus immutable generation-scoped search, tag, and per-series artifacts; the browser renders cached or network bootstrap data first, then fetches only the selected series or requested search/tag payload. Existing full/core/tag artifacts remain available for compatibility and rollback.

**Tech Stack:** Python 3.11 standard library, JavaScript ES modules, IndexedDB, static HTML/CSS, Node.js 24 test runner, GitHub Actions, GitHub Pages.

**Spec:** `docs/superpowers/specs/2026-08-29-giga-catalog-speed-v3-design.md`

## Global Constraints

- Keep `public/data/catalog.json` as the complete canonical public artifact.
- Keep generating `catalog-core.json` and `catalog-tags.json` during the transition.
- Keep the current GitHub Pages URL and the 11:30 Asia/Shanghai incremental schedule.
- Do not add a service worker, hosted API, database, paid host, or runtime dependency.
- Do not change playback classification or resolve, follow, proxy, or bypass short links.
- Store only validated public generated artifacts in the V3 IndexedDB database; never mix it with favorites or UI preferences.
- Runtime files must repeat and validate the same schema version, generation ID, and `generatedAt`.
- Any failed generation or browser refresh must leave the previous valid generation usable.
- Initial view and each progressive increment contain 24 real video cards.
- Use TDD for every implementation task and commit each independently green task.

## File map

- `src/giga_catalog/runtime_catalog.py`: retain the V1 core/tag split and add deterministic V3 bundle construction and validation.
- `scripts/sync_official_tags.py`: publish V1 and V3 files in one recoverable file transaction and prune only proven stale generation directories after commit.
- `.github/workflows/refresh-catalog.yml`: stage the stable bootstrap and generation tree through a path-limited allowlist.
- `src/giga_catalog/release.py`, `scripts/release.py`: continue hashing every public file and require the V3 bootstrap in release verification.
- `public/js/runtime-catalog.js`: validate V3 payloads and expose the generation-bound in-memory store.
- `public/js/catalog-cache.js`: own the V3 IndexedDB database and its two-generation retention policy.
- `public/js/runtime-loader.js`: coordinate cached bootstrap, background revalidation, lazy deduplicated artifact fetches, and aborts.
- `public/js/app.js`: connect the loader/store to existing views and preserve UI behavior.
- `public/js/render.js`: render 24-item progressive windows and the load-more status/control.
- `public/index.html`, `public/css/style.css`, `netlify.toml`: point preload/copy/styles/cache headers at V3 without changing the visual identity.
- `tests/python/test_runtime_catalog.py`, `tests/python/test_sync_official_tags.py`, `tests/python/test_deployment_config.py`, `tests/python/test_release.py`: deterministic generation, atomic publication, workflow, and release gates.
- `tests/js/runtime-catalog.test.mjs`, `tests/js/catalog-cache.test.mjs`, `tests/js/runtime-loader.test.mjs`, `tests/js/catalog.test.mjs`, `tests/js/app.test.mjs`: validation, cache, lazy loading, rendering, and compatibility.

---

### Task 1: Deterministic V3 runtime bundle

**Files:**
- Modify: `src/giga_catalog/runtime_catalog.py`
- Modify: `tests/python/test_runtime_catalog.py`

**Interfaces:**
- Consumes: one already validated canonical catalog mapping.
- Produces: `RuntimeV3Bundle(generation: str, bootstrap: dict, files: tuple[tuple[str, dict], ...])` and `build_runtime_v3(catalog, recent_limit=24) -> RuntimeV3Bundle`.
- Keeps: `build_runtime_catalogs(catalog) -> tuple[dict, dict]` unchanged for V1 compatibility.

- [ ] **Step 1: Write failing shape, determinism, and coverage tests**

Add tests that build a two-series catalog in opposite input orders and assert exact canonical output:

```python
from src.giga_catalog.runtime_catalog import build_runtime_v3


def test_v3_builds_bootstrap_search_tags_and_one_shard_per_series(self):
    bundle = build_runtime_v3(self._catalog())
    self.assertRegex(bundle.generation, r"^[0-9a-f]{64}$")
    self.assertEqual(bundle.bootstrap["schemaVersion"], 3)
    self.assertEqual(bundle.bootstrap["generation"], bundle.generation)
    self.assertEqual(len(bundle.bootstrap["recentVideos"]), 2)
    self.assertEqual(
        [item["code"] for item in bundle.bootstrap["series"]],
        ["NEWS", "SPSF"],
    )
    paths = [path for path, _ in bundle.files]
    prefix = f"runtime/g/{bundle.generation}/"
    self.assertEqual(
        paths,
        [
            prefix + "search.json",
            prefix + "tags.json",
            prefix + "series/news.json",
            prefix + "series/spsf.json",
        ],
    )
```

Add separate tests that assert:

```python
self.assertEqual(build_runtime_v3(first), build_runtime_v3(second))
self.assertEqual(all_shard_codes, canonical_codes)
self.assertEqual(len(all_shard_codes), len(set(all_shard_codes)))
self.assertLess(len(compact(bundle.bootstrap)), 250 * 1024)
```

Add rejection cases for duplicate canonical series codes, unsafe shard codes such as `../SPSF`, duplicate video codes across shards, bad counts, and a non-positive `recent_limit`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
py -m unittest tests.python.test_runtime_catalog.RuntimeCatalogTests -v
```

Expected: import failure for `build_runtime_v3`.

- [ ] **Step 3: Add the bundle type and deterministic builder**

Implement these public definitions:

```python
from dataclasses import dataclass
import hashlib
import json


@dataclass(frozen=True)
class RuntimeV3Bundle:
    generation: str
    bootstrap: dict
    files: tuple[tuple[str, dict], ...]


def build_runtime_v3(
    catalog: Mapping[str, object], *, recent_limit: int = 24
) -> RuntimeV3Bundle:
    if isinstance(recent_limit, bool) or not isinstance(recent_limit, int) or recent_limit <= 0:
        raise ValueError("recent_limit must be a positive integer")
    templates = _build_v3_templates(catalog, recent_limit=recent_limit)
    identity = hashlib.sha256(_compact_bytes(templates)).hexdigest()
    bootstrap, files = _bind_generation(templates, identity)
    _validate_runtime_v3(catalog, bootstrap, files)
    return RuntimeV3Bundle(identity, bootstrap, tuple(files))
```

Use `_core_video(video)` to remove the existing four tag/provenance fields without mutating the source. Sort series by `(latestReleaseDate descending, code ascending)`, videos within shards by `(number, code)`, and recent videos by `(releaseDate descending, code ascending)`. Search payload records must contain complete core video records plus their canonical `series` so existing cards, link badges, favorites, and dialogs can be reconstructed after lazy load.

Define payload shapes exactly as:

```python
bootstrap = {
    "schemaVersion": 3,
    "generation": identity,
    "generatedAt": catalog["generatedAt"],
    "totals": copy.deepcopy(catalog["totals"]),
    "refresh": copy.deepcopy(catalog["refresh"]),
    "resources": copy.deepcopy(catalog.get("resources", {})),
    "artifacts": {
        "search": f"runtime/g/{identity}/search.json",
        "tags": f"runtime/g/{identity}/tags.json",
    },
    "recentVideos": recent_videos,
    "series": series_summaries,
}
```

Each series summary includes `code`, `count`, `firstReleaseDate`, `latestReleaseDate`, optional public `links`, and `artifact`. Each child payload includes `schemaVersion`, `generation`, and `generatedAt`; search uses `videos`, tags reuses normalized `tags` plus `[code, tagIds]` assignments, and a shard uses one `series` object.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run:

```powershell
py -m unittest tests.python.test_runtime_catalog.RuntimeCatalogTests -v
git diff --check
```

Expected: all runtime catalog tests pass and no whitespace errors.

- [ ] **Step 5: Commit the runtime bundle**

```powershell
git add src/giga_catalog/runtime_catalog.py tests/python/test_runtime_catalog.py
git commit -m "feat: build sharded runtime catalog"
```

---

### Task 2: Atomic V3 publication and scheduled-workflow coverage

**Files:**
- Modify: `scripts/sync_official_tags.py`
- Create: `scripts/build_runtime_catalog.py`
- Modify: `.github/workflows/refresh-catalog.yml`
- Modify: `tests/python/test_sync_official_tags.py`
- Modify: `tests/python/test_deployment_config.py`

**Interfaces:**
- Consumes: `build_runtime_catalogs(catalog)` and `build_runtime_v3(catalog)` from Task 1.
- Produces: `build_publish_targets(...)` entries for the stable bootstrap and every current generation file, with bootstrap last; `prune_runtime_generations(runtime_root, keep_generations)` restricted to validated 64-hex directory names.
- Produces: a network-free `scripts/build_runtime_catalog.py` command that validates the checked-in canonical catalog and republishes only its derived V1/V3 runtime artifacts.

- [ ] **Step 1: Write failing publication and allowlist tests**

Extend option-default tests to require:

```python
self.assertEqual(options.runtime_bootstrap.name, "catalog-bootstrap.json")
self.assertEqual(options.runtime_root.parts[-2:], ("data", "runtime"))
```

Add a publication test:

```python
targets = build_publish_targets(options, raw_products, raw_tags, catalog)
paths = [path for path, _ in targets]
self.assertEqual(paths[-1], options.runtime_bootstrap)
self.assertIn(options.runtime_root / "g" / generation / "search.json", paths)
self.assertIn(options.runtime_root / "g" / generation / "series" / "spsf.json", paths)
```

Add a pruning test with directories named `<current hash>`, `<previous hash>`, `<old hash>`, `notes`, and `.staging`; assert only `<old hash>` is removed and non-generation paths are untouched.

Extend the workflow test to require the exact staged paths:

```python
self.assertIn("public/data/catalog-bootstrap.json", stage_command)
self.assertIn("public/data/runtime", stage_command)
self.assertNotIn("git add .", stage_command)
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
py -m unittest tests.python.test_sync_official_tags tests.python.test_deployment_config -v
```

Expected: missing V3 options/targets and workflow paths.

- [ ] **Step 3: Publish generated files in the existing recoverable transaction**

Add CLI defaults:

```python
parser.add_argument(
    "--runtime-bootstrap",
    type=Path,
    default=ROOT / "public" / "data" / "catalog-bootstrap.json",
)
parser.add_argument(
    "--runtime-root",
    type=Path,
    default=ROOT / "public" / "data" / "runtime",
)
```

Build target entries from the V3 bundle:

```python
runtime_core, runtime_tags = build_runtime_catalogs(catalog)
v3 = build_runtime_v3(catalog)
v3_targets = [
    (options.runtime_root.parent / relative, _json_bytes(payload))
    for relative, payload in v3.files
]
return [
    (options.products, _json_bytes(raw_products)),
    (options.tags, _json_bytes(raw_tags)),
    (options.catalog, serialize_catalog(catalog)),
    (options.runtime_core, _json_bytes(runtime_core)),
    (options.runtime_tags, _json_bytes(runtime_tags)),
    *v3_targets,
    (options.runtime_bootstrap, _json_bytes(v3.bootstrap)),
]
```

After `_commit_transaction` succeeds, read the new bootstrap generation and the previous bootstrap generation captured before commit, then call:

```python
prune_runtime_generations(
    options.runtime_root,
    keep_generations={new_generation, previous_generation},
)
```

The pruning function must resolve every candidate, require `^[0-9a-f]{64}$`, require its parent to resolve to `<runtime_root>/g`, and use `shutil.rmtree` only on that verified directory. Pruning failure is reported in the command result but does not reinterpret a committed catalog as failed.

Create `scripts/build_runtime_catalog.py` with this exact command boundary:

```python
def build_runtime_from_catalog(
    catalog_path: Path,
    runtime_core: Path,
    runtime_tags: Path,
    runtime_bootstrap: Path,
    runtime_root: Path,
) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    errors = validate_catalog(catalog)
    if errors:
        raise RuntimeError("catalog validation failed:\n" + "\n".join(sorted(errors)))
    return publish_runtime_artifacts(
        catalog,
        runtime_core=runtime_core,
        runtime_tags=runtime_tags,
        runtime_bootstrap=runtime_bootstrap,
        runtime_root=runtime_root,
    )
```

The CLI defaults to the checked-in `public/data/catalog.json` and derived paths, performs no network request, prints generation/counts as compact JSON, and exits nonzero without changing files when canonical validation or V3 staging fails. Add `tests/python/test_runtime_catalog.py` cases that run it twice, assert byte-identical output, and inject a replacement failure to prove the old bootstrap and generation survive.

Update the workflow staging command to add only:

```bash
git add -- data/raw data/state data/update-summary.json \
  public/data/catalog.json public/data/catalog-core.json \
  public/data/catalog-tags.json public/data/catalog-bootstrap.json \
  public/data/featured-covers.json public/data/runtime \
  public/media/featured-covers
```

- [ ] **Step 4: Run focused tests and a no-write real build check**

Run:

```powershell
py -m unittest tests.python.test_sync_official_tags tests.python.test_deployment_config -v
py scripts/refresh.py --mode links-only --dry-run
git diff --check
```

Expected: tests pass; dry-run reports a valid catalog and writes no V3 files.

- [ ] **Step 5: Commit publication wiring**

```powershell
git add scripts/sync_official_tags.py scripts/build_runtime_catalog.py .github/workflows/refresh-catalog.yml tests/python/test_sync_official_tags.py tests/python/test_runtime_catalog.py tests/python/test_deployment_config.py
git commit -m "feat: publish runtime catalog generations"
```

---

### Task 3: Strict browser payload validation and generation-bound model

**Files:**
- Create: `public/js/runtime-catalog.js`
- Create: `tests/js/runtime-catalog.test.mjs`
- Modify: `public/js/catalog.js`
- Modify: `tests/js/catalog.test.mjs`

**Interfaces:**
- Consumes: the Task 1 payload shapes.
- Produces: `parseBootstrap(value)`, `parseSearchPayload(value, bootstrap)`, `parseTagPayload(value, bootstrap)`, `parseSeriesPayload(value, bootstrap, code)`, and `createRuntimeCatalogStore(bootstrap)`.
- Store methods: `metadata`, `getSeriesSummaries()`, `getRecentVideos()`, `getSeries(code)`, `getVideo(code)`, `search(query)`, `getTag(id)`, `getTags(group)`, `filterByTags(options)`, `installSeries(payload)`, `installSearch(payload)`, `installTags(payload)`.

- [ ] **Step 1: Write failing validation and store tests**

Create tests with one valid fixture and mutations for each boundary:

```javascript
const bootstrap = parseBootstrap(validBootstrap());
const store = createRuntimeCatalogStore(bootstrap);
assert.equal(store.getRecentVideos().length, 2);
assert.equal(store.getSeries("SPSF"), null);

store.installSeries(parseSeriesPayload(validSeries(), bootstrap, "SPSF"));
assert.equal(store.getSeries("spsf").videos[0].code, "SPSF-1");

store.installSearch(parseSearchPayload(validSearch(), bootstrap));
assert.deepEqual(store.search("女战士").map((video) => video.code), ["SPSF-1"]);
```

Assert throws for wrong schema, mixed generation, different `generatedAt`, undeclared artifact path, wrong shard code, duplicate code, unknown series, unsafe URL/path, bad totals, and a tag assignment referencing an unknown search video. Assert installation clones/freezes inputs and cannot mutate caller fixtures.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```powershell
node --test tests/js/runtime-catalog.test.mjs tests/js/catalog.test.mjs
```

Expected: module-not-found failure for `runtime-catalog.js`.

- [ ] **Step 3: Implement validators and the runtime store**

Use one public error class that never embeds payload content:

```javascript
export class RuntimeCatalogError extends Error {
  constructor(kind) {
    super(`运行目录数据无效（${kind}）`);
    this.name = "RuntimeCatalogError";
    this.kind = kind;
  }
}
```

Validation must accept only own plain-object fields, canonical codes, exact relative generation paths, a 64-character lowercase generation, equal timestamps, arrays with unique members, finite non-boolean integers, and existing safe public HTTP(S) URLs where the legacy model already accepts them. Return cloned, deeply frozen values.

The store uses private maps internally and exposes synchronous reads. `installSeries`, `installSearch`, and `installTags` validate before replacing any map. `getVideo` checks loaded series, recent records, then the installed search map. Tag installation reuses the current `createTagIndex`/`filterVideosByTags` behavior rather than duplicating tag rules.

Export the existing `normalizeText(value)` and `normalizeVideoCode(value)` helpers from `catalog.js` and import those exact functions in `runtime-catalog.js`; do not duplicate their canonicalization rules. Retain `createCatalogModel` so old V1 tests and emergency rollback remain valid.

- [ ] **Step 4: Run focused tests and confirm GREEN**

```powershell
node --test tests/js/runtime-catalog.test.mjs tests/js/catalog.test.mjs
git diff --check
```

Expected: all focused JavaScript tests pass.

- [ ] **Step 5: Commit the browser model**

```powershell
git add public/js/runtime-catalog.js public/js/catalog.js tests/js/runtime-catalog.test.mjs tests/js/catalog.test.mjs
git commit -m "feat: validate sharded browser catalog"
```

---

### Task 4: IndexedDB cache and lazy artifact loader

**Files:**
- Create: `public/js/catalog-cache.js`
- Create: `public/js/runtime-loader.js`
- Create: `tests/js/catalog-cache.test.mjs`
- Create: `tests/js/runtime-loader.test.mjs`

**Interfaces:**
- Consumes: Task 3 parser functions and an injected `fetcher`/IndexedDB implementation.
- Produces: `openCatalogCache(indexedDB) -> Promise<CatalogCache>` and `createRuntimeLoader({ fetcher, cache, bootstrapUrl })`, where `cache` may be a cache object or its opening promise.
- Cache methods: `getLatestBootstrap()`, `getArtifact(generation, path)`, `putBootstrap(bootstrap)`, `putArtifact(generation, path, payload)`, `prune(keep=2)`, `close()`.
- Loader methods: `start({ signal, onCached, onFresh })`, `ensureSeries(code, { signal })`, `ensureSearch({ signal })`, `ensureTags({ signal })`, `setBootstrap(bootstrap)`.

- [ ] **Step 1: Write failing cache tests with a deterministic fake IndexedDB adapter**

Test the public cache contract rather than browser timing:

```javascript
const cache = await openCatalogCache(fakeIndexedDB);
await cache.putBootstrap(first);
await cache.putArtifact(first.generation, first.artifacts.search, search);
assert.deepEqual(await cache.getLatestBootstrap(), first);
assert.deepEqual(
  await cache.getArtifact(first.generation, first.artifacts.search),
  search,
);
await cache.putBootstrap(second);
await cache.putBootstrap(third);
await cache.prune(2);
assert.equal(await cache.getArtifact(first.generation, first.artifacts.search), null);
```

Cover unavailable/throwing IndexedDB with a no-op cache whose methods resolve safely. Assert pruning never touches the `giga_catalog_favorites_v1` or `giga_catalog_ui_v1` localStorage keys by keeping the module free of localStorage access.

- [ ] **Step 2: Write failing loader tests**

Use deferred fetch promises and a memory cache to prove:

```javascript
const seen = [];
await loader.start({
  onCached(value) { seen.push(["cache", value.generation]); },
  onFresh(value) { seen.push(["network", value.generation]); },
});
assert.deepEqual(seen, [["cache", OLD], ["network", NEW]]);
```

Also assert same-generation network data does not cause a second UI activation, invalid network data does not replace cache, two concurrent `ensureSeries("SPSF")` calls share one fetch, an abort does not poison the shared cache, and a generation change aborts/invalidates every old in-flight artifact.

- [ ] **Step 3: Run the new tests and confirm RED**

```powershell
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
```

Expected: module-not-found failures for both new modules.

- [ ] **Step 4: Implement IndexedDB ownership**

Use database `giga_catalog_runtime_v3`, version `1`, with stores:

```javascript
const BOOTSTRAPS = "bootstraps"; // keyPath: generation
const ARTIFACTS = "artifacts";   // keyPath: key = `${generation}:${path}`
const META = "meta";             // latestGeneration and ordered generations
```

Write a transaction helper that resolves on `transaction.oncomplete` and rejects on `onabort`/`onerror`. Clone values on both write and read. `putBootstrap` must call `parseBootstrap` before opening a write transaction. `putArtifact` accepts only a path declared by that generation’s stored bootstrap. Failed transactions leave prior records unchanged.

When `indexedDB` is missing or opening fails, return a memory-safe no-op implementation:

```javascript
export function unavailableCatalogCache() {
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
```

- [ ] **Step 5: Implement cache-first/background-network coordination**

`start` begins the network fetch before awaiting the cache-opening promise, then performs the cache read. It calls `onCached` once for a valid cache, then calls `onFresh` only for a different valid network generation. The bootstrap request uses:

```javascript
fetch(new URL("../data/catalog-bootstrap.json", import.meta.url), {
  headers: { Accept: "application/json" },
  cache: "no-cache",
  signal,
})
```

Artifact requests use the exact relative path from the validated bootstrap, never a caller-built URL. Check IndexedDB first, then fetch, parse against the active bootstrap, store, and return. Keep one promise per `generation:path`; delete rejected promises so retry works.

- [ ] **Step 6: Run focused tests and confirm GREEN**

```powershell
node --test tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
git diff --check
```

Expected: cache and loader suites pass.

- [ ] **Step 7: Commit cache and loader**

```powershell
git add public/js/catalog-cache.js public/js/runtime-loader.js tests/js/catalog-cache.test.mjs tests/js/runtime-loader.test.mjs
git commit -m "feat: cache and lazily load catalog shards"
```

---

### Task 5: Twenty-four-item latest view and progressive rendering

**Files:**
- Modify: `public/js/render.js`
- Modify: `public/js/app.js`
- Modify: `public/index.html`
- Modify: `tests/js/catalog.test.mjs`
- Modify: `tests/js/app.test.mjs`

**Interfaces:**
- Consumes: Task 3 store and Task 4 loader.
- Produces: `resolveProgressiveWindow(items, visibleCount=24)`, 24-card initial/latest/series/search/tag/favorites views, and `data-action="load-more"` with `data-context` and `data-next-visible`.

- [ ] **Step 1: Write failing progressive-render tests**

Replace the old `>250 => 100` expectation with explicit 24-item behavior:

```javascript
const first = renderSearchResults(container, videos(60), { visibleCount: 24 });
assert.equal(first.rendered, 24);
assert.equal(first.total, 60);
assert.equal(first.nextVisible, 48);
assert.equal(container.querySelectorAll(".video-card").length, 24);

const second = renderSearchResults(container, videos(60), { visibleCount: 48 });
assert.equal(second.rendered, 48);
assert.equal(second.nextVisible, 60);
```

Add app helper tests for `increaseVisibleCount(current, total, 24)` returning `48`, `60`, and `60`, plus reset tests when view/query/series changes.

Add a deterministic upcoming-label test by passing `asOfDate: "2026-08-29"`: a card dated `2026-09-11` contains `<span class="video-release-state">预告</span>`, while a card dated `2026-08-28` does not.

- [ ] **Step 2: Write failing startup/lazy-view tests**

Test exported orchestration helpers with a fake loader/store:

```javascript
await activateSeries({ code: "SPSF", loader, store, render });
assert.deepEqual(loader.calls, ["series:SPSF"]);
assert.equal(render.calls.at(-1).videos.length, 24);

await activateSearch({ query: "SPSF-61", loader, store, render });
assert.deepEqual(loader.calls, ["series:SPSF", "search"]);
assert.deepEqual(render.calls.at(-1).videos.map((v) => v.code), ["SPSF-61"]);
```

Assert ordinary startup never calls `ensureSearch`, `ensureTags`, or `ensureSeries`; favorites calls `ensureSearch`; tag view calls `ensureSearch` and `ensureTags`; opening a detail from recent data does not fetch a shard, while an unloaded search result fetches its declared shard before opening.

- [ ] **Step 3: Run focused tests and confirm RED**

```powershell
node --test tests/js/catalog.test.mjs tests/js/app.test.mjs
```

Expected: progressive-window/orchestration assertions fail against the current monolithic series startup.

- [ ] **Step 4: Implement progressive rendering**

Set one exported constant and pure helper:

```javascript
export const RESULT_WINDOW_SIZE = 24;

export function resolveProgressiveWindow(items, visibleCount = RESULT_WINDOW_SIZE) {
  const total = items.length;
  const requested = Number.isInteger(visibleCount) ? visibleCount : RESULT_WINDOW_SIZE;
  const end = Math.min(total, Math.max(RESULT_WINDOW_SIZE, requested));
  return {
    items: items.slice(0, end),
    rendered: end,
    total,
    hasMore: end < total,
    nextVisible: Math.min(total, end + RESULT_WINDOW_SIZE),
  };
}
```

Use this window in `mountSeries` and `renderSearchResults`. Replace previous/next pagination with one status plus “加载更多” button. After a load-more rerender, focus the replacement load-more button if one remains; otherwise focus the status element with temporary `tabindex="-1"`. Announce `已显示 X / Y 部` through `role="status"` and `aria-live="polite"`.

Pass the active bootstrap’s `generatedAt.slice(0, 10)` to rendering as `asOfDate`. `renderVideoCard` adds the text-only “预告” badge when `video.releaseDate > asOfDate`; it performs no wall-clock comparison, so cached and fresh generations render deterministically.

- [ ] **Step 5: Replace monolithic startup with V3 activation**

Import `openCatalogCache`, `createRuntimeLoader`, and `createRuntimeCatalogStore`. Replace `loadCatalog()` with `loadRuntimeCatalog()` that:

```javascript
const cache = openCatalogCache(globalThis.indexedDB);
const loader = createRuntimeLoader({ fetcher: globalThis.fetch, cache });
await loader.start({
  signal: state.fetchController.signal,
  onCached: (bootstrap) => activateBootstrap(bootstrap, { cached: true }),
  onFresh: (bootstrap) => activateBootstrap(bootstrap, { cached: false }),
});
```

`activateBootstrap` creates a new Task 3 store, calls `loader.setBootstrap`, resets only generation-dependent starts, retains a valid selected series, updates summaries/navigation, and renders `store.getRecentVideos()` as “最新目录”. The visible state includes separate counters for `recent`, each series, search, tag results, and each favorite state, all initialized to 24.

Series selection awaits `loader.ensureSeries(code)`, installs it, then renders. Search focus may prefetch search after the bootstrap is interactive, but startup itself must not. Debounced search awaits `ensureSearch`; tag-aware search/view additionally awaits `ensureTags`. Disable the affected control and show a bounded loading state while a resource is outstanding; leave current content visible on failure and expose retry.

Change the initial copy in `index.html` and loading panel from “完整数据在内存中建立索引” to “先载入最新目录，其余内容按需读取”. Preload `data/catalog-bootstrap.json`; remove the `catalog-core.json` preload. Do not preload search, tags, or shards.

Add modulepreload entries for `js/runtime-catalog.js`, `js/catalog-cache.js`, and `js/runtime-loader.js` because they are on the startup module graph; preserve the existing modulepreloads and assert each startup module appears exactly once.

- [ ] **Step 6: Run focused tests and confirm GREEN**

```powershell
node --test tests/js/catalog.test.mjs tests/js/app.test.mjs tests/js/runtime-catalog.test.mjs tests/js/runtime-loader.test.mjs
git diff --check
```

Expected: latest view, lazy flow, progressive rendering, keyboard focus, and legacy helpers pass.

- [ ] **Step 7: Commit the V3 UI flow**

```powershell
git add public/js/render.js public/js/app.js public/index.html tests/js/catalog.test.mjs tests/js/app.test.mjs
git commit -m "feat: render fast progressive catalog views"
```

---

### Task 6: Cache headers, release gates, responsive states, and compatibility

**Files:**
- Modify: `public/css/style.css`
- Modify: `netlify.toml`
- Modify: `src/giga_catalog/release.py`
- Modify: `tests/python/test_deployment_config.py`
- Modify: `tests/python/test_release.py`
- Modify: `tests/js/app.test.mjs`

**Interfaces:**
- Consumes: the stable bootstrap and generation paths from Tasks 1–2 and UI states from Task 5.
- Produces: tested cache policies, required release-manifest entry, and responsive cached/offline/load-more presentation.

- [ ] **Step 1: Write failing deployment and release tests**

Require exact Netlify policies:

```python
self.assertEqual(
    self._header_values("/data/catalog-bootstrap.json")["Cache-Control"],
    "public, max-age=300, must-revalidate",
)
self.assertEqual(
    self._header_values("/data/runtime/g/*")["Cache-Control"],
    "public, max-age=31536000, immutable",
)
```

Extend release required-file tests with `data/catalog-bootstrap.json`. Build a manifest fixture containing one search file and one series shard, then prove missing, altered, and redirecting shards fail the exact-file verification.

Add static frontend assertions that `index.html` preloads bootstrap once, contains no `catalog-core.json`, `catalog-tags.json`, `search.json`, or series preload, and does not register a service worker.

- [ ] **Step 2: Run focused tests and confirm RED**

```powershell
py -m unittest tests.python.test_deployment_config tests.python.test_release -v
node --test tests/js/app.test.mjs
```

Expected: missing V3 cache/required-file assertions fail.

- [ ] **Step 3: Add exact headers and release requirements**

Add:

```toml
[[headers]]
  for = "/data/catalog-bootstrap.json"
  [headers.values]
    Cache-Control = "public, max-age=300, must-revalidate"

[[headers]]
  for = "/data/runtime/g/*"
  [headers.values]
    Cache-Control = "public, max-age=31536000, immutable"
```

Add `data/catalog-bootstrap.json` to the release module’s required public files. Keep full-file hashing unchanged so every generation file is covered automatically.

- [ ] **Step 4: Style only required states and verify accessibility behavior**

Add styles for `.catalog-cache-state`, `.load-more`, and `.progressive-status` using existing color, radius, and focus tokens. At widths up to 767 px, make the load-more button full width with at least a 44 px hit target. Under `prefers-reduced-motion: reduce`, do not add transitions or smooth scrolling. Do not restyle cards, branding, or navigation in this task.

Extend JS tests so cached state announces “已显示缓存，正在检查更新”, offline state announces “离线使用已缓存目录”, load-more exposes an accessible name containing the next item count, and error text contains no source URL.

- [ ] **Step 5: Run focused and complete suites**

```powershell
py -m unittest tests.python.test_deployment_config tests.python.test_release -v
node --test tests/js/app.test.mjs
py -m unittest discover -s tests/python -p "test_*.py" -v
node --test
git diff --check
```

Expected: Python and JavaScript suites pass with only the existing Windows symlink privilege skip allowed.

- [ ] **Step 6: Commit release and presentation gates**

```powershell
git add public/css/style.css netlify.toml src/giga_catalog/release.py tests/python/test_deployment_config.py tests/python/test_release.py tests/js/app.test.mjs
git commit -m "test: gate runtime catalog release"
```

---

### Task 7: Generate artifacts, run real-browser performance gates, and prepare rollout

**Files:**
- Generate: `public/data/catalog-bootstrap.json`
- Generate: `public/data/runtime/g/<generation>/search.json`
- Generate: `public/data/runtime/g/<generation>/tags.json`
- Generate: `public/data/runtime/g/<generation>/series/*.json`
- Modify: `docs/superpowers/reports/2026-08-29-giga-catalog-speed-v3-release.md`

**Interfaces:**
- Consumes: all prior tasks and current canonical catalog.
- Produces: committed generated V3 artifacts and an evidence-based release/rollback report; no production claim before live verification.

- [ ] **Step 1: Generate V3 from the current canonical catalog without network mutation**

Run the network-free builder created in Task 2:

```powershell
py scripts/build_runtime_catalog.py
```

Expected: one bootstrap and one generation directory are created; rerunning produces byte-identical files and no Git diff.

- [ ] **Step 2: Verify artifact size, coverage, and public-data safety**

Run:

```powershell
py -c "import json,pathlib; p=pathlib.Path('public/data/catalog-bootstrap.json'); b=p.read_bytes(); v=json.loads(b); print(len(b),len(v['recentVideos']),v['generation'])"
rg -n -i "netlify.{0,20}(token|key)|owner.{0,20}(secret|token)|authorization:|bearer |jwt|private[_-]?url" public/data public/js
py scripts/release.py prepare --source-commit "$(git rev-parse HEAD)"
```

On PowerShell, obtain the commit separately if command substitution is unavailable:

```powershell
$commit = git rev-parse HEAD
py scripts/release.py prepare --source-commit $commit
```

Expected: bootstrap is at most 256,000 bytes, contains exactly 24 recent records, static scan finds no secret, and release manifest preparation validates every local public file.

- [ ] **Step 3: Run complete code and refresh regression gates**

```powershell
py -m unittest discover -s tests/python -p "test_*.py" -v
node --test
py scripts/refresh.py --mode incremental --dry-run
py scripts/refresh.py --mode links-only --dry-run
git diff --check
```

Expected: all suites pass; both real-source dry-runs succeed and write nothing.

- [ ] **Step 4: Serve locally and measure the exact browser flows**

Start:

```powershell
py -m http.server 8000 --directory public
```

With Playwright CLI, inspect 320, 390, 768, and 1440 px. For each width, record console errors, failed requests, `scrollWidth - clientWidth`, initial card count, bootstrap transfer/decoded size, and whether search/tag/series artifacts were absent on initial navigation. At 390 px also:

1. search `SPSF-61` and confirm one result;
2. open SPSF and confirm only its shard was requested;
3. load the next window and confirm 48 cards plus retained focus;
4. reload after cache population with network delayed and confirm usable cached content appears within 500 ms;
5. serve a mismatched bootstrap fixture and confirm the valid cache remains;
6. emulate reduced motion and 200% zoom and confirm no horizontal overflow.

- [ ] **Step 5: Write the release report with measured, not inferred, results**

Create `docs/superpowers/reports/2026-08-29-giga-catalog-speed-v3-release.md` containing:

- branch and commit IDs;
- baseline versus five-run cold median and warm-cache time;
- artifact sizes and initial request list;
- Python/JavaScript/dry-run/release-verifier commands and results;
- responsive/accessibility/browser results;
- confirmation that the public URL, canonical catalog, schedules, links, playback policy, and private-data boundary are unchanged;
- rollback command sequence using `git revert <merge-commit>` followed by the normal deployment workflow;
- explicit statement that production is not yet verified until the default-branch deployment succeeds.

- [ ] **Step 6: Commit generated artifacts and release evidence**

```powershell
git add public/data/catalog-bootstrap.json public/data/runtime docs/superpowers/reports/2026-08-29-giga-catalog-speed-v3-release.md
git commit -m "chore: prepare catalog speed v3 release"
```

- [ ] **Step 7: Run verification-before-completion and finish the branch**

Invoke `superpowers:verification-before-completion`, rerun the complete commands from Steps 2–4, and inspect `git status --short`. Then invoke `superpowers:requesting-code-review`. Resolve only evidence-backed review findings with their own tests and commits. Finally invoke `superpowers:finishing-a-development-branch` to present the integration choices; do not merge or push to the default branch before that workflow’s decision point.

- [ ] **Step 8: Verify production only after an approved default-branch deployment**

After integration and GitHub Pages success, run:

```powershell
py scripts/release.py verify-production --url https://siwencifudalinzi.github.io/giga-catalog-cn/ --attempts 3 --timeout 30 --delay 5
```

Repeat the 390 px and 1440 px browser checks against production with a cache-busting query. Confirm 24 initial cards, no initial search/tag/shard requests, a successful `SPSF-61` search, no horizontal overflow, no console errors, and a matching release manifest. Append the live deployment ID/commit and results to the release report in a final documentation commit.
