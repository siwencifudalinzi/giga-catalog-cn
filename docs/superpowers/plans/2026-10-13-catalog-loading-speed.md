# Catalog loading speed implementation plan

> Execute inline with superpowers:executing-plans; obtain one independent whole-branch review before release.

Goal: Complete the first optimization round approved in chat: fast catalog lookups, less unsolicited detail data, first-preview priority, viewer preparation on detail intent, and reuse of loaded covers.

Spec: The measured performance analysis and first-round priorities approved on 2026-10-13. Evidence is retained in ignored `output/playwright/performance-report-2026-10-13.md` and associated JSON/scripts.

Architecture: Keep the current static ES-module application and V3 data schema. Cache immutable derived catalog records and normalized search rows within each store; invalidate derived state when search, series or tags change. Coordinate preview and deferred detail work with an abortable per-opening lifecycle. Warm viewer code after detail intent, without constructing overlays. Resolve links after the first preview or explicit link intent. Reuse validated local covers and already loaded image sources.

Constraints:
- No new frontend dependencies or data contract changes in this round.
- Keep search ordering, code normalization, tag aliases, include/exclude filters, favorites and full-series detail precedence correct.
- Tagged records and arrays remain immutable; failed installs must not discard the last working store.
- Closing or changing detail cancels pending image scheduling and obsolete UI callbacks.
- First image is high priority; subsequent images low priority and bounded on slow/save-data connections.
- Viewer warming must not create dialogs or history entries. Failed warming must permit an explicit retry.
- Labels and link destinations remain usable while optional data loads; tag failure provides retry.
- Preserve mobile/desktop layouts, image-error recovery, scroll/focus and nested Back/Forward.

Review focus: stale derived state after tag/series replacement; delayed callbacks after rapid close/reopen; warmed module failures; cached first-image synchronous completion; missing/no-preview records and link upgrades without preview success.

## Task 1: Runtime query indexes

- [x] RED tests for stable immutable record identity, tag-install invalidation, series precedence and failed-install atomicity.
- [x] Cache tagged records per source object, direct full-detail lookups, sorted search rows and normalized search text; avoid full-map cloning for getVideo/favorites.
- [x] GREEN focused and full JavaScript suites; compare CPU benchmarks using equivalent inputs.

## Task 2: Detail scheduling and reuse

- [x] RED tests for first-preview settlement/timeout/abort, viewer warming coalescing/retry, slow-network budgets, and cover selection/fallback.
- [x] Add preview loading helpers; load tags on metadata expansion and add loading/retry feedback.
- [x] Request first preview before subsequent images; defer optional link upgrades until first-preview settlement or link intent.
- [x] Warm viewer imports after detail intent; skip redundant recent-series prefetch and reuse local/loaded covers.
- [x] GREEN JavaScript and real-browser checks for cached images, errors, metadata retry and close/reopen.

## Task 3: Verification and release

- [x] Run full Python/JavaScript suites and mobile browser flow checks.
- [x] Repeat the network/CPU measurements on the candidate; report actual first-image/viewer/lookup results and limitations.
- [x] Independent review; fix important findings with regressions.
- [ ] Commit, synchronize latest main, publish and verify deployment/live release and gallery flow.

Execution record:
- Existing isolated worktree reused; base current main `3f58620`. Main workspace and the other checkout's rebase are untouched.
- Publishing the existing GitHub website is authorized by this conversation.
- Scope is the five first-round changes recommended immediately before approval. New image storage, content-hash schema migration and lean search artifacts are follow-on work, not prerequisites for these measured improvements.
- Use native PowerShell and this execution record for verification instead of the skills' Bash-only ledger scripts.
- Task 1: RED identity and removed-series-record regressions observed; GREEN 179 JavaScript tests. Controlled browser CPU 4x benchmark: lookup 42 ms -> below clock resolution, 24 favorites 990.7 -> 0.4 ms, SPSF search 169.8 -> 2.6 ms. Representative Chinese/Japanese tag queries retain identical results. One-time tag installation increases 77.6 -> 216.8 ms to build reusable indexes.
- Task 2: RED helper tests observed before implementation; 9 helper tests green, full JavaScript 188/188. Browser scheduling checks 17/17, including delayed first image, stale-close cancellation, cached images, low-bandwidth budget, tag retry, and separately failed viewer/state warm imports. Mobile flow 24/24 covers swipe/pinch/zoom, Back/Forward, focus/scroll, narrow and desktop widths; fault recovery 8/8.
- Browser regression during implementation: recent prefetch used a nonexistent store property; corrected to getRecentVideos after the smoke test exposed it. The same smoke test now reports zero exceptions.
- Browser module failure is cached per URL: both viewer and state imports now receive a fresh URL only after failure, with state injected into the viewer to avoid a cached failed static dependency. RED attempt-counter test and browser failure/retry check verified this path.
- Task 2 Ruling: stage the next preview before later thumbnails and automatic link upgrades — first paired measurement showed these competing downloads prolonged the immediate next-picture wait — cost: automatic link upgrades may wait up to one extra six-second image deadline; existing links and explicit link intent remain usable immediately. RED sequential-follow-up test and browser checks pass.
- Task 3 measurements: final paired weak-network sample first preview 4,703 -> 2,191 ms, first viewer 396 -> 48 ms. Immediate uncached next picture is 1,282 ms versus the old already cached 37 ms; the report records this tradeoff and the need for smaller preview assets rather than claiming every interaction is faster.
- First full Python run: one unrelated 120 ms deadline test finished at 125 ms during parallel browser CPU measurement; the isolated timing test passed. Re-running the full suite with browser measurements finished before release.
- Final full validation: Python unittest discovery ran 382 tests, OK with one existing skip; node --test ran 188 tests, all passing; browser checks 49/49. Windows npm's script subprocess cannot locate its PowerShell-only npm shim, so the two underlying full suites were run directly; no test was omitted.
- Independent review: one fresh read-only reviewer checked 3f58620..78ec0a2 and independently ran 26 focused tests. No Critical or Minor findings; two Important DOM issues reproduced.
- Final: fixed observer bypass of next-preview staging — wide 1440 px save-data regression RED (image 3 requested while image 2 blocked) -> GREEN (no later requests until image 2 settles); observer callbacks also retain the opening's abort signal.
- Final: fixed watching-link focus loss — focus-intent regression RED (section replacement discards the anchor) -> GREEN (anchor remains connected and focused while resolved destinations update in place).
- Final Ruling: preserve raw tag-filter insertion order as well as sorted text-search output — the reviewer found no current UI regression because the tag view re-sorts results, but keeping existing store semantics avoids an unnecessary compatibility change — cost: one small temporary pointer-array copy while building search rows. RED shuffled-payload test -> GREEN; full JavaScript suite 189/189.
- Final Ruling: responsive derivatives and cache/schema redesign remain outside the approved first round — the reviewer confirmed the documented uncached next-image tradeoff is not a release blocker — cost: source-image bandwidth and cache-generation churn remain until those later changes.
- Final targeted review regressions: 4/4 passing, no browser exceptions. No second reviewer dispatched; fixes are checked by reproductions and the full JavaScript suite.
