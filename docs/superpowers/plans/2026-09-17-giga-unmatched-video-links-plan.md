# GIGA Unmatched Video Links Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish safe, unrecognized sheet links in a separate, lazy-loaded “无详情影片” view while keeping official catalog sync fail-closed and intact.

**Architecture:** Parse unknown rows at each successful sheet source, retain them in source-identity-keyed private snapshots, and atomically publish a minimal public aggregate plus URL-free freshness status. The browser validates that aggregate again and renders only codes and external links; it never converts those links into playable media.

**Tech Stack:** Python 3 stdlib and requests, existing `src/giga_catalog` parsers, Node's built-in test runner, plain ES modules/CSS, GitHub Actions and Pages.

**Spec:** `docs/superpowers/specs/2026-09-17-giga-unmatched-video-links-design.md`

## Global Constraints

- Do not alter official `catalog.json` schema, counts, link-slot conflict behavior, subtitle-only parsing, daily schedule, or player policy.
- Public item keys are exactly `code` and `links`; public manifest keys are exactly `schemaVersion`, `generatedAt`, `entries`. No title, cover, tag, source, date or player.
- Candidate URLs must be public HTTPS without credentials, fragment, local/private address or unsafe redirect resolution; collection child URLs retain the stricter `ouo.io/<opaque>` rule. Never resolve or proxy short links.
- A known catalog code with a conflicting source-series relationship is an error, not an unmatched item. Unknown prefixes are not guessed or corrected.
- At most three transiently unavailable active child sources may be isolated per run; source identity changes, 401/403, malformed CSV/HTML, main-sheet/directory/official failures remain fatal.
- More than 100 newly unmatched codes in one run blocks publication. A publication failure restores every prior artifact.
- Keep source URLs and raw link values out of public status/error text, DOM `data-*`, page URL, console logs and exception messages. All public links use `target="_blank" rel="noopener noreferrer"`.
- Preserve 44px touch targets, keyboard/focus behavior and no horizontal overflow at 320, 390, 768, 1440 CSS pixels and 200% zoom.
- Work on `codex/unmatched-links-design` in the isolated checkout; never stage unrelated changes. Each task needs a RED test, GREEN test, safety/data/UI review and its own commit. No push/deploy before final verification.

## File map and contracts

| File | Responsibility |
| --- | --- |
| `src/giga_catalog/unmatched_links.py` | URL/code validation, source snapshot schema, deterministic public aggregate and surge comparison. |
| `src/giga_catalog/subtitles.py` | Expose safe unknown rows from active/archive collection CSV without changing existing official-link return values. |
| `scripts/refresh.py` | Collect main/child/archive unknown rows, isolate transient child failures, apply surge gate and atomic publication. |
| `public/data/unmatched-links.json` | Generated public list; no hand-edited rows. |
| `public/data/source-status.json` | Generated URL-free partial-source status. |
| `data/state/unmatched-link-sources.json` | Source-identity-keyed last-success snapshots; never served as a public asset. |
| `public/js/unmatched-links.js` | Strict client parser, same-origin lazy fetch, safe code search and DOM construction. |
| `public/js/app.js`, `public/index.html`, `public/css/style.css` | View switch, loading/retry states and responsive tab/list presentation. |
| `.github/workflows/refresh-catalog.yml` | Explicitly stage the two generated public files and the private state file. |

The new Python module exports `validate_public_link(value: str, *, collection: bool = False) -> str`, `load_source_snapshots(value: object) -> dict`, `build_unmatched_manifest(snapshots: dict, catalog_codes: set[str], generated_at: str) -> dict`, and `new_code_count(current: dict, previous: object) -> int`. Snapshot shape: `{"schemaVersion":1,"sources":[{"series":str,"sourceUrl":str,"lastSuccessAt":str,"links":{code:[url,...]}}]}`. Main sheet uses `series="MAIN"`; child/archive sheets use their canonical series. A source identity is the pair `(series, sourceUrl)`; replacing a source for the same series invalidates any old snapshot fallback. Public status shape: `{"schemaVersion":1,"generatedAt":str,"partial":bool,"unavailableSeries":[str,...]}`.

---

### Task 1: Strict unmatched data model

**Files:**
- Create: `src/giga_catalog/unmatched_links.py`
- Create: `tests/python/test_unmatched_links.py`

**Interfaces:** Produces the four module functions and exact schemas in the file map. Consumes existing `normalize_code` from `src/giga_catalog/codes.py` and `ipaddress`/`urllib.parse` from stdlib.

- [ ] **Step 1: Write failing tests** for `https://example.test/a` acceptance; `http:`, `javascript:`, credentials, fragment, localhost, `.local`, literal private/loopback/link-local IP, missing host and control characters rejection; `collection=True` accepting only existing `_collection_source_url` grammar; exact `code` normalization, URL dedup/sort, catalog-code removal, >100 new-code count and schema rejection. Include this executable core:

```python
class UnmatchedLinksTests(unittest.TestCase):
    def test_public_aggregate_deduplicates_and_excludes_known_codes(self):
        sources = {"schemaVersion": 1, "sources": [{
            "series": "MAIN", "sourceUrl": "https://docs.google.com/sheet.csv",
            "lastSuccessAt": "2026-09-17T00:00:00Z",
            "links": {"SPSF-1": ["https://example.com/known"],
                      "PMIF-109": ["https://example.com/b", "https://example.com/b"]},
        }]}
        result = build_unmatched_manifest(sources, {"SPSF-1"}, "2026-09-17T00:00:00Z")
        self.assertEqual(result["entries"], [{"code": "PMIF-109", "links": ["https://example.com/b"]}])
        self.assertEqual(new_code_count(result, {"entries": []}), 1)
```

- [ ] **Step 2: Run RED:** `py -m unittest tests.python.test_unmatched_links -v`; expect import failure for the new module.
- [ ] **Step 3: Implement** fail-closed validators and pure builders; use `urlsplit`, `ipaddress.ip_address`, IDNA-normalized hostname inspection and exact key/type checks. Reject control characters before parsing; reject apparent IP literals including bracketed IPv6 and private/reserved ranges; never follow URLs. Serialize sorted sources, codes and links. Reject duplicate source identities and duplicate codes within a source rather than silently overwriting them. In the `collection=True` path invoke the existing collection URL validator or the same exact host/path predicate, not a looser hostname suffix check.

```python
def new_code_count(current: dict, previous: object) -> int:
    old = {item["code"] for item in previous.get("entries", [])} if isinstance(previous, dict) else set()
    return len({item["code"] for item in current["entries"]} - old)
```

- [ ] **Step 4: Run GREEN:** `py -m unittest tests.python.test_unmatched_links -v`; then `py -m unittest tests.python.test_sheet tests.python.test_subtitles -v`.
- [ ] **Step 5: Review and commit:** check normalization parity, no URL in exception text, and `git diff --check`; `git add src/giga_catalog/unmatched_links.py tests/python/test_unmatched_links.py`; `git commit -m "feat: validate unmatched link snapshots"`.

### Task 2: Surface unknown child and archive rows without weakening known rows

**Files:**
- Modify: `src/giga_catalog/subtitles.py:665-818`
- Modify: `tests/python/test_subtitles.py`

**Interfaces:** Add keyword-only `unmatched_links: Optional[Dict[str, List[str]]] = None` to both `parse_collection_child_csv` and `parse_collection_archive_csv`. Preserve their existing return types. The output argument is updated only after the entire active sheet is valid; archive diagnostics retain existing row-scoped behavior. Remove `allow_known_pmid_typo` and the eight-code constant only when new tests prove the same rows now enter `unmatched_links`.

- [ ] **Step 1: Write failing tests:** a PMID CSV with `PMID-1,https://ouo.io/valid`, `PIMD-105,https://ouo.io/typo`, `PMIF-109,https://ouo.io/typo2` returns only the official link and exposes both unknown links. Add same-prefix unknown, blank/reupload exclusion, duplicate code, dangerous URL, and known wrong-series code rejection; assert malformed active sheet leaves `unmatched_links` unchanged.

```python
unknown = {}
known = parse_collection_child_csv(
    "PMID-1,https://ouo.io/valid\nPIMD-105,https://ouo.io/typo\n",
    series="PMID", catalog_codes={"PMID-1"}, unmatched_links=unknown,
)
self.assertEqual(known, {"PMID-1": "https://ouo.io/valid"})
self.assertEqual(unknown, {"PIMD-105": ["https://ouo.io/typo"]})
```

- [ ] **Step 2: Run RED:** `py -m unittest tests.python.test_subtitles -v`; expect unexpected `unmatched_links` argument.
- [ ] **Step 3: Implement** local `candidate_unknown` maps, assigning a validated unknown row only when `code not in catalog_codes`; if a *known* code has a different prefix from the sheet, raise `SubtitleFormatError`. Active sheet success may consist solely of valid unknown links, but a sheet with only blanks remains invalid. For archives preserve row diagnostics and invalidate a duplicate unknown code rather than keeping an arbitrary URL. Assign to the caller's output mapping after parsing succeeds.

```python
if code not in normalized_catalog_codes and not pending_reupload:
    candidate_unknown[code] = [validate_public_link(row[1].strip(), collection=True)]
if unmatched_links is not None:
    unmatched_links.update(candidate_unknown)
```

- [ ] **Step 4: Run GREEN:** `py -m unittest tests.python.test_subtitles -v`; specifically rerun old known-row and archive tests.
- [ ] **Step 5: Review and commit:** ensure known-link and pending semantics unchanged, no URL-bearing exception, `git diff --check`; stage only these two files; `git commit -m "feat: expose unmatched collection rows"`.

### Task 3: Main-sheet and source-failure decision helpers

**Files:**
- Modify: `scripts/refresh.py:102-310`
- Modify: `tests/python/test_refresh.py`

**Interfaces:** Consumes Task 1 validators/builders. Add internal helpers `_main_unmatched_links(sheet_links: Mapping[str, dict], catalog_codes: set[str]) -> Dict[str, List[str]]`, `_transient_child_failure(error: BaseException) -> bool`, and `_source_snapshot(snapshots: dict, series: str, url: str) -> Optional[dict]`. No public CLI changes. Task 4 uses these helpers and Task 2 `unmatched_links` arguments to wire the refresh pipeline.

- [ ] **Step 1: Write failing helper tests:** `_main_unmatched_links` flattens normal and uncensored provider URLs only for codes absent from the official set, validates each and preserves distinct URLs; `_transient_child_failure` accepts 404/408/429/5xx and timeout/connect but not 401/403; `_source_snapshot` returns only an exact `(series, url)` match. Add these to `tests/python/test_refresh.py` and import the helpers explicitly.

```python
response = requests.Response()
response.status_code = 404
error = requests.HTTPError("missing", response=response)
self.assertTrue(_transient_child_failure(error))
response.status_code = 403
self.assertFalse(_transient_child_failure(error))
```

- [ ] **Step 2: Run RED:** `py -m unittest tests.python.test_refresh -v`; expect missing helper imports.
- [ ] **Step 3: Implement** only the pure helpers in `scripts/refresh.py`. Flatten the provider mapping produced by `parse_sheet_csv`, excluding known codes and empty values, with `validate_public_link`. `_source_snapshot` returns the exact source record or `None` without modifying the snapshots. `_transient_child_failure` reads `requests.HTTPError.response.status_code` or recognizes `requests.Timeout`/`requests.ConnectionError`; it does not inspect exception message text. Do not yet change `run_refresh` or remove the temporary exceptions—that is Task 4.

```python
def _transient_child_failure(error: BaseException) -> bool:
    if isinstance(error, requests.HTTPError):
        status = getattr(error.response, "status_code", None)
        return status in {404, 408, 429} or isinstance(status, int) and 500 <= status <= 599
    return isinstance(error, (requests.Timeout, requests.ConnectionError))
```

- [ ] **Step 4: Run GREEN:** `py -m unittest tests.python.test_refresh -v`; rerun `tests.python.test_sheet` and `tests.python.test_subtitles`.
- [ ] **Step 5: Review and commit:** verify helper tests cover provider nesting and HTTP status boundaries, `git diff --check`; stage `scripts/refresh.py tests/python/test_refresh.py`; `git commit -m "feat: classify unmatched source inputs"`.

### Task 4: Surge gate and atomic publication of all three new artifacts

**Files:**
- Modify: `scripts/refresh.py:450-505,724-774,1149-1208`
- Modify: `tests/python/test_refresh.py`
- Modify: `.github/workflows/refresh-catalog.yml`
- Create (generated): `public/data/unmatched-links.json`, `public/data/source-status.json`, `data/state/unmatched-link-sources.json`
- Modify: `tests/python/test_release.py`

**Interfaces:** `run_refresh` passes validated `unmatched_manifest`, `source_status` and snapshots to `_publish` as additional explicit parameters. `_publish` adds all three serialized files to `_commit_transaction`. Do not add URLs to `source-status.json` or `data/update-summary.json` diagnostics.

- [ ] **Step 1: Write failing tests:** known PMID plus all eight exact wrong-prefix rows produces eight public unknown codes; a 404 child with prior same-identity snapshot retains its unknown row while another child updates; first-time 404 creates no item; recovery replaces stale rows; changed URL then 404 fails; four transient active failures fail; 401/403, HTML, malformed CSV, main-sheet and directory failures remain fatal. Unknown-only delta sets `result["changed"]` with unchanged catalog bytes; 101 new codes raises before writes while 100 passes; exact-code migration removes unknown; forced failure at each new transaction destination restores byte-for-byte pre-run tree; `source-status.json` contains series codes but no URLs; path overlap checks include new files. Test release manifest includes both public artifacts.

```python
replacement_targets = [
    "public/data/catalog.json", "public/data/unmatched-links.json",
    "public/data/source-status.json", "private/state/unmatched-link-sources.json",
    "private/raw/products.json", "private/raw/sheet.csv",
    "private/state/scrape-state.json", "private/update-summary.json",
]
# Extend the existing test_transaction_rolls_back_stale_cleanup_and_every_replacement
# loop with these exact destinations, including the three new destinations.
```

- [ ] **Step 2: Run RED:** `py -m unittest tests.python.test_refresh tests.python.test_release -v`; expect missing files/rollback coverage.
- [ ] **Step 3: Implement:** load/validate snapshots before downloads; replace each successfully read source's `(series, exact source URL)` snapshot, and preserve it during eligible outages. Generalize active-child isolation to at most three failures while rejecting changed identity, auth and parse errors; record URL-free `unavailableSeries`; preserve optional archive tolerance but mark it partial. Remove GHVR/PMID temporary exceptions. Use Task 3 main-sheet helper after official code discovery. Compare effective unmatched `entries` and URL-free `partial/unavailableSeries` against prior public files, excluding `generatedAt`; apply `new_code_count > 100` before `_publish`. Serialize the manifest, status and snapshots with `_json_bytes` in the existing transaction; add paths to `_validate_options` and explicit workflow staging. Redact raw request exception messages from user-facing/private summary diagnostics. Keep release manifest generation in the existing public-tree walker.

```python
operations = [
    (catalog_path, candidate_bytes),
    (catalog_path.with_name("unmatched-links.json"), _json_bytes(unmatched_manifest)),
    (catalog_path.with_name("source-status.json"), _json_bytes(source_status)),
    (data_root / "state" / "unmatched-link-sources.json", _json_bytes(snapshots)),
    (stale_public_summary, None),
    *private_outputs.items(),
]
```

- [ ] **Step 4: Run GREEN:** `py -m unittest tests.python.test_refresh tests.python.test_release -v`; run `py scripts/refresh.py --mode links-only --dry-run` against live sources without writing, then compare the selected official catalog item count with the previous file.
- [ ] **Step 5: Review and commit:** inspect generated files for unexpected fields/secrets, check rollback and workflow stage list, `git diff --check`; stage only listed files and generated artifacts; `git commit -m "feat: atomically publish unmatched link index"`.

### Task 5: Lazy, safe browser view

**Files:**
- Create: `public/js/unmatched-links.js`
- Create: `tests/js/unmatched-links.test.mjs`
- Modify: `public/index.html:131-179`
- Modify: `public/js/app.js:925-1020,1736-1775,2640-2690`
- Modify: `public/css/style.css:462-510,1623-1755`
- Modify: `tests/js/app.test.mjs`

**Interfaces:** Export `parseUnmatchedLinks(value: unknown)`, `parseSourceStatus(value: unknown)`, `fetchUnmatchedLinks(fetchImpl = globalThis.fetch)`, `fetchSourceStatus(fetchImpl = globalThis.fetch)`, `filterUnmatchedLinks(entries, query: string)` and `renderUnmatchedLinks(container: Element, entries, query: string)` from the new ES module. `app.js` dynamically imports it only on first tab activation; the module's fetch URL is `new URL("../data/unmatched-links.json", import.meta.url)` and status URL is `new URL("../data/source-status.json", import.meta.url)`.

- [ ] **Step 1: Write failing Node tests:** strict exact-key parsing, HTTPS/public-host and code validation, unsafe/duplicate rejection, escaped code search, no HTML interpretation, href only on `<a>`, `_blank` plus rel, no URL in `data-*`, lazy import absent from startup modulepreloads and CSS 44px control rule. Verify a rejected manifest produces a generic error without raw URL.

```javascript
const result = parseUnmatchedLinks({schemaVersion: 1, generatedAt: "2026-09-17T00:00:00Z",
  entries: [{code: "PMIF-109", links: ["https://example.com/watch"]}]});
assert.equal(filterUnmatchedLinks(result.entries, "pmif").length, 1);
assert.throws(() => parseUnmatchedLinks({schemaVersion: 1, generatedAt: result.generatedAt,
  entries: [{code: "PMIF-109", links: ["javascript:alert(1)"]}]}), TypeError);
```

- [ ] **Step 2: Run RED:** `node --test tests/js/unmatched-links.test.mjs tests/js/app.test.mjs`; expect missing module/tab.
- [ ] **Step 3: Implement:** add the fifth tab and a dedicated view branch before global-search routing; keep a separate `state.unmatchedQuery` so its code search does not trigger the official catalog search. On first activation, dynamically import/fetch, show bounded loading state, and render code-only cards; on failure show generic text and a retry button scoped to this view. Build nodes with `textContent` and `setAttribute`, never `innerHTML`, and keep URLs only in `<a href>`. Render status from `source-status.json` as URL-free “部分来源未更新” only after its schema is validated; failure to fetch status must not block entries. Limit initial cards and add a “加载更多” control following existing progressive counters. CSS must wrap tabs and links, maintain 44px targets, visible focus and reduced-motion behavior without hiding overflow.

```javascript
const anchor = document.createElement("a");
anchor.href = link;
anchor.target = "_blank";
anchor.rel = "noopener noreferrer";
anchor.textContent = `打开链接 ${index + 1}`;
```

- [ ] **Step 4: Run GREEN:** `node --test tests/js/unmatched-links.test.mjs tests/js/app.test.mjs`; `node --check public/js/unmatched-links.js`; `node --check public/js/app.js`.
- [ ] **Step 5: Review and commit:** inspect DOM for private URL placement, test keyboard tab switching and link focus, `git diff --check`; stage only these six files; `git commit -m "feat: add lazy unmatched video view"`.

### Task 6: End-to-end regression, browser and release gate

**Files:**
- Modify: `tests/python/test_refresh.py`, `tests/js/unmatched-links.test.mjs` only if a discovered regression needs a test; then fix the minimal owning file.
- Create: `docs/superpowers/reports/2026-09-17-giga-unmatched-video-links-release.md`

**Interfaces:** No new production interface. Deliver a reproducible report with branch/commit IDs, exact test totals, artifact counts, source freshness warnings, browser widths and deployment boundary.

- [ ] **Step 1: Add a failing end-to-end regression** for any gap found during read-only sampling (especially all eight PMID wrong-prefix rows and GHVR outage), then run that focused test RED. Use source fixtures in tests; do not depend on live Google availability for the regression suite.
- [ ] **Step 2: Fix the smallest owning parser/pipeline/UI function, run the focused test GREEN**, and commit that code/test separately if needed. If no gap exists, record “no additional regression required” with evidence rather than creating an artificial failure.
- [ ] **Step 3: Run full gates:** `py -m unittest discover -s tests/python -p "test_*.py" -v`; `node --test (rg --files tests/js -g '*.test.mjs')`; `git diff --check`; after the final code commit run `$releaseCommit = (git rev-parse HEAD).Trim(); py scripts/release.py prepare --source-commit $releaseCommit`, followed by `py -c "from pathlib import Path; from src.giga_catalog.release import parse_manifest, validate_local_release; root=Path('.'); manifest=parse_manifest((root/'public/giga-release.json').read_bytes()); validate_local_release(manifest, root/'public', root/'netlify.toml'); print('local release valid')"`. Check workflow `git add` includes all artifacts and source state is not under `public/`.
- [ ] **Step 4: Serve `public/` over HTTP and use a real Playwright browser** to check 320, 390, 768, 1440 and 200% zoom: no document horizontal overflow, searchable unknown codes, no fake metadata or player, `_blank` link rel, retry state, keyboard focus, console/network errors. Also confirm the official catalog still renders and startup does not request `unmatched-links.json` until activation. Do not use `file://` for the browser gate.
- [ ] **Step 5: Record release evidence and commit the report:** include exact branch/HEAD, full test counts, official catalog count before/after, unknown count, partial-source flags, manifest hash, local browser measurements and whether live production was actually deployed. `git add docs/superpowers/reports/2026-09-17-giga-unmatched-video-links-release.md`; `git commit -m "docs: record unmatched links release evidence"`. Do not claim production deployed from local verification alone; request explicit production action at the handoff if not already authorized.

## Self-review checklist

- [ ] Spec sections 1–2: separate public view/data; no official-catalog pollution (Tasks 1, 4, 5).
- [ ] Section 3: all four row classes, multi-link dedup, no player/redirect resolution (Tasks 1–3, 5).
- [ ] Section 4: exact-identity snapshot, transient cap, source freshness, fail-closed inputs (Tasks 3–4).
- [ ] Section 5: deterministic artifacts, migration, surge, atomic rollback, workflow stage, release manifest (Tasks 1, 4, 6).
- [ ] Section 6: lazy mobile/desktop UI, pure external link handling, accessibility (Task 5–6).
- [ ] Section 7: live sampling, full Python/Node, browser, release and post-deployment boundary (Task 6).
- [ ] Search this plan for incomplete steps, undefined helper names or mismatched signatures; resolve before implementation.
